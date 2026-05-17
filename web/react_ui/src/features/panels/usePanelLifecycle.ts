import type { Dispatch, SetStateAction } from "react";

import type { LatestSignals } from "../telemetry/useTelemetryStream";
import {
  isStreamScalarPanel,
  isTelemetryPanel,
  streamScalarTrace,
} from "../stream/panel_helpers";
import type { PlotPanelState, PanelKind } from "../stream/types";
import type { StreamAnalysisWorkspaceConfig } from "../stream/types";
import type { TraceKey } from "../../types";
import {
  DEFAULT_BIN2D_REDUCER,
  DEFAULT_STREAM_OVERLAY_COUNT,
  DEFAULT_TELEMETRY_SMOOTHING_MODE,
  DEFAULT_TELEMETRY_SMOOTHING_WINDOW_S,
  DEFAULT_TRACE_AVERAGE_MODE,
  DEFAULT_TRACE_DECIMATOR,
  DEFAULT_TRACE_MAX_FPS,
  DEFAULT_TRACE_MAX_POINTS,
  DEFAULT_TRACE_ROLLING_WINDOW,
  DEFAULT_UNCERTAINTY_SCALE,
  DEFAULT_WATERFALL_ROWS,
  traceKeyId,
} from "../stream/utils";
import {
  defaultOutputForKind,
  defaultStreamAnalysisSettings,
  defaultStreamAnalysisWorkspaceConfig,
  defaultStreamBinStatsSettings,
  workspaceOutputOptionsByKind,
} from "../stream/workspace";
import { useStreamAnalysis } from "../stream_analysis/StreamAnalysisContext";
import { useTelemetry } from "../telemetry/TelemetryContext";
import { RingBuffer } from "../../utils/ringBuffer";
import {
  ensurePanelBuffers as ensurePanelBuffersImpl,
  panelCapacity as panelCapacityImpl,
} from "./applyToPanels";
import { usePanels } from "./PanelsContext";

const DEFAULT_WINDOW_S = 60;

/**
 * Panel-lifecycle handlers: create / remove / add+remove telemetry
 * traces / resize a panel's time window.
 *
 * These are the most cross-context-coupled handlers in App.tsx —
 * `createPanel` alone reads/writes PanelsContext + StreamAnalysisContext
 * + TelemetryContext plus a handful of default constants. The hook
 * consolidates them so a future `<PanelsGrid>` component extraction
 * (round 20) can pass them as props rather than threading the deps.
 *
 * **Handlers**:
 *
 * - `createPanel(kind)` — creates a panel of the given kind, lazily
 *   seeding a default DAQ workspace if none exists, and primes the
 *   relevant per-panel ref (buffers / frames / overlay caches).
 * - `removePanel(id)` — deletes the panel + clears every per-panel
 *   ref + closes any modal currently pointing at it + advances the
 *   active panel selection.
 * - `addTraceToPanel(panelId, deviceId, signal)` — adds a (deviceId,
 *   signal) trace to a telemetry panel, looking up live units +
 *   value-kind from `latestByDevice` and seeding the buffer.
 * - `removeTraceFromPanel(panelId, trace)` — drops a trace from a
 *   telemetry panel + deletes its buffer.
 * - `setPanelTimeWindow(panelId, value)` — resizes a telemetry or
 *   scalar panel's time window and rebuilds the ring buffers at the
 *   matching capacity (resize-in-place when the trace stays).
 *
 * **Args** (App.tsx-local state that hasn't been extracted yet):
 *
 * - `latestByDevice` — `useTelemetryStream` return value used by
 *   `addTraceToPanel` to seed units + valueKind on a new trace.
 * - `editingPanelId` / `setEditingPanelId` / `setPanelTitleDraft` —
 *   App's inline editor state; `removePanel` clears these if the
 *   removed panel was being edited.
 * - `closePlotOptions` — comes from `usePanelAutoRangeHandlers`;
 *   `removePanel` calls it if the plot-options modal was open on the
 *   removed panel.
 */

export interface PanelLifecycleArgs {
  latestByDevice: LatestSignals;
  editingPanelId: string | null;
  setEditingPanelId: Dispatch<SetStateAction<string | null>>;
  setPanelTitleDraft: Dispatch<SetStateAction<string>>;
  closePlotOptions: () => void;
}

export function usePanelLifecycle(args: PanelLifecycleArgs) {
  const {
    latestByDevice,
    editingPanelId,
    setEditingPanelId,
    setPanelTitleDraft,
    closePlotOptions,
  } = args;
  const {
    panels,
    setPanels,
    activePanelId,
    setActivePanelId,
    panelIdRef,
    setPlotTick,
    plotOptionsPanelId,
    expandedPlotPanelId,
    setExpandedPlotPanelId,
    streamTraceOptionsPanelId,
    setStreamTraceOptionsPanelId,
    streamBinStatsOptionsPanelId,
    setStreamBinStatsOptionsPanelId,
    streamBin2dOptionsPanelId,
    setStreamBin2dOptionsPanelId,
    streamParamsOptionsPanelId,
    setStreamParamsOptionsPanelId,
  } = usePanels();
  const {
    streamWorkspacesRef,
    streamWorkspaceIdRef,
    setStreamWorkspaces,
    daqWorkspaceId,
    setDaqWorkspaceId,
  } = useStreamAnalysis();
  const {
    buffersRef,
    streamFramesRef,
    streamTraceOverlayRef,
    streamBinStatsOverlayRef,
    streamBinStatsFitOverlayRef,
    streamParamsLatestRef,
    streamBinStatsRef,
    streamBin2dRef,
  } = useTelemetry();

  const createPanel = (kind: PanelKind) => {
    panelIdRef.current += 1;
    const id = `panel-${panelIdRef.current}`;
    const workspaceIds = Object.keys(streamWorkspacesRef.current).sort();
    let defaultWorkspaceId = workspaceIds[0] ?? null;
    if (!defaultWorkspaceId) {
      const nextId = Math.max(1, Math.trunc(streamWorkspaceIdRef.current));
      const workspaceId = `workspace-${nextId}`;
      streamWorkspaceIdRef.current = nextId + 1;
      const workspace = defaultStreamAnalysisWorkspaceConfig(workspaceId);
      setStreamWorkspaces((prev) => ({ ...prev, [workspaceId]: workspace }));
      streamWorkspacesRef.current = {
        ...streamWorkspacesRef.current,
        [workspaceId]: workspace,
      };
      if (!daqWorkspaceId) {
        setDaqWorkspaceId(workspaceId);
      }
      defaultWorkspaceId = workspaceId;
    }
    const workspaceConfig: StreamAnalysisWorkspaceConfig | null =
      defaultWorkspaceId
        ? streamWorkspacesRef.current[defaultWorkspaceId] ?? null
        : null;
    let panel: PlotPanelState;
    if (kind === "stream_raw" || kind === "stream_waterfall") {
      const traceOutputId = defaultOutputForKind(workspaceConfig, "trace");
      panel = {
        id,
        title:
          kind === "stream_waterfall"
            ? `Waterfall ${panelIdRef.current}`
            : `Trace ${panelIdRef.current}`,
        kind,
        sourceMode: "raw",
        stream: null,
        overlayCount:
          kind === "stream_waterfall"
            ? DEFAULT_WATERFALL_ROWS
            : DEFAULT_STREAM_OVERLAY_COUNT,
        channelIndex: 0,
        workspaceId: defaultWorkspaceId ?? id,
        outputId: traceOutputId,
        overlayOutputIds: [],
        traceDecimator: DEFAULT_TRACE_DECIMATOR,
        traceMaxPoints: DEFAULT_TRACE_MAX_POINTS,
        traceMaxFps: DEFAULT_TRACE_MAX_FPS,
        rollingWindow: DEFAULT_TRACE_ROLLING_WINDOW,
        averageMode: DEFAULT_TRACE_AVERAGE_MODE,
        yScaleMode: "auto",
        yMin: null,
        yMax: null,
      };
      streamFramesRef.set(id, []);
      streamTraceOverlayRef.set(id, new Map());
    } else if (kind === "stream_scalar") {
      const integralOutputId = defaultOutputForKind(workspaceConfig, "scalar");
      panel = {
        id,
        title: `Scalar ${panelIdRef.current}`,
        kind: "stream_scalar",
        workspaceId: defaultWorkspaceId ?? id,
        outputId: integralOutputId,
        stream: workspaceConfig?.stream ?? null,
        channelIndex: workspaceConfig?.channelIndex ?? 0,
        analysis: workspaceConfig?.analysis ?? defaultStreamAnalysisSettings(),
        timeWindowS: DEFAULT_WINDOW_S,
        yScaleMode: "auto",
        yMin: null,
        yMax: null,
      };
      buffersRef.set(id, new Map());
    } else if (kind === "stream_params") {
      const paramsOutputIds = workspaceOutputOptionsByKind(
        workspaceConfig,
        "params_map"
      ).map((item) => item.value);
      const firstScalarOutputId = defaultOutputForKind(workspaceConfig, "scalar");
      panel = {
        id,
        title: `Params ${panelIdRef.current}`,
        kind: "stream_params",
        workspaceId: defaultWorkspaceId ?? id,
        outputIds:
          paramsOutputIds.length > 0
            ? paramsOutputIds
            : firstScalarOutputId
            ? [firstScalarOutputId]
            : [],
      };
      streamParamsLatestRef.set(id, {});
    } else if (kind === "stream_bin_stats") {
      const binOutputId = defaultOutputForKind(workspaceConfig, "hist_agg");
      panel = {
        id,
        title: `Bin stats ${panelIdRef.current}`,
        kind: "stream_bin_stats",
        workspaceId: defaultWorkspaceId ?? id,
        outputId: binOutputId,
        overlayOutputIds: [],
        fitOverlayOutputIds: [],
        stream: workspaceConfig?.stream ?? null,
        channelIndex: workspaceConfig?.channelIndex ?? 0,
        analysis: workspaceConfig?.analysis ?? defaultStreamAnalysisSettings(),
        binStats: workspaceConfig?.binStats ?? defaultStreamBinStatsSettings(),
        uncertaintyMode: "sem",
        uncertaintyScale: DEFAULT_UNCERTAINTY_SCALE,
        showBinMarkers: false,
        yScaleMode: "auto",
        yMin: null,
        yMax: null,
      };
      streamBinStatsRef.delete(id);
      streamBinStatsFitOverlayRef.delete(id);
    } else if (kind === "stream_bin2d") {
      const bin2dOutputId = defaultOutputForKind(workspaceConfig, "hist2d");
      panel = {
        id,
        title: `Bin2D ${panelIdRef.current}`,
        kind: "stream_bin2d",
        workspaceId: defaultWorkspaceId ?? id,
        outputId: bin2dOutputId,
        reducer: DEFAULT_BIN2D_REDUCER,
        yScaleMode: "auto",
        yMin: null,
        yMax: null,
      };
      streamBin2dRef.delete(id);
    } else {
      panel = {
        id,
        title: `Panel ${panelIdRef.current}`,
        kind: "telemetry",
        traces: [],
        timeWindowS: DEFAULT_WINDOW_S,
        yScaleMode: "auto",
        yMin: null,
        yMax: null,
        yDisplayMode: "absolute",
        yOffsetMode: "auto",
        yOffsetValue: null,
        smoothingMode: DEFAULT_TELEMETRY_SMOOTHING_MODE,
        smoothingWindowS: DEFAULT_TELEMETRY_SMOOTHING_WINDOW_S,
      };
      buffersRef.set(id, new Map());
    }
    setPanels((prev) => [...prev, panel]);
    setActivePanelId(id);
  };

  const removePanel = (panelId: string) => {
    if (panels.length <= 1) {
      return;
    }
    const nextActive = panels.find((panel) => panel.id !== panelId);
    buffersRef.delete(panelId);
    streamFramesRef.delete(panelId);
    streamTraceOverlayRef.delete(panelId);
    streamBinStatsOverlayRef.delete(panelId);
    streamBinStatsFitOverlayRef.delete(panelId);
    streamParamsLatestRef.delete(panelId);
    streamBinStatsRef.delete(panelId);
    streamBin2dRef.delete(panelId);
    if (editingPanelId === panelId) {
      setEditingPanelId(null);
      setPanelTitleDraft("");
    }
    if (streamTraceOptionsPanelId === panelId) {
      setStreamTraceOptionsPanelId(null);
    }
    if (streamBinStatsOptionsPanelId === panelId) {
      setStreamBinStatsOptionsPanelId(null);
    }
    if (streamBin2dOptionsPanelId === panelId) {
      setStreamBin2dOptionsPanelId(null);
    }
    if (streamParamsOptionsPanelId === panelId) {
      setStreamParamsOptionsPanelId(null);
    }
    if (plotOptionsPanelId === panelId) {
      closePlotOptions();
    }
    if (expandedPlotPanelId === panelId) {
      setExpandedPlotPanelId(null);
    }
    setPanels((prev) => prev.filter((panel) => panel.id !== panelId));
    if (activePanelId === panelId && nextActive) {
      setActivePanelId(nextActive.id);
    }
    setPlotTick((tick) => tick + 1);
  };

  const addTraceToPanel = (
    panelId: string,
    deviceId: string,
    signal: string
  ) => {
    const panel = panels.find((p) => p.id === panelId);
    if (!panel || !isTelemetryPanel(panel)) {
      return;
    }
    if (panel.traces.some((t) => t.deviceId === deviceId && t.signal === signal)) {
      return;
    }
    const units = latestByDevice[deviceId]?.[signal]?.units ?? null;
    const latestValue = latestByDevice[deviceId]?.[signal]?.value;
    const valueKind =
      typeof latestValue === "boolean"
        ? "boolean"
        : typeof latestValue === "number"
        ? "number"
        : undefined;
    const trace = { deviceId, signal, units, valueKind };
    setPanels((prev) =>
      prev.map((p) =>
        p.id === panelId ? { ...p, traces: [...p.traces, trace] } : p
      )
    );
    const panelBuffers = ensurePanelBuffersImpl(buffersRef, panelId);
    const capacity = panelCapacityImpl(panel.timeWindowS);
    const key = traceKeyId(trace);
    if (!panelBuffers.has(key)) {
      panelBuffers.set(key, new RingBuffer(capacity));
    }
    setPlotTick((tick) => tick + 1);
  };

  const removeTraceFromPanel = (panelId: string, trace: TraceKey) => {
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isTelemetryPanel(panel)
          ? {
              ...panel,
              traces: panel.traces.filter(
                (item) =>
                  !(item.deviceId === trace.deviceId && item.signal === trace.signal)
              ),
            }
          : panel
      )
    );
    const panelBuffers = buffersRef.get(panelId);
    panelBuffers?.delete(traceKeyId(trace));
    setPlotTick((tick) => tick + 1);
  };

  const setPanelTimeWindow = (panelId: string, value: number) => {
    const panel = panels.find((p) => p.id === panelId);
    if (
      !panel ||
      (!isTelemetryPanel(panel) && !isStreamScalarPanel(panel))
    ) {
      return;
    }
    const nextWindow = Number.isFinite(value)
      ? Math.max(5, value)
      : panel.timeWindowS;
    setPanels((prev) =>
      prev.map((p) =>
        p.id === panelId && (isTelemetryPanel(p) || isStreamScalarPanel(p))
          ? { ...p, timeWindowS: nextWindow }
          : p
      )
    );
    const capacity = panelCapacityImpl(nextWindow);
    const panelBuffers = ensurePanelBuffersImpl(buffersRef, panelId);
    const traceKeys = isTelemetryPanel(panel)
      ? new Set(panel.traces.map(traceKeyId))
      : new Set([traceKeyId(streamScalarTrace(panel))]);
    for (const [key, buffer] of panelBuffers.entries()) {
      if (!traceKeys.has(key)) {
        panelBuffers.delete(key);
      } else {
        buffer.resize(capacity);
      }
    }
    if (isTelemetryPanel(panel)) {
      for (const trace of panel.traces) {
        const key = traceKeyId(trace);
        if (!panelBuffers.has(key)) {
          panelBuffers.set(key, new RingBuffer(capacity));
        }
      }
    } else {
      const key = traceKeyId(streamScalarTrace(panel));
      if (!panelBuffers.has(key)) {
        panelBuffers.set(key, new RingBuffer(capacity));
      }
    }
    setPlotTick((tick) => tick + 1);
  };

  return {
    createPanel,
    removePanel,
    addTraceToPanel,
    removeTraceFromPanel,
    setPanelTimeWindow,
  };
}
