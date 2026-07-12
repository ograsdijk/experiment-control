import { useEffect, useMemo, useRef, useState } from "react";

import { buildWsUrl, fetchStreamWorkspaceSnapshot } from "../../api";
import { normalizeStreamAnalysisOutputMessage } from "../stream/messages";
import { decimateTraceValues } from "../stream/utils";
import type {
  StreamAnalysisWorkspaceSubscription,
  StreamTraceAverageMode,
  StreamTraceDecimator,
} from "../stream/types";
import type { StreamAnalysisMessage } from "../../types";
import {
  isStreamAnalysisRefreshOutputRequested,
  normalizeStreamAnalysisRefreshRequests,
  STREAM_ANALYSIS_HYDRATION_INVALIDATE_EVENT,
  streamAnalysisHydrationInvalidationRequests,
  type StreamAnalysisRefreshRequest,
} from "./streamAnalysisHydration";

/**
 * Stream-analysis WebSocket subscriptions manager.
 *
 * Mirrors `useRawStreamSubscriptions` but for the per-workspace
 * stream_analysis output stream:
 *
 * 1. **Snapshot hydration** — when the active subscription set
 *    changes (and the stream_analysis RPC is ready), fetch a
 *    workspace snapshot for each new subscription group and feed
 *    each contained output through `applyOutput`. Hydration is
 *    keyed on (workspaceId, kinds, maxTracePoints) so distinct
 *    subscriptions to the same workspace with different kinds get
 *    fresh snapshots.
 *
 * 2. **Live WS** — for each active subscription, open a
 *    `/ws/stream/{workspaceId}` WebSocket and feed each incoming
 *    output through `applyOutput` (with a trace-decimation filter
 *    pulled from the subscription metadata when applicable).
 *
 * `bumpPlotTick` fires after batches that produce updates.
 * `wsConnected` reports whether *any* socket is currently open,
 * falling back to `streamAnalysisRpcReady` when there are no
 * subscriptions (no sockets to maintain means the RPC link is the
 * only signal).
 */

export interface StreamAnalysisTraceFilter {
  traceDecimator: StreamTraceDecimator;
  traceMaxPoints: number;
  traceMaxFps: number;
  traceRollingWindow: number;
  traceAverageMode: StreamTraceAverageMode;
}

export interface StreamAnalysisSubscriptionsArgs {
  activeSubscriptions: StreamAnalysisWorkspaceSubscription[];
  streamAnalysisRpcReady: boolean;
  applyOutput: (
    output: NonNullable<ReturnType<typeof normalizeStreamAnalysisOutputMessage>>,
    traceFilter?: StreamAnalysisTraceFilter
  ) => boolean;
  bumpPlotTick: () => void;
}

function snapshotKey(target: {
  workspaceId: string;
  kinds: string[];
  maxTracePoints: number | undefined;
}): string {
  return `${target.workspaceId}|${target.kinds.join(",")}|${
    typeof target.maxTracePoints === "number" ? String(target.maxTracePoints) : ""
  }`;
}

function workspaceSubscriptionKey(
  subscription: StreamAnalysisWorkspaceSubscription
): string {
  const kinds = [...subscription.kinds].map(String).sort().join(",");
  const traceParts = [
    subscription.traceDecimator ?? "",
    subscription.traceMaxPoints ?? "",
    typeof subscription.traceMaxFps === "number"
      ? subscription.traceMaxFps.toFixed(3)
      : "",
    subscription.traceRollingWindow ?? "",
    subscription.traceAverageMode ?? "",
  ].join("|");
  return `${subscription.workspaceId}|${kinds}|${traceParts}`;
}

function workspaceSocketGroupKey(
  subscription: StreamAnalysisWorkspaceSubscription
): string {
  const traceParts = [
    subscription.traceDecimator ?? "",
    typeof subscription.traceMaxFps === "number"
      ? subscription.traceMaxFps.toFixed(3)
      : "",
    subscription.traceRollingWindow ?? "",
    subscription.traceAverageMode ?? "",
  ].join("|");
  return `${subscription.workspaceId}|${traceParts}`;
}

function binaryValuesFromOutput(
  output: NonNullable<ReturnType<typeof normalizeStreamAnalysisOutputMessage>>,
  data: ArrayBuffer
): Float64Array | null {
  if (output.byteLength !== null && output.byteLength !== data.byteLength) {
    return null;
  }
  if (data.byteLength % Float64Array.BYTES_PER_ELEMENT !== 0) {
    return null;
  }
  return new Float64Array(data);
}

function buildTraceFilter(
  subscription: StreamAnalysisWorkspaceSubscription
): StreamAnalysisTraceFilter | undefined {
  if (
    subscription.kinds.includes("trace") &&
    subscription.traceDecimator !== undefined &&
    subscription.traceMaxPoints !== undefined &&
    subscription.traceMaxFps !== undefined &&
    subscription.traceRollingWindow !== undefined &&
    subscription.traceAverageMode !== undefined
  ) {
    return {
      traceDecimator: subscription.traceDecimator,
      traceMaxPoints: subscription.traceMaxPoints,
      traceMaxFps: subscription.traceMaxFps,
      traceRollingWindow: subscription.traceRollingWindow,
      traceAverageMode: subscription.traceAverageMode,
    };
  }
  return undefined;
}

export function useStreamAnalysisSubscriptions({
  activeSubscriptions,
  streamAnalysisRpcReady,
  applyOutput,
  bumpPlotTick,
}: StreamAnalysisSubscriptionsArgs): { wsConnected: boolean } {
  const [wsConnected, setWsConnected] = useState(false);
  const [refreshGeneration, setRefreshGeneration] = useState(0);
  const hydratedRef = useRef<Set<string>>(new Set());
  const pendingRefreshRequestsRef = useRef<StreamAnalysisRefreshRequest[]>([]);
  const refreshMountedRef = useRef(true);

  const applyOutputRef = useRef(applyOutput);
  applyOutputRef.current = applyOutput;
  const bumpPlotTickRef = useRef(bumpPlotTick);
  bumpPlotTickRef.current = bumpPlotTick;

  useEffect(() => {
    return () => {
      refreshMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    const handleInvalidate = (event: Event) => {
      const requests = streamAnalysisHydrationInvalidationRequests(event);
      if (requests.length <= 0) {
        return;
      }
      pendingRefreshRequestsRef.current = normalizeStreamAnalysisRefreshRequests([
        ...pendingRefreshRequestsRef.current,
        ...requests,
      ]);
      setRefreshGeneration((value) => value + 1);
    };
    window.addEventListener(
      STREAM_ANALYSIS_HYDRATION_INVALIDATE_EVENT,
      handleInvalidate
    );
    return () => {
      window.removeEventListener(
        STREAM_ANALYSIS_HYDRATION_INVALIDATE_EVENT,
        handleInvalidate
      );
    };
  }, []);

  // Stable string key derived from the subscription set. The caller
  // re-allocates `activeSubscriptions` on every panels-state mutation
  // (title edits, smoothing-window changes, ...), so depending on the
  // array reference directly would tear down and re-open every
  // workspace WebSocket on each unrelated UI edit. The sorted-key
  // string only changes when the actual subscription set changes,
  // which keeps the live sockets in place across cosmetic panel edits.
  const subscriptionsKey = useMemo(
    () =>
      activeSubscriptions
        .map(workspaceSubscriptionKey)
        .sort()
        .join(";"),
    [activeSubscriptions]
  );
  // Mirror the current subscription list into a ref so effects keyed
  // on `subscriptionsKey` can read it without taking the array
  // reference itself as a dep.
  const activeSubscriptionsRef = useRef(activeSubscriptions);
  activeSubscriptionsRef.current = activeSubscriptions;

  // Snapshot hydration on subscription-set / rpcReady change.
  useEffect(() => {
    const currentSubscriptions = activeSubscriptionsRef.current;
    if (!streamAnalysisRpcReady || currentSubscriptions.length <= 0) {
      return;
    }
    let cancelled = false;
    const kindsByWorkspace = new Map<string, Set<string>>();
    const traceMaxPointsByWorkspace = new Map<string, number>();
    for (const subscription of currentSubscriptions) {
      const workspaceId = String(subscription.workspaceId ?? "").trim();
      if (!workspaceId) {
        continue;
      }
      const kinds = kindsByWorkspace.get(workspaceId) ?? new Set<string>();
      for (const kind of subscription.kinds) {
        kinds.add(String(kind));
      }
      kindsByWorkspace.set(workspaceId, kinds);
      if (
        subscription.kinds.includes("trace") &&
        typeof subscription.traceMaxPoints === "number" &&
        Number.isFinite(subscription.traceMaxPoints)
      ) {
        const current = traceMaxPointsByWorkspace.get(workspaceId) ?? 0;
        traceMaxPointsByWorkspace.set(
          workspaceId,
          Math.max(current, Math.max(32, Math.trunc(subscription.traceMaxPoints)))
        );
      }
    }
    const snapshotTargets = [...kindsByWorkspace.entries()].map(
      ([workspaceId, kindsSet]) => {
        const kinds = [...kindsSet].sort();
        const maxTracePoints = traceMaxPointsByWorkspace.get(workspaceId);
        return {
          workspaceId,
          kinds,
          maxTracePoints,
          key: snapshotKey({ workspaceId, kinds, maxTracePoints }),
        };
      }
    );
    const activeKeys = new Set(snapshotTargets.map((entry) => entry.key));
    hydratedRef.current = new Set(
      [...hydratedRef.current].filter((key) => activeKeys.has(key))
    );
    const pending = snapshotTargets.filter(
      (entry) => !hydratedRef.current.has(entry.key)
    );
    if (pending.length <= 0) {
      return;
    }
    const load = async () => {
      let updated = false;
      for (const target of pending) {
        try {
          const resp = await fetchStreamWorkspaceSnapshot(target.workspaceId, {
            kinds: target.kinds,
            maxTracePoints: target.maxTracePoints ?? null,
          });
          if (cancelled) {
            return;
          }
          hydratedRef.current.add(target.key);
          if (!resp.ok || !resp.result || typeof resp.result !== "object") {
            continue;
          }
          const outputsRaw = Array.isArray(resp.result.outputs)
            ? resp.result.outputs
            : [];
          for (const outputRaw of outputsRaw) {
            if (!outputRaw || typeof outputRaw !== "object") {
              continue;
            }
            const normalized = normalizeStreamAnalysisOutputMessage({
              topic: "manager.stream_analysis.output",
              payload: outputRaw as StreamAnalysisMessage["payload"],
            });
            if (normalized === null) {
              continue;
            }
            if (applyOutputRef.current(normalized)) {
              updated = true;
            }
          }
        } catch {
          hydratedRef.current.add(target.key);
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
    // subscriptionsKey only ticks on real set changes; depending on
    // the raw array reference would re-hydrate on unrelated edits.
  }, [streamAnalysisRpcReady, subscriptionsKey]);

  // Panel source changes refresh only the newly selected outputs. This
  // intentionally leaves normal hydration keys and live sockets untouched.
  useEffect(() => {
    if (!streamAnalysisRpcReady) return;
    const requests = pendingRefreshRequestsRef.current;
    pendingRefreshRequestsRef.current = [];
    if (requests.length === 0) return;
    const load = async () => {
      let updated = false;
      for (const request of requests) {
        try {
          const resp = await fetchStreamWorkspaceSnapshot(request.workspaceId, {
            outputIds: request.outputIds,
            maxTracePoints: request.maxTracePoints ?? null,
          });
          if (!refreshMountedRef.current) return;
          if (!resp.ok || !resp.result || typeof resp.result !== "object") {
            continue;
          }
          const outputs = Array.isArray(resp.result.outputs)
            ? resp.result.outputs
            : [];
          for (const outputRaw of outputs) {
            if (!outputRaw || typeof outputRaw !== "object") continue;
            const normalized = normalizeStreamAnalysisOutputMessage({
              topic: "manager.stream_analysis.output",
              payload: outputRaw as StreamAnalysisMessage["payload"],
            });
            if (
              normalized &&
              isStreamAnalysisRefreshOutputRequested(
                request,
                normalized.workspaceId,
                normalized.outputId
              ) &&
              applyOutputRef.current(normalized)
            ) {
              updated = true;
            }
          }
        } catch {
          continue;
        }
      }
      if (refreshMountedRef.current && updated) bumpPlotTickRef.current();
    };
    void load();
  }, [streamAnalysisRpcReady, refreshGeneration]);

  // Live WS for each active subscription.
  useEffect(() => {
    const currentSubscriptions = activeSubscriptionsRef.current;
    if (currentSubscriptions.length <= 0) {
      setWsConnected(streamAnalysisRpcReady);
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

    const subscriptionsByKey = new Map<string, StreamAnalysisWorkspaceSubscription[]>();
    for (const subscription of currentSubscriptions) {
      const socketKey = workspaceSocketGroupKey(subscription);
      const peers = subscriptionsByKey.get(socketKey);
      if (peers) {
        peers.push(subscription);
      } else {
        subscriptionsByKey.set(socketKey, [subscription]);
      }
    }

    const applyOutputToSubscriptions = (
      subscriptions: StreamAnalysisWorkspaceSubscription[],
      output: NonNullable<ReturnType<typeof normalizeStreamAnalysisOutputMessage>>
    ) => {
      let updated = false;
      for (const subscription of subscriptions) {
        const traceFilter = buildTraceFilter(subscription);
        const outputForSubscription =
          output.kind === "trace" && traceFilter
            ? {
                ...output,
                value: decimateTraceValues(
                  output.value,
                  traceFilter.traceDecimator,
                  traceFilter.traceMaxPoints
                ),
              }
            : output;
        if (applyOutputRef.current(outputForSubscription, traceFilter)) {
          updated = true;
        }
      }
      if (updated) {
        bumpPlotTickRef.current();
      }
    };

    const onMessage = (subscriptions: StreamAnalysisWorkspaceSubscription[]) => {
      let pendingOutput: NonNullable<ReturnType<typeof normalizeStreamAnalysisOutputMessage>> | null = null;
      return (event: MessageEvent<string | ArrayBuffer>) => {
        try {
          if (typeof event.data === "string") {
            const msg = JSON.parse(event.data) as StreamAnalysisMessage;
            const output = normalizeStreamAnalysisOutputMessage(msg);
            if (output === null) {
              pendingOutput = null;
              return;
            }
            if (output.encoding === "binary-frame") {
              pendingOutput = output;
              return;
            }
            pendingOutput = null;
            applyOutputToSubscriptions(subscriptions, output);
            return;
          }
          if (pendingOutput === null) {
            return;
          }
          const values = binaryValuesFromOutput(pendingOutput, event.data);
          if (values === null) {
            pendingOutput = null;
            return;
          }
          const output = { ...pendingOutput, value: values };
          pendingOutput = null;
          applyOutputToSubscriptions(subscriptions, output);
        } catch {
          pendingOutput = null;
          return;
        }
      };
    };

    for (const [socketKey, subscriptions] of subscriptionsByKey) {
      const subscription = subscriptions[0];
      const workspaceId = subscription.workspaceId;
      const kinds = [...new Set(subscriptions.flatMap((entry) => entry.kinds))].sort();
      const params = new URLSearchParams();
      if (kinds.length > 0) {
        params.set("kinds", kinds.join(","));
      }
      const traceFilter = buildTraceFilter(subscription);
      if (traceFilter) {
        const traceMaxPoints = Math.max(
          ...subscriptions
            .map((entry) => buildTraceFilter(entry)?.traceMaxPoints)
            .filter((value): value is number => typeof value === "number")
        );
        params.set("trace_decimator", traceFilter.traceDecimator);
        params.set("trace_max_points", String(traceMaxPoints));
        params.set("trace_max_fps", String(traceFilter.traceMaxFps));
        params.set("rolling_window", String(traceFilter.traceRollingWindow));
        params.set("trace_average_mode", traceFilter.traceAverageMode);
        params.set("transport", "binary");
      }
      const query = params.toString();
      const ws = new WebSocket(
        buildWsUrl(
          `/ws/stream/${encodeURIComponent(workspaceId)}${query ? `?${query}` : ""}`
        )
      );
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
    // edits no longer tear down and reopen every workspace socket.
  }, [subscriptionsKey, streamAnalysisRpcReady]);

  return { wsConnected };
}
