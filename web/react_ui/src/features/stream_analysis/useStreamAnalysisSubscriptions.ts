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
  openReconnectingSocket,
  type ReconnectingSocket,
} from "../common/reconnectingSocket";
import { perfCount } from "../performance/perfInstrumentation";
import { markPanelsDirty } from "../panels/PanelInvalidationStore";
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
 * 2. **Live WS** — for each socket group, keep a
 *    `/ws/stream/{workspaceId}` WebSocket that reconnects with backoff
 *    and feeds each incoming output through `applyOutput` (with a
 *    trace-decimation filter pulled from the subscription metadata when
 *    applicable). Groups are diffed by key on subscription-set changes;
 *    a reconnect re-hydrates that workspace's snapshot.
 *
 * Exact changed panel IDs are coalesced after batches that produce updates.
 * `wsConnected` is true only while every expected socket is open,
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
  ) => ReadonlySet<string>;
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

type AnalysisOutput = NonNullable<
  ReturnType<typeof normalizeStreamAnalysisOutputMessage>
>;

type WorkspaceSocketGroup = {
  url: string;
  subscriptions: StreamAnalysisWorkspaceSubscription[];
  socket: ReconnectingSocket;
};

function workspaceSocketUrl(
  subscriptions: StreamAnalysisWorkspaceSubscription[]
): string {
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
  return buildWsUrl(
    `/ws/stream/${encodeURIComponent(workspaceId)}${query ? `?${query}` : ""}`
  );
}

function applyOutputToSubscriptions(
  subscriptions: StreamAnalysisWorkspaceSubscription[],
  output: AnalysisOutput,
  applyOutput: StreamAnalysisSubscriptionsArgs["applyOutput"]
) {
  perfCount("stream_analysis.messages");
  const dirtyPanelIds = new Set<string>();
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
    for (const panelId of applyOutput(outputForSubscription, traceFilter)) {
      dirtyPanelIds.add(panelId);
    }
  }
  markPanelsDirty(dirtyPanelIds);
}

export function useStreamAnalysisSubscriptions({
  activeSubscriptions,
  streamAnalysisRpcReady,
  applyOutput,
}: StreamAnalysisSubscriptionsArgs): { wsConnected: boolean } {
  const [wsConnected, setWsConnected] = useState(false);
  const [refreshGeneration, setRefreshGeneration] = useState(0);
  const [hydrationGeneration, setHydrationGeneration] = useState(0);
  const hydratedRef = useRef<Set<string>>(new Set());
  // Workspaces whose socket reconnected: their snapshot is fetched again.
  const invalidatedWorkspacesRef = useRef<Set<string>>(new Set());
  const pendingRefreshRequestsRef = useRef<StreamAnalysisRefreshRequest[]>([]);
  const refreshMountedRef = useRef(true);

  const applyOutputRef = useRef(applyOutput);
  applyOutputRef.current = applyOutput;

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
    for (const target of snapshotTargets) {
      if (invalidatedWorkspacesRef.current.has(target.workspaceId)) {
        hydratedRef.current.delete(target.key);
      }
    }
    invalidatedWorkspacesRef.current.clear();
    const pending = snapshotTargets.filter(
      (entry) => !hydratedRef.current.has(entry.key)
    );
    if (pending.length <= 0) {
      return;
    }
    const load = async () => {
      const dirtyPanelIds = new Set<string>();
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
            for (const panelId of applyOutputRef.current(normalized)) {
              dirtyPanelIds.add(panelId);
            }
          }
        } catch {
          hydratedRef.current.add(target.key);
        }
      }
      if (!cancelled) markPanelsDirty(dirtyPanelIds);
    };
    void load();
    return () => {
      cancelled = true;
    };
    // subscriptionsKey only ticks on real set changes; depending on
    // the raw array reference would re-hydrate on unrelated edits.
    // hydrationGeneration ticks when a socket reconnects.
  }, [streamAnalysisRpcReady, subscriptionsKey, hydrationGeneration]);

  // Panel source changes refresh only the newly selected outputs. This
  // intentionally leaves normal hydration keys and live sockets untouched.
  useEffect(() => {
    if (!streamAnalysisRpcReady) return;
    const requests = pendingRefreshRequestsRef.current;
    pendingRefreshRequestsRef.current = [];
    if (requests.length === 0) return;
    const load = async () => {
      const dirtyPanelIds = new Set<string>();
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
              )
            ) {
              for (const panelId of applyOutputRef.current(normalized)) {
                dirtyPanelIds.add(panelId);
              }
            }
          }
        } catch {
          continue;
        }
      }
      if (refreshMountedRef.current) markPanelsDirty(dirtyPanelIds);
    };
    void load();
  }, [streamAnalysisRpcReady, refreshGeneration]);

  // Live sockets persist across subscription-set changes: one per socket
  // group, diffed by group key so adding or removing a panel only opens or
  // closes the sockets whose group actually changed.
  const socketGroupsRef = useRef<Map<string, WorkspaceSocketGroup>>(new Map());
  const socketsRpcReadyRef = useRef(streamAnalysisRpcReady);
  const rpcReadyRef = useRef(streamAnalysisRpcReady);
  rpcReadyRef.current = streamAnalysisRpcReady;

  const updateConnected = () => {
    const groups = socketGroupsRef.current;
    // No sockets to maintain: the RPC link is the only signal. Otherwise
    // connected only when every expected socket is open.
    setWsConnected(
      groups.size === 0
        ? rpcReadyRef.current
        : [...groups.values()].every((group) => group.socket.isOpen)
    );
  };
  const updateConnectedRef = useRef(updateConnected);
  updateConnectedRef.current = updateConnected;

  useEffect(() => {
    const groups = socketGroupsRef.current;
    // An RPC readiness change (stream_analysis restarted) recycles every
    // socket, as the previous teardown-on-change effect did.
    if (socketsRpcReadyRef.current !== streamAnalysisRpcReady) {
      socketsRpcReadyRef.current = streamAnalysisRpcReady;
      for (const group of groups.values()) {
        group.socket.close();
      }
      groups.clear();
    }
    const subscriptionsByKey = new Map<string, StreamAnalysisWorkspaceSubscription[]>();
    for (const subscription of activeSubscriptionsRef.current) {
      const socketKey = workspaceSocketGroupKey(subscription);
      const peers = subscriptionsByKey.get(socketKey);
      if (peers) {
        peers.push(subscription);
      } else {
        subscriptionsByKey.set(socketKey, [subscription]);
      }
    }

    for (const [key, group] of groups) {
      const subscriptions = subscriptionsByKey.get(key);
      if (!subscriptions || workspaceSocketUrl(subscriptions) !== group.url) {
        group.socket.close();
        groups.delete(key);
      } else {
        // Same socket parameters: keep the socket, refresh its consumers.
        group.subscriptions = subscriptions;
      }
    }

    for (const [key, subscriptions] of subscriptionsByKey) {
      if (groups.has(key)) {
        continue;
      }
      const url = workspaceSocketUrl(subscriptions);
      const workspaceId = subscriptions[0].workspaceId;
      let pendingOutput: AnalysisOutput | null = null;
      const group: WorkspaceSocketGroup = {
        url,
        subscriptions,
        socket: openReconnectingSocket({
          url,
          binaryType: "arraybuffer",
          onOpen: ({ reconnect }) => {
            pendingOutput = null;
            if (reconnect) {
              // Outputs published while the socket was down are gone;
              // reload the workspace snapshot.
              invalidatedWorkspacesRef.current.add(workspaceId);
              setHydrationGeneration((value) => value + 1);
            }
            updateConnectedRef.current();
          },
          onClose: () => {
            pendingOutput = null;
            updateConnectedRef.current();
          },
          onMessage: (event: MessageEvent<string | ArrayBuffer>) => {
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
                applyOutputToSubscriptions(group.subscriptions, output, applyOutputRef.current);
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
              applyOutputToSubscriptions(group.subscriptions, output, applyOutputRef.current);
            } catch {
              pendingOutput = null;
            }
          },
        }),
      };
      groups.set(key, group);
    }
    updateConnectedRef.current();
    // subscriptionsKey only ticks on real set changes; unrelated panel
    // edits leave the live sockets untouched.
  }, [subscriptionsKey, streamAnalysisRpcReady]);

  // Close every socket on unmount only; set changes are diffed above.
  useEffect(() => {
    const groups = socketGroupsRef.current;
    return () => {
      for (const group of groups.values()) {
        group.socket.close();
      }
      groups.clear();
    };
  }, []);

  return { wsConnected };
}
