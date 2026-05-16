import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  type MutableRefObject,
  type ReactNode,
} from "react";

import type {
  StreamBin2dSnapshot,
  StreamBinStatsSnapshot,
  StreamFitCurveSnapshot,
  StreamFrameSample,
  StreamParamsOutputValue,
} from "../stream/types";
import { RingBuffer } from "../../utils/ringBuffer";

/**
 * Shared telemetry / plot-buffer state container.
 *
 * App.tsx historically held the per-panel ring buffers and stream-overlay
 * caches inline. This Provider owns them so future feature-module
 * extractions (panel rendering, DAQ, etc.) can subscribe via
 * `useTelemetry()` rather than threading refs through props.
 *
 * **Scope choices for the first cut:**
 *
 * - The Provider owns the **mutable Map containers** (ring buffers +
 *   per-stream overlay caches) plus the P5 reverse index
 *   (`panelBuffersByTraceKey`).
 * - WS lifecycle and message-routing logic stays in App.tsx for now —
 *   it's coupled to `panels` state and apply* helpers that haven't been
 *   extracted yet. Later PRs can fold those in once `panels` state moves
 *   into its own controller.
 * - `useTelemetryStream` (the simple WS hook used by downstream centrex
 *   instance UIs) is **not touched**. The Context wraps a different
 *   problem (panel-buffer management) and doesn't subsume the standalone
 *   hook; both APIs coexist.
 *
 * Maps are exposed directly (not via refs) so existing call sites in
 * App.tsx continue to work without rewriting every `.get()` / `.set()`
 * to go through `.current`. The Map identity is stable across renders
 * because the Provider holds them in refs internally — only the
 * `useMemo` value object's identity changes when actions are first
 * created (once, at mount).
 *
 * **P5 reverse index** (`panelBuffersByTraceKey`):
 *
 * The original message-routing hot path walked `buffersRef.values()` for
 * every incoming signal — O(N panels) lookups per message. The reverse
 * index maps each `traceKey` (`deviceId:signal`) to the set of `panelId`s
 * that draw that trace, so the hot path becomes O(1) lookup + O(matching
 * panels) writes. Panel components call
 * `registerPanelTraces(panelId, traceKeys)` whenever their trace set
 * changes and `unregisterPanel(panelId)` on unmount.
 */

export type TraceKey = string; // `${deviceId}:${signal}`
export type PanelId = string;

type PanelBuffersMap = Map<PanelId, Map<TraceKey, RingBuffer>>;
type StreamFramesMap = Map<string, StreamFrameSample[]>;
type StreamTraceOverlayMap = Map<
  string,
  Map<string, { seq: number; values: number[] }>
>;
type StreamBinStatsOverlayMap = Map<
  string,
  Map<string, { seq: number; values: number[] }>
>;
type StreamBinStatsFitOverlayMap = Map<
  string,
  Map<string, StreamFitCurveSnapshot>
>;
type StreamParamsLatestMap = Map<string, Record<string, StreamParamsOutputValue>>;
type StreamBinStatsMap = Map<string, StreamBinStatsSnapshot>;
type StreamBin2dMap = Map<string, StreamBin2dSnapshot>;

export interface TelemetryContextValue {
  // Plot-buffer Maps. Stable identity across renders; mutated in place by
  // App.tsx's telemetry handlers and read by panel renderers.
  buffersRef: PanelBuffersMap;
  streamFramesRef: StreamFramesMap;
  streamTraceOverlayRef: StreamTraceOverlayMap;
  streamBinStatsOverlayRef: StreamBinStatsOverlayMap;
  streamBinStatsFitOverlayRef: StreamBinStatsFitOverlayMap;
  streamParamsLatestRef: StreamParamsLatestMap;
  streamBinStatsRef: StreamBinStatsMap;
  streamBin2dRef: StreamBin2dMap;

  // P5: reverse index. traceKey → set of panelIds that draw that trace.
  // Exposed via a ref because consumers (the message handler) read
  // `.current` from inside the WS thread without needing re-renders.
  panelBuffersByTraceKey: MutableRefObject<Map<TraceKey, Set<PanelId>>>;

  // Panel lifecycle. Called from panel-renderer useEffects so the index
  // stays consistent with each panel's current trace set.
  registerPanelTraces(panelId: PanelId, traceKeys: readonly TraceKey[]): void;
  unregisterPanel(panelId: PanelId): void;
}

const TelemetryContext = createContext<TelemetryContextValue | null>(null);

export function TelemetryProvider({ children }: { children: ReactNode }) {
  // useMemo with [] deps gives the same stable Map identity across renders
  // that App.tsx's previous inline `useMemo(() => new Map(), [])` calls
  // provided. The Maps themselves are mutated in place.
  const buffersRef = useMemo<PanelBuffersMap>(() => new Map(), []);
  const streamFramesRef = useMemo<StreamFramesMap>(() => new Map(), []);
  const streamTraceOverlayRef = useMemo<StreamTraceOverlayMap>(
    () => new Map(),
    []
  );
  const streamBinStatsOverlayRef = useMemo<StreamBinStatsOverlayMap>(
    () => new Map(),
    []
  );
  const streamBinStatsFitOverlayRef = useMemo<StreamBinStatsFitOverlayMap>(
    () => new Map(),
    []
  );
  const streamParamsLatestRef = useMemo<StreamParamsLatestMap>(
    () => new Map(),
    []
  );
  const streamBinStatsRef = useMemo<StreamBinStatsMap>(() => new Map(), []);
  const streamBin2dRef = useMemo<StreamBin2dMap>(() => new Map(), []);

  // P5 reverse index + an auxiliary panel→traceKey map so unregister
  // can find which trace-key sets to strip a panelId from without
  // iterating the whole reverse index.
  const panelBuffersByTraceKey = useRef<Map<TraceKey, Set<PanelId>>>(new Map());
  const traceKeysByPanel = useRef<Map<PanelId, Set<TraceKey>>>(new Map());

  const registerPanelTraces = useCallback(
    (panelId: PanelId, traceKeys: readonly TraceKey[]) => {
      const reverse = panelBuffersByTraceKey.current;
      const byPanel = traceKeysByPanel.current;
      const previous = byPanel.get(panelId) ?? new Set<TraceKey>();
      const next = new Set<TraceKey>(traceKeys);
      // Remove panel from trace-key sets it no longer belongs to.
      for (const key of previous) {
        if (next.has(key)) continue;
        const peers = reverse.get(key);
        if (!peers) continue;
        peers.delete(panelId);
        if (peers.size === 0) {
          reverse.delete(key);
        }
      }
      // Add panel to newly registered trace-key sets.
      for (const key of next) {
        if (previous.has(key)) continue;
        let peers = reverse.get(key);
        if (!peers) {
          peers = new Set<PanelId>();
          reverse.set(key, peers);
        }
        peers.add(panelId);
      }
      if (next.size === 0) {
        byPanel.delete(panelId);
      } else {
        byPanel.set(panelId, next);
      }
    },
    []
  );

  const unregisterPanel = useCallback((panelId: PanelId) => {
    const reverse = panelBuffersByTraceKey.current;
    const byPanel = traceKeysByPanel.current;
    const previous = byPanel.get(panelId);
    if (!previous) return;
    for (const key of previous) {
      const peers = reverse.get(key);
      if (!peers) continue;
      peers.delete(panelId);
      if (peers.size === 0) {
        reverse.delete(key);
      }
    }
    byPanel.delete(panelId);
  }, []);

  const value = useMemo<TelemetryContextValue>(
    () => ({
      buffersRef,
      streamFramesRef,
      streamTraceOverlayRef,
      streamBinStatsOverlayRef,
      streamBinStatsFitOverlayRef,
      streamParamsLatestRef,
      streamBinStatsRef,
      streamBin2dRef,
      panelBuffersByTraceKey,
      registerPanelTraces,
      unregisterPanel,
    }),
    [
      buffersRef,
      streamFramesRef,
      streamTraceOverlayRef,
      streamBinStatsOverlayRef,
      streamBinStatsFitOverlayRef,
      streamParamsLatestRef,
      streamBinStatsRef,
      streamBin2dRef,
      registerPanelTraces,
      unregisterPanel,
    ]
  );

  return (
    <TelemetryContext.Provider value={value}>
      {children}
    </TelemetryContext.Provider>
  );
}

export function useTelemetry(): TelemetryContextValue {
  const ctx = useContext(TelemetryContext);
  if (ctx === null) {
    throw new Error("useTelemetry must be called inside a <TelemetryProvider>");
  }
  return ctx;
}
