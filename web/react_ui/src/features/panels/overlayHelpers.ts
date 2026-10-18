import type {
  PlotStreamBinStatsPanelState,
  PlotStreamPanelState,
  PlotStreamWaterfallPanelState,
  StreamBinStatsSnapshot,
  StreamFitCurveSnapshot,
} from "../stream/types";
import type { StreamBinStatsHistOverlay } from "../../components/StreamBinStatsPanel";

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
type BinStatsHistOverlayMap = Map<string, Map<string, StreamBinStatsSnapshot>>;

function cachedValues(
  entry: { seq: number; values: number[] } | undefined
): number[] {
  return entry && Array.isArray(entry.values) && entry.values.length > 0
    ? entry.values
    : [];
}

export function streamTraceOverlaySeries(
  panel: TracePanel,
  overlayRef: TraceOverlayMap
): Array<{ label: string; values: number[] }> {
  const overlayMap = overlayRef.get(panel.id);
  return (panel.overlayOutputIds ?? []).map((outputId) => ({
    label: outputId,
    values: cachedValues(overlayMap?.get(outputId)),
  }));
}

/**
 * Extra-channel series for a multi-channel raw stream panel: one entry
 * per `extraChannelIndices` channel, each carrying that channel's latest
 * frame (populated by `applyRawStreamFrameToPanels`). Empty for
 * single-channel panels, DAG panels, and waterfalls. Rendered as
 * additional uPlot series via `StreamRawPanel`'s `extraSeries` prop.
 *
 * Configured channel slots are preserved even before a frame is cached so
 * transient data availability cannot change the uPlot series topology.
 */
export function streamExtraChannelSeries(
  panel: TracePanel,
  extraChannelRef: ExtraChannelMap
): Array<{ label: string; values: number[] }> {
  if (panel.kind !== "stream_raw" || panel.sourceMode !== "raw") {
    return [];
  }
  const byChannel = extraChannelRef.get(panel.id);
  return (panel.extraChannelIndices ?? []).map((channel) => {
    const normalizedChannel = Math.max(0, Math.trunc(channel));
    return {
      label: `ch ${channel}`,
      values: cachedValues(byChannel?.get(normalizedChannel)),
    };
  });
}

export function streamBinStatsOverlaySeries(
  panel: PlotStreamBinStatsPanelState,
  overlayRef: BinStatsOverlayMap
): Array<{ label: string; values: number[] }> {
  const overlayMap = overlayRef.get(panel.id);
  return (panel.overlayOutputIds ?? []).map((outputId) => ({
    label: outputId,
    values: cachedValues(overlayMap?.get(outputId)),
  }));
}

/**
 * Histogram overlays for a bin-stats panel, in selection order. A slot is
 * returned for every selected output, with `series: null` until its first
 * update, so the plot's series layout and colours stay put.
 */
export function streamBinStatsHistOverlaySeries(
  panel: PlotStreamBinStatsPanelState,
  overlayRef: BinStatsHistOverlayMap
): StreamBinStatsHistOverlay[] {
  const overlayMap = overlayRef.get(panel.id);
  return (panel.histOverlayOutputIds ?? []).map((outputId) => ({
    label: outputId,
    series: overlayMap?.get(outputId)?.series ?? null,
  }));
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
