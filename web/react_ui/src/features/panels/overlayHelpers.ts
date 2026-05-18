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
