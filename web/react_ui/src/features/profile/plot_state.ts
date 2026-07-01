import type { Bin2dReducer } from "../../components/StreamBin2dPanel";
import type { TraceKey } from "../../types";
import {
  defaultStreamAnalysisSettings,
  defaultStreamBinStatsSettings,
  normalizeStreamAnalysisSettings,
  normalizeStreamBinStatsSettings,
  normalizeUncertaintyMode,
} from "../stream/workspace";
import type {
  PanelKind,
  PlotPanelState,
  StreamTraceSourceMode,
} from "../stream/types";
import type { PlotState } from "./types";
import {
  DEFAULT_STREAM_OVERLAY_COUNT,
  DEFAULT_TELEMETRY_SMOOTHING_MODE,
  DEFAULT_TELEMETRY_SMOOTHING_WINDOW_S,
  DEFAULT_TRACE_AVERAGE_MODE,
  DEFAULT_TRACE_DECIMATOR,
  DEFAULT_TRACE_MAX_FPS,
  DEFAULT_TRACE_MAX_POINTS,
  DEFAULT_TRACE_ROLLING_WINDOW,
  DEFAULT_UNCERTAINTY_SCALE,
  normalizeShape,
  normalizeTelemetrySmoothingMode,
  normalizeTelemetrySmoothingWindow,
  normalizeTraceAverageMode,
  normalizeTraceDecimator,
  normalizeTraceMaxFps,
  normalizeTraceMaxPoints,
  normalizeTraceRollingWindow,
  normalizeYBound,
  normalizeYScaleMode,
} from "../stream/utils";

export function normalizePlotState(
  raw: unknown,
  opts?: { defaultWindowS?: number }
): PlotState {
  const defaultWindowS = opts?.defaultWindowS ?? 60;
  const fallbackPanel: PlotPanelState = {
    id: "panel-1",
    title: "Panel 1",
    kind: "telemetry",
    traces: [],
    timeWindowS: defaultWindowS,
    yScaleMode: "auto",
    yMin: null,
    yMax: null,
    yDisplayMode: "absolute",
    yOffsetMode: "auto",
    yOffsetValue: null,
    smoothingMode: DEFAULT_TELEMETRY_SMOOTHING_MODE,
    smoothingWindowS: DEFAULT_TELEMETRY_SMOOTHING_WINDOW_S,
  };
  const fallback: PlotState = {
    panels: [fallbackPanel],
    activePanelId: fallbackPanel.id,
    nextPanelId: 1,
  };
  if (!raw || typeof raw !== "object") {
    return fallback;
  }
  const parsed = raw as { panels?: unknown; activePanelId?: unknown };
  if (!Array.isArray(parsed.panels)) {
    return fallback;
  }
  const panels: PlotPanelState[] = [];
  for (const entry of parsed.panels) {
    if (!entry || typeof entry !== "object") {
      continue;
    }
    const panel = entry as {
      id?: unknown;
      title?: unknown;
      kind?: unknown;
      sourceMode?: unknown;
      workspaceId?: unknown;
      outputId?: unknown;
      overlayOutputIds?: unknown;
      fitOverlayOutputIds?: unknown;
      timeWindowS?: unknown;
      traces?: unknown;
      stream?: unknown;
      overlayCount?: unknown;
      channelIndex?: unknown;
      extraChannelIndices?: unknown;
      traceDecimator?: unknown;
      traceMaxPoints?: unknown;
      traceMaxFps?: unknown;
      rollingWindow?: unknown;
      averageMode?: unknown;
      analysis?: unknown;
      binStats?: unknown;
      reducer?: unknown;
      uncertaintyMode?: unknown;
      uncertaintyScale?: unknown;
      showBinMarkers?: unknown;
      yScaleMode?: unknown;
      yMin?: unknown;
      yMax?: unknown;
      yDisplayMode?: unknown;
      yOffsetMode?: unknown;
      yOffsetValue?: unknown;
      smoothingMode?: unknown;
      smoothingWindowS?: unknown;
    };
    const id = typeof panel.id === "string" ? panel.id : "";
    if (!id) {
      continue;
    }
    const title =
      typeof panel.title === "string" && panel.title.trim().length > 0
        ? panel.title
        : id;
    const kindRaw = String(panel.kind ?? "").trim();
    const kind: PanelKind =
      kindRaw === "stream_raw" ||
      kindRaw === "stream_trace" ||
      kindRaw === "stream_waterfall" ||
      kindRaw === "stream_scalar" ||
      kindRaw === "stream_params" ||
      kindRaw === "stream_bin_stats" ||
      kindRaw === "stream_bin2d" ||
      kindRaw === "telemetry"
        ? (kindRaw === "stream_trace" ? "stream_raw" : (kindRaw as PanelKind))
        : "telemetry";
    const yScaleMode = normalizeYScaleMode(panel.yScaleMode);
    const yMin = normalizeYBound(panel.yMin);
    const yMax = normalizeYBound(panel.yMax);
    const yDisplayMode = panel.yDisplayMode === "delta" ? "delta" : "absolute";
    const yOffsetMode = panel.yOffsetMode === "freeze" ? "freeze" : "auto";
    const yOffsetValue = normalizeYBound(panel.yOffsetValue);
    const smoothingMode = normalizeTelemetrySmoothingMode(
      panel.smoothingMode ?? DEFAULT_TELEMETRY_SMOOTHING_MODE
    );
    const smoothingWindowS = normalizeTelemetrySmoothingWindow(
      panel.smoothingWindowS ?? DEFAULT_TELEMETRY_SMOOTHING_WINDOW_S
    );
    if (kind === "stream_raw" || kind === "stream_waterfall") {
      const streamRaw =
        panel.stream && typeof panel.stream === "object"
          ? (panel.stream as {
              deviceId?: unknown;
              stream?: unknown;
              units?: unknown;
              shape?: unknown;
            })
          : null;
      const streamDeviceId =
        streamRaw && typeof streamRaw.deviceId === "string"
          ? streamRaw.deviceId
          : "";
      const streamName =
        streamRaw && typeof streamRaw.stream === "string"
          ? streamRaw.stream
          : "";
      const streamUnits =
        streamRaw && typeof streamRaw.units === "string"
          ? streamRaw.units
          : undefined;
      const streamTarget =
        streamDeviceId && streamName
          ? {
              deviceId: streamDeviceId,
              stream: streamName,
              units: streamUnits,
              shape: normalizeShape(streamRaw?.shape),
            }
          : null;
      const overlayCount =
        typeof panel.overlayCount === "number" && Number.isFinite(panel.overlayCount)
          ? Math.max(1, Math.trunc(panel.overlayCount))
          : DEFAULT_STREAM_OVERLAY_COUNT;
      const channelIndex =
        typeof panel.channelIndex === "number" && Number.isFinite(panel.channelIndex)
          ? Math.max(0, Math.trunc(panel.channelIndex))
          : 0;
      const sourceMode: StreamTraceSourceMode =
        panel.sourceMode === "dag" ? "dag" : "raw";
      const workspaceId =
        typeof panel.workspaceId === "string" && panel.workspaceId.trim().length > 0
          ? panel.workspaceId.trim()
          : id;
      const outputIdRaw =
        typeof panel.outputId === "string" ? panel.outputId.trim() : "";
      const overlayOutputIds = Array.isArray(panel.overlayOutputIds)
        ? panel.overlayOutputIds
            .map((value) => String(value ?? "").trim())
            .filter((value) => value.length > 0)
        : [];
      const traceDecimator = normalizeTraceDecimator(
        panel.traceDecimator ?? DEFAULT_TRACE_DECIMATOR
      );
      const traceMaxPoints = normalizeTraceMaxPoints(
        panel.traceMaxPoints ?? DEFAULT_TRACE_MAX_POINTS
      );
      const traceMaxFps = normalizeTraceMaxFps(
        panel.traceMaxFps ?? DEFAULT_TRACE_MAX_FPS
      );
      const rollingWindow = normalizeTraceRollingWindow(
        panel.rollingWindow ?? DEFAULT_TRACE_ROLLING_WINDOW
      );
      const averageMode = normalizeTraceAverageMode(
        panel.averageMode ?? DEFAULT_TRACE_AVERAGE_MODE
      );
      const baseTrace = {
        id,
        title,
        sourceMode,
        stream: streamTarget,
        overlayCount,
        channelIndex,
        workspaceId,
        outputId: outputIdRaw || null,
        overlayOutputIds,
        traceDecimator,
        traceMaxPoints,
        traceMaxFps,
        rollingWindow,
        averageMode,
        yScaleMode,
        yMin,
        yMax,
      };
      if (kind === "stream_raw") {
        const extraChannelIndices = Array.isArray(panel.extraChannelIndices)
          ? panel.extraChannelIndices
              .map((value) => Math.trunc(Number(value)))
              .filter(
                (value) => Number.isFinite(value) && value >= 0 && value !== channelIndex
              )
          : [];
        panels.push({ ...baseTrace, kind, extraChannelIndices });
      } else {
        panels.push({ ...baseTrace, kind });
      }
      continue;
    }

    if (kind === "stream_scalar") {
      const streamRaw =
        panel.stream && typeof panel.stream === "object"
          ? (panel.stream as {
              deviceId?: unknown;
              stream?: unknown;
              units?: unknown;
              shape?: unknown;
            })
          : null;
      const streamDeviceId =
        streamRaw && typeof streamRaw.deviceId === "string"
          ? streamRaw.deviceId
          : "";
      const streamName =
        streamRaw && typeof streamRaw.stream === "string"
          ? streamRaw.stream
          : "";
      const streamUnits =
        streamRaw && typeof streamRaw.units === "string"
          ? streamRaw.units
          : undefined;
      const streamTarget =
        streamDeviceId && streamName
          ? {
              deviceId: streamDeviceId,
              stream: streamName,
              units: streamUnits,
              shape: normalizeShape(streamRaw?.shape),
            }
          : null;
      const channelIndex =
        typeof panel.channelIndex === "number" && Number.isFinite(panel.channelIndex)
          ? Math.max(0, Math.trunc(panel.channelIndex))
          : 0;
      const workspaceId =
        typeof panel.workspaceId === "string" && panel.workspaceId.trim().length > 0
          ? panel.workspaceId.trim()
          : id;
      const outputIdRaw =
        typeof panel.outputId === "string" ? panel.outputId.trim() : "";
      const timeWindowS =
        typeof panel.timeWindowS === "number" && Number.isFinite(panel.timeWindowS)
          ? Math.max(5, panel.timeWindowS)
          : defaultWindowS;
      panels.push({
        id,
        title,
        kind: "stream_scalar",
        workspaceId,
        outputId: outputIdRaw || null,
        stream: streamTarget,
        channelIndex,
        analysis: normalizeStreamAnalysisSettings(panel.analysis),
        timeWindowS,
        yScaleMode,
        yMin,
        yMax,
      });
      continue;
    }

    if (kind === "stream_params") {
      const workspaceId =
        typeof panel.workspaceId === "string" && panel.workspaceId.trim().length > 0
          ? panel.workspaceId.trim()
          : id;
      const outputIds = Array.isArray((panel as { outputIds?: unknown }).outputIds)
        ? ((panel as { outputIds?: unknown }).outputIds as unknown[])
            .map((value) => String(value ?? "").trim())
            .filter((value) => value.length > 0)
        : [];
      panels.push({
        id,
        title,
        kind: "stream_params",
        workspaceId,
        outputIds,
      });
      continue;
    }

    if (kind === "stream_bin_stats") {
      const streamRaw =
        panel.stream && typeof panel.stream === "object"
          ? (panel.stream as {
              deviceId?: unknown;
              stream?: unknown;
              units?: unknown;
              shape?: unknown;
            })
          : null;
      const streamDeviceId =
        streamRaw && typeof streamRaw.deviceId === "string"
          ? streamRaw.deviceId
          : "";
      const streamName =
        streamRaw && typeof streamRaw.stream === "string"
          ? streamRaw.stream
          : "";
      const streamUnits =
        streamRaw && typeof streamRaw.units === "string"
          ? streamRaw.units
          : undefined;
      const streamTarget =
        streamDeviceId && streamName
          ? {
              deviceId: streamDeviceId,
              stream: streamName,
              units: streamUnits,
              shape: normalizeShape(streamRaw?.shape),
            }
          : null;
      const channelIndex =
        typeof panel.channelIndex === "number" && Number.isFinite(panel.channelIndex)
          ? Math.max(0, Math.trunc(panel.channelIndex))
          : 0;
      const workspaceId =
        typeof panel.workspaceId === "string" && panel.workspaceId.trim().length > 0
          ? panel.workspaceId.trim()
          : id;
      const outputIdRaw =
        typeof panel.outputId === "string" ? panel.outputId.trim() : "";
      const overlayOutputIds = Array.isArray(panel.overlayOutputIds)
        ? panel.overlayOutputIds
            .map((value) => String(value ?? "").trim())
            .filter((value) => value.length > 0)
        : [];
      const fitOverlayOutputIds = Array.isArray(panel.fitOverlayOutputIds)
        ? panel.fitOverlayOutputIds
            .map((value) => String(value ?? "").trim())
            .filter((value) => value.length > 0)
        : [];
      panels.push({
        id,
        title,
        kind: "stream_bin_stats",
        workspaceId,
        outputId: outputIdRaw || null,
        overlayOutputIds,
        fitOverlayOutputIds,
        stream: streamTarget,
        channelIndex,
        analysis: normalizeStreamAnalysisSettings(panel.analysis),
        binStats: normalizeStreamBinStatsSettings(panel.binStats),
        uncertaintyMode: normalizeUncertaintyMode(panel.uncertaintyMode),
        uncertaintyScale: (() => {
          const raw = Number(panel.uncertaintyScale);
          if (!Number.isFinite(raw) || raw < 0) {
            return DEFAULT_UNCERTAINTY_SCALE;
          }
          return raw;
        })(),
        showBinMarkers: panel.showBinMarkers === true,
        yScaleMode,
        yMin,
        yMax,
      });
      continue;
    }

    if (kind === "stream_bin2d") {
      const workspaceId =
        typeof panel.workspaceId === "string" && panel.workspaceId.trim().length > 0
          ? panel.workspaceId.trim()
          : id;
      const outputIdRaw =
        typeof panel.outputId === "string" ? panel.outputId.trim() : "";
      const reducerRaw = String(panel.reducer ?? "").trim().toLowerCase();
      const reducer: Bin2dReducer =
        reducerRaw === "max" ||
        reducerRaw === "min" ||
        reducerRaw === "count" ||
        reducerRaw === "std" ||
        reducerRaw === "sem" ||
        reducerRaw === "sum"
          ? (reducerRaw as Bin2dReducer)
          : "mean";
      panels.push({
        id,
        title,
        kind: "stream_bin2d",
        workspaceId,
        outputId: outputIdRaw || null,
        reducer,
        yScaleMode,
        yMin,
        yMax,
      });
      continue;
    }

    const timeWindowS =
      typeof panel.timeWindowS === "number" && Number.isFinite(panel.timeWindowS)
        ? Math.max(5, panel.timeWindowS)
        : defaultWindowS;
    const traces: TraceKey[] = [];
    if (Array.isArray(panel.traces)) {
      for (const trace of panel.traces) {
        if (!trace || typeof trace !== "object") {
          continue;
        }
        const rawTrace = trace as {
          deviceId?: unknown;
          signal?: unknown;
          units?: unknown;
          valueKind?: unknown;
        };
        const deviceId =
          typeof rawTrace.deviceId === "string" ? rawTrace.deviceId : "";
        const signal = typeof rawTrace.signal === "string" ? rawTrace.signal : "";
        if (!deviceId || !signal) {
          continue;
        }
        const units =
          typeof rawTrace.units === "string" ? rawTrace.units : undefined;
        const valueKind =
          rawTrace.valueKind === "boolean" || rawTrace.valueKind === "number"
            ? rawTrace.valueKind
            : undefined;
        traces.push({ deviceId, signal, units, valueKind });
      }
    }
    panels.push({
      id,
      title,
      kind: "telemetry",
      traces,
      timeWindowS,
      yScaleMode,
      yMin,
      yMax,
      yDisplayMode,
      yOffsetMode,
      yOffsetValue,
      smoothingMode,
      smoothingWindowS,
    });
  }
  if (panels.length === 0) {
    return fallback;
  }
  const activePanelId =
    typeof parsed.activePanelId === "string" &&
    panels.some((panel) => panel.id === parsed.activePanelId)
      ? parsed.activePanelId
      : panels[0].id;
  let maxId = 0;
  for (const panel of panels) {
    const match = /panel-(\d+)/.exec(panel.id);
    if (match) {
      const num = Number(match[1]);
      if (Number.isFinite(num)) {
        maxId = Math.max(maxId, num);
      }
    }
  }
  const nextPanelId = Math.max(maxId, panels.length);
  return { panels, activePanelId, nextPanelId };
}

export function defaultPlotState(defaultWindowS = 60): PlotState {
  return normalizePlotState(null, { defaultWindowS });
}

export function serializePlotState(
  state: Pick<PlotState, "panels" | "activePanelId">
): Pick<PlotState, "panels" | "activePanelId"> {
  const panels = state.panels.map((panel): PlotPanelState => {
    if (panel.kind === "stream_raw" || panel.kind === "stream_waterfall") {
      return {
        ...panel,
        stream: panel.stream
          ? {
              ...panel.stream,
              shape: normalizeShape(panel.stream.shape),
            }
          : null,
        overlayOutputIds: [...panel.overlayOutputIds],
        ...(panel.kind === "stream_raw"
          ? { extraChannelIndices: [...panel.extraChannelIndices] }
          : {}),
      };
    }

    if (panel.kind === "stream_scalar") {
      return {
        ...panel,
        stream: panel.stream
          ? {
              ...panel.stream,
              shape: normalizeShape(panel.stream.shape),
            }
          : null,
        analysis: normalizeStreamAnalysisSettings(panel.analysis),
      };
    }

    if (panel.kind === "stream_params") {
      return {
        ...panel,
        outputIds: [...panel.outputIds],
      };
    }

    if (panel.kind === "stream_bin_stats") {
      return {
        ...panel,
        stream: panel.stream
          ? {
              ...panel.stream,
              shape: normalizeShape(panel.stream.shape),
            }
          : null,
        overlayOutputIds: [...panel.overlayOutputIds],
        fitOverlayOutputIds: [...panel.fitOverlayOutputIds],
        analysis: normalizeStreamAnalysisSettings(panel.analysis),
        binStats: normalizeStreamBinStatsSettings(panel.binStats),
      };
    }

    if (panel.kind === "stream_bin2d") {
      return { ...panel };
    }

    return {
      ...panel,
      traces: panel.traces.map((trace) => ({ ...trace })),
    };
  });

  return {
    panels,
    activePanelId: state.activePanelId,
  };
}

export function defaultStreamAnalysisLegacySettings() {
  return defaultStreamAnalysisSettings();
}

export function defaultStreamBinStatsLegacySettings() {
  return defaultStreamBinStatsSettings();
}
