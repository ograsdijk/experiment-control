import { useEffect, useRef, useState } from "react";

import { buildWsUrl, fetchRawStreamSnapshot } from "../../api";
import { normalizeStreamFrameMessage } from "../stream/messages";
import type { RawStreamSubscription, StreamFrameMessage } from "../stream/types";

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

  // Snapshot hydration on subscription-set change.
  useEffect(() => {
    if (activeSubscriptions.length <= 0) {
      return;
    }
    let cancelled = false;
    const activeKeys = new Set(activeSubscriptions.map(subscriptionKey));
    hydratedRef.current = new Set(
      [...hydratedRef.current].filter((key) => activeKeys.has(key))
    );
    const pending = activeSubscriptions.filter(
      (subscription) => !hydratedRef.current.has(subscriptionKey(subscription))
    );
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
  }, [activeSubscriptions]);

  // Live WS for each active subscription.
  useEffect(() => {
    if (activeSubscriptions.length <= 0) {
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

    const onMessage =
      (subscription: RawStreamSubscription) => (event: MessageEvent<string>) => {
        try {
          const msg = JSON.parse(event.data) as StreamFrameMessage;
          const frame = normalizeStreamFrameMessage(msg);
          if (frame === null) {
            return;
          }
          if (
            frame.deviceId !== subscription.deviceId ||
            frame.stream !== subscription.stream
          ) {
            return;
          }
          const updated = applyFrameRef.current(subscription, frame);
          if (updated) {
            bumpPlotTickRef.current();
          }
        } catch {
          return;
        }
      };

    for (const subscription of activeSubscriptions) {
      const params = new URLSearchParams();
      params.set("device_id", subscription.deviceId);
      params.set("stream", subscription.stream);
      params.set("channel_index", String(subscription.channelIndex));
      params.set("trace_decimator", subscription.traceDecimator);
      params.set("trace_max_points", String(subscription.traceMaxPoints));
      params.set("trace_max_fps", String(subscription.traceMaxFps));
      params.set("rolling_window", String(subscription.rollingWindow));
      params.set("trace_average_mode", subscription.averageMode);
      const query = params.toString();
      const socketKey = subscriptionKey(subscription);
      const ws = new WebSocket(buildWsUrl(`/ws/raw_stream?${query}`));
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
      ws.onmessage = onMessage(subscription);
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
  }, [activeSubscriptions]);

  return { wsConnected };
}
