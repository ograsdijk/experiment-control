import type {
  PlotStreamBinStatsPanelState,
  PlotStreamPanelState,
  PlotStreamWaterfallPanelState,
  StreamFitCurveSnapshot,
} from "../stream/types";

/**
 * Pure functions that build display-ready overlay series from the
 * Map-of-Map overlay caches owned by TelemetryContext.
 *
 * App.tsx defined these inline alongside the panel handlers; they're
 * read by both the auto-range Y-axis logic and the panel render loop,
 * so extracting them to a shared module lets both call sites import
 * the same implementation.
 *
 * Each function takes the panel + the relevant overlay Map. The Maps
 * are passed explicitly rather than read from context so the helpers
 * stay pure and trivially unit-testable.
 */

type TracePanel = PlotStreamPanelState | PlotStreamWaterfallPanelState;
type TraceOverlayMap = Map<string, Map<string, { seq: number; values: number[] }>>;
type ExtraChannelMap = Map<string, Map<number, { seq: number; values: number[] }>>;
type BinStatsOverlayMap = Map<string, Map<string, { seq: number; values: number[] }>>;
type BinStatsFitOverlayMap = Map<string, Map<string, StreamFitCurveSnapshot>>;

export function streamTraceOverlaySeries(
  panel: TracePanel,
  overlayRef: TraceOverlayMap
): Array<{ label: string; values: number[] }> {
  const overlayMap = overlayRef.get(panel.id);
  if (!overlayMap || overlayMap.size <= 0) {
    return [];
  }
  const selected = panel.overlayOutputIds ?? [];
  const out: Array<{ label: string; values: number[] }> = [];
  for (const outputId of selected) {
    const entry = overlayMap.get(outputId);
    if (!entry || !Array.isArray(entry.values) || entry.values.length <= 0) {
      continue;
    }
    out.push({
      label: outputId,
      values: entry.values,
    });
  }
  return out;
}

/**
 * Extra-channel series for a multi-channel raw stream panel: one entry
 * per `extraChannelIndices` channel, each carrying that channel's latest
 * frame (populated by `applyRawStreamFrameToPanels`). Empty for
 * single-channel panels, DAG panels, and waterfalls. Rendered as
 * additional uPlot series via `StreamRawPanel`'s `extraSeries` prop.
 */
export function streamExtraChannelSeries(
  panel: TracePanel,
  extraChannelRef: ExtraChannelMap
): Array<{ label: string; values: number[] }> {
  if (panel.kind !== "stream_raw" || panel.sourceMode !== "raw") {
    return [];
  }
  const byChannel = extraChannelRef.get(panel.id);
  if (!byChannel || byChannel.size <= 0) {
    return [];
  }
  const out: Array<{ label: string; values: number[] }> = [];
  for (const channel of panel.extraChannelIndices ?? []) {
    const entry = byChannel.get(Math.max(0, Math.trunc(channel)));
    if (!entry || !Array.isArray(entry.values) || entry.values.length <= 0) {
      continue;
    }
    out.push({ label: `ch ${channel}`, values: entry.values });
  }
  return out;
}

export function streamBinStatsOverlaySeries(
  panel: PlotStreamBinStatsPanelState,
  overlayRef: BinStatsOverlayMap
): Array<{ label: string; values: number[] }> {
  const overlayMap = overlayRef.get(panel.id);
  if (!overlayMap || overlayMap.size <= 0) {
    return [];
  }
  const selected = panel.overlayOutputIds ?? [];
  const out: Array<{ label: string; values: number[] }> = [];
  for (const outputId of selected) {
    const entry = overlayMap.get(outputId);
    if (!entry || !Array.isArray(entry.values) || entry.values.length <= 0) {
      continue;
    }
    out.push({ label: outputId, values: entry.values });
  }
  return out;
}

export function streamBinStatsFitOverlayCurves(
  panel: PlotStreamBinStatsPanelState,
  overlayRef: BinStatsFitOverlayMap
): Array<{ label: string; x: number[]; y: number[] }> {
  const overlayMap = overlayRef.get(panel.id);
  if (!overlayMap || overlayMap.size <= 0) {
    return [];
  }
  const selected = panel.fitOverlayOutputIds ?? [];
  const out: Array<{ label: string; x: number[]; y: number[] }> = [];
  for (const outputId of selected) {
    const entry = overlayMap.get(outputId);
    if (!entry) {
      continue;
    }
    const x = entry.xDense ?? entry.x;
    const y = entry.yhatDense ?? entry.yhat;
    if (!Array.isArray(x) || !Array.isArray(y) || x.length <= 1 || y.length <= 1) {
      continue;
    }
    out.push({ label: outputId, x, y });
  }
  return out;
}
