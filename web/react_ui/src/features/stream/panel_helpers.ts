import type { TraceKey } from "../../types";
import {
  defaultGraphForLegacyWorkspace,
  defaultStreamAnalysisWorkspaceConfig,
  normalizeStreamAnalysisSettings,
  normalizeStreamBinStatsSettings,
} from "./workspace";
import type {
  PlotPanelState,
  PlotStreamBin2dPanelState,
  PlotStreamBinStatsPanelState,
  PlotStreamPanelState,
  PlotStreamScalarPanelState,
  PlotStreamParamsPanelState,
  PlotStreamWaterfallPanelState,
  PlotTelemetryPanelState,
  StreamAnalysisWorkspaceConfig,
} from "./types";
import { DEFAULT_INTEGRAL_OUTPUT_ID } from "./utils";

export function normalizeAutoRange(
  range: { min: number; max: number } | null
): { min: number; max: number } | null {
  if (!range) {
    return null;
  }
  let min = Number(range.min);
  let max = Number(range.max);
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return null;
  }
  if (min > max) {
    const tmp = min;
    min = max;
    max = tmp;
  }
  if (min === max) {
    const pad = Math.abs(min) > 0 ? Math.abs(min) * 0.05 : 1;
    min -= pad;
    max += pad;
  }
  return { min, max };
}

export function isTelemetryPanel(panel: PlotPanelState): panel is PlotTelemetryPanelState {
  return panel.kind === "telemetry";
}

export function isStreamRawPanel(panel: PlotPanelState): panel is PlotStreamPanelState {
  return panel.kind === "stream_raw";
}

export function isStreamWaterfallPanel(
  panel: PlotPanelState
): panel is PlotStreamWaterfallPanelState {
  return panel.kind === "stream_waterfall";
}

export function isStreamTracePanel(
  panel: PlotPanelState
): panel is PlotStreamPanelState | PlotStreamWaterfallPanelState {
  return isStreamRawPanel(panel) || isStreamWaterfallPanel(panel);
}

export function isStreamScalarPanel(
  panel: PlotPanelState
): panel is PlotStreamScalarPanelState {
  return panel.kind === "stream_scalar";
}

export function isStreamParamsPanel(
  panel: PlotPanelState
): panel is PlotStreamParamsPanelState {
  return panel.kind === "stream_params";
}

export function isStreamBinStatsPanel(
  panel: PlotPanelState
): panel is PlotStreamBinStatsPanelState {
  return panel.kind === "stream_bin_stats";
}

export function isStreamBin2dPanel(
  panel: PlotPanelState
): panel is PlotStreamBin2dPanelState {
  return panel.kind === "stream_bin2d";
}

export function streamScalarTrace(panel: PlotStreamScalarPanelState): TraceKey {
  const outputId = panel.outputId ? panel.outputId.trim() : "";
  return {
    deviceId: "analysis",
    signal: `${panel.workspaceId}.${outputId || DEFAULT_INTEGRAL_OUTPUT_ID}`,
    valueKind: "number",
  };
}

export function workspaceFromLegacyPanel(
  panel: PlotStreamScalarPanelState | PlotStreamBinStatsPanelState
): StreamAnalysisWorkspaceConfig {
  const base = defaultStreamAnalysisWorkspaceConfig(panel.workspaceId);
  const out: StreamAnalysisWorkspaceConfig = {
    ...base,
    stream: panel.stream ?? null,
    channelIndex: panel.channelIndex,
    analysis: normalizeStreamAnalysisSettings(panel.analysis),
    binStats: isStreamBinStatsPanel(panel)
      ? normalizeStreamBinStatsSettings(panel.binStats)
      : base.binStats,
  };
  const graph = defaultGraphForLegacyWorkspace(out);
  out.graphNodes = graph.nodes;
  out.publishOutputs = graph.outputs;
  return out;
}
