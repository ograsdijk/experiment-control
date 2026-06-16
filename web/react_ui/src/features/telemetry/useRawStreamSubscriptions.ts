import { useEffect, useMemo, useRef, useState } from "react";

import { buildWsUrl, fetchRawStreamSnapshot } from "../../api";
import { normalizeStreamFrameMessage } from "../stream/messages";
import { decimateTraceValues } from "../stream/utils";
import type { RawStreamSubscription } from "../stream/types";
import type { StreamFrameMessage } from "../../types";

/**
 * Raw-stream WebSocket subscriptions manager.
 *
 * Owns two effects per call:
 *
 * 1. **Snapshot hydration** — when the active subscription set
 *    changes, fetch a one-shot snapshot via `fetchRawStreamSnapshot`
 *    for each new subscription and feed it through `applyFrame`.
 *    Already-hydrated subscriptions are tracked in a ref so we don't
 *    re-fetch on every effect re-run.
 *
 * 2. **Live WS** — for each active subscription, open a
 *    `/ws/raw_stream` WebSocket and feed each incoming frame to
 *    `applyFrame`. Tracks open-socket count so the returned
 *    `wsConnected` flag reflects whether *any* socket is currently
 *    open.
 *
 * If `applyFrame` reports an update (returns true), the hook calls
 * `bumpPlotTick` so downstream plot panels re-render. When the
 * subscription list is empty, `wsConnected` is reported as true (no
 * sockets to maintain → no connection failure to surface).
 *
 * **Args**:
 *
 * - `activeSubscriptions` — the App-derived list of raw-stream
 *   subscriptions currently bound to panels.
 * - `applyFrame(subscription, frame) → updated` — call site's
 *   per-subscription frame consumer; returns whether any panel
 *   buffer changed.
 * - `bumpPlotTick()` — called after a batch of frames that produced
 *   updates so the UI redraws.
 */

export interface RawStreamSubscriptionsArgs {
  activeSubscriptions: RawStreamSubscription[];
  applyFrame: (
    subscription: RawStreamSubscription,
    frame: NonNullable<ReturnType<typeof normalizeStreamFrameMessage>>
  ) => boolean;
  bumpPlotTick: () => void;
}

function subscriptionKey(subscription: RawStreamSubscription): string {
  return `${subscription.deviceId}|${subscription.stream}|${subscription.channelIndex}|${subscription.traceDecimator}|${subscription.traceMaxPoints}|${subscription.traceMaxFps.toFixed(3)}|${subscription.rollingWindow}|${subscription.averageMode}`;
}

function socketGroupKey(subscription: RawStreamSubscription): string {
  return `${subscription.deviceId}|${subscription.stream}|${subscription.channelIndex}|${subscription.traceDecimator}|${subscription.traceMaxFps.toFixed(3)}|${subscription.rollingWindow}|${subscription.averageMode}`;
}

function binaryValuesFromFrame(
  frame: NonNullable<ReturnType<typeof normalizeStreamFrameMessage>>,
  data: ArrayBuffer
): Float64Array | null {
  const expectedBytes = Number((frame as { byteLength?: unknown }).byteLength);
  if (Number.isFinite(expectedBytes) && expectedBytes > 0 && expectedBytes !== data.byteLength) {
    return null;
  }
  if (data.byteLength % Float64Array.BYTES_PER_ELEMENT !== 0) {
    return null;
  }
  return new Float64Array(data);
}

export function useRawStreamSubscriptions({
  activeSubscriptions,
  applyFrame,
  bumpPlotTick,
}: RawStreamSubscriptionsArgs): { wsConnected: boolean } {
  const [wsConnected, setWsConnected] = useState(true);
  const hydratedRef = useRef<Set<string>>(new Set());

  // Stable refs to the callbacks so the effects below don't need
  // them in their dependency arrays.
  const applyFrameRef = useRef(applyFrame);
  applyFrameRef.current = applyFrame;
  const bumpPlotTickRef = useRef(bumpPlotTick);
  bumpPlotTickRef.current = bumpPlotTick;

  // Stable string key derived from the subscription set. The caller
  // re-allocates `activeSubscriptions` on every panels-state mutation
  // (title edits, smoothing-window changes, ...), so depending on the
  // array reference directly would tear down and re-open every
  // WebSocket on each unrelated UI edit. The sorted-key string only
  // changes when the actual subscription set changes, which keeps the
  // live sockets in place across cosmetic panel edits.
  const subscriptionsKey = useMemo(
    () =>
      activeSubscriptions
        .map(subscriptionKey)
        .sort()
        .join(";"),
    [activeSubscriptions]
  );
  // Mirror the current subscription list into a ref so effects keyed
  // on `subscriptionsKey` can read it without taking the array
  // reference itself as a dep.
  const activeSubscriptionsRef = useRef(activeSubscriptions);
  activeSubscriptionsRef.current = activeSubscriptions;

  // Snapshot hydration on subscription-set change.
  useEffect(() => {
    const currentSubscriptions = activeSubscriptionsRef.current;
    if (currentSubscriptions.length <= 0) {
      return;
    }
    let cancelled = false;
    const activeKeys = new Set(currentSubscriptions.map(subscriptionKey));
    hydratedRef.current = new Set(
      [...hydratedRef.current].filter((key) => activeKeys.has(key))
    );
    const seenPending = new Set<string>();
    const pending = currentSubscriptions.filter((subscription) => {
      const key = subscriptionKey(subscription);
      if (hydratedRef.current.has(key) || seenPending.has(key)) {
        return false;
      }
      seenPending.add(key);
      return true;
    });
    if (pending.length <= 0) {
      return;
    }
    const load = async () => {
      let updated = false;
      for (const subscription of pending) {
        const key = subscriptionKey(subscription);
        try {
          const msg = await fetchRawStreamSnapshot({
            deviceId: subscription.deviceId,
            stream: subscription.stream,
            channelIndex: subscription.channelIndex,
            traceDecimator: subscription.traceDecimator,
            traceMaxPoints: subscription.traceMaxPoints,
            traceMaxFps: subscription.traceMaxFps,
            rollingWindow: subscription.rollingWindow,
            averageMode: subscription.averageMode,
          });
          if (cancelled) {
            return;
          }
          hydratedRef.current.add(key);
          const frame = msg ? normalizeStreamFrameMessage(msg) : null;
          if (frame === null) {
            continue;
          }
          if (applyFrameRef.current(subscription, frame)) {
            updated = true;
          }
        } catch {
          hydratedRef.current.add(key);
        }
      }
      if (!cancelled && updated) {
        bumpPlotTickRef.current();
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
    // subscriptionsKey changes only when the actual subscription set
    // changes; this avoids re-hydrating on unrelated panel edits.
  }, [subscriptionsKey]);

  // Live WS for each active subscription.
  useEffect(() => {
    const currentSubscriptions = activeSubscriptionsRef.current;
    if (currentSubscriptions.length <= 0) {
      setWsConnected(true);
      return;
    }
    let disposed = false;
    const sockets = new Map<string, WebSocket>();
    const openIds = new Set<string>();

    const updateConnected = () => {
      if (disposed) {
        return;
      }
      setWsConnected(openIds.size > 0);
    };

    const subscriptionsByKey = new Map<string, RawStreamSubscription[]>();
    for (const subscription of currentSubscriptions) {
      const key = socketGroupKey(subscription);
      const peers = subscriptionsByKey.get(key);
      if (peers) {
        peers.push(subscription);
      } else {
        subscriptionsByKey.set(key, [subscription]);
      }
    }

    const applyFrameToSubscriptions = (
      subscriptions: RawStreamSubscription[],
      frame: NonNullable<ReturnType<typeof normalizeStreamFrameMessage>>
    ) => {
      let updated = false;
      for (const subscription of subscriptions) {
        if (
          frame.deviceId !== subscription.deviceId ||
          frame.stream !== subscription.stream
        ) {
          continue;
        }
        const values = decimateTraceValues(
          frame.values,
          subscription.traceDecimator,
          subscription.traceMaxPoints
        );
        const frameForSubscription = values
          ? { ...frame, values, shape: [values.length] }
          : frame;
        if (applyFrameRef.current(subscription, frameForSubscription)) {
          updated = true;
        }
      }
      if (updated) {
        bumpPlotTickRef.current();
      }
    };

    const onMessage = (subscriptions: RawStreamSubscription[]) => {
      let pendingFrame: NonNullable<ReturnType<typeof normalizeStreamFrameMessage>> | null = null;
      return (event: MessageEvent<string | ArrayBuffer>) => {
        try {
          if (typeof event.data === "string") {
            const msg = JSON.parse(event.data) as StreamFrameMessage;
            const frame = normalizeStreamFrameMessage(msg);
            if (frame === null) {
              pendingFrame = null;
              return;
            }
            if (frame.encoding === "binary-frame") {
              pendingFrame = frame;
              return;
            }
            pendingFrame = null;
            applyFrameToSubscriptions(subscriptions, frame);
            return;
          }
          if (pendingFrame === null) {
            return;
          }
          const values = binaryValuesFromFrame(pendingFrame, event.data);
          if (values === null) {
            pendingFrame = null;
            return;
          }
          const frame = { ...pendingFrame, values, shape: [values.length] };
          pendingFrame = null;
          applyFrameToSubscriptions(subscriptions, frame);
        } catch {
          pendingFrame = null;
          return;
        }
      };
    };

    for (const [socketKey, subscriptions] of subscriptionsByKey) {
      const subscription = subscriptions[0];
      const traceMaxPoints = Math.max(
        ...subscriptions.map((entry) => entry.traceMaxPoints)
      );
      const params = new URLSearchParams();
      params.set("device_id", subscription.deviceId);
      params.set("stream", subscription.stream);
      params.set("channel_index", String(subscription.channelIndex));
      params.set("trace_decimator", subscription.traceDecimator);
      params.set("trace_max_points", String(traceMaxPoints));
      params.set("trace_max_fps", String(subscription.traceMaxFps));
      params.set("rolling_window", String(subscription.rollingWindow));
      params.set("trace_average_mode", subscription.averageMode);
      params.set("transport", "binary");
      const query = params.toString();
      const ws = new WebSocket(buildWsUrl(`/ws/raw_stream?${query}`));
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        openIds.add(socketKey);
        updateConnected();
      };
      ws.onclose = () => {
        openIds.delete(socketKey);
        updateConnected();
      };
      ws.onerror = () => {
        openIds.delete(socketKey);
        updateConnected();
      };
      ws.onmessage = onMessage(subscriptions);
      sockets.set(socketKey, ws);
    }

    updateConnected();
    return () => {
      disposed = true;
      openIds.clear();
      setWsConnected(false);
      for (const ws of sockets.values()) {
        ws.close();
      }
      sockets.clear();
    };
    // subscriptionsKey only ticks on real set changes; unrelated panel
    // edits no longer tear down and reopen every socket.
  }, [subscriptionsKey]);

  return { wsConnected };
}
