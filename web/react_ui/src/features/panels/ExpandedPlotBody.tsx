import { useComputedColorScheme } from "@mantine/core";

import { PlotPanel } from "../../components/PlotPanel";
import { StreamBin2dPanel } from "../../components/StreamBin2dPanel";
import { StreamBinStatsPanel } from "../../components/StreamBinStatsPanel";
import { StreamRawPanel } from "../../components/StreamRawPanel";
import { StreamWaterfallPanel } from "../../components/StreamWaterfallPanel";
import {
  isStreamBin2dPanel,
  isStreamBinStatsPanel,
  isStreamRawPanel,
  isStreamScalarPanel,
  isStreamTracePanel,
  isTelemetryPanel,
  streamScalarTrace,
} from "../stream/panel_helpers";
import type {
  PlotPanelState,
  PlotStreamBinStatsPanelState,
  PlotStreamPanelState,
  PlotStreamWaterfallPanelState,
  PlotTelemetryPanelState,
} from "../stream/types";
import { workspaceXAxisLabel } from "../stream/workspace";
import { useStreamAnalysis } from "../stream_analysis/StreamAnalysisContext";
import { useTelemetry } from "../telemetry/TelemetryContext";
import { usePanels } from "./PanelsContext";
import { usePlotTick } from "./PlotTickContext";

/**
 * Expanded-plot modal body — renders a single panel at the larger
 * plotHeight used inside the "enlarge plot" modal.
 *
 * Previously this was a `renderExpandedPlot` inline helper in
 * App.tsx; lifting it into its own component gets the JSX out of
 * App.tsx and establishes the pattern for the larger PanelsGrid
 * extraction that follows.
 *
 * The component dispatches on panel kind to render the appropriate
 * `<PlotPanel>` / `<StreamRawPanel>` / `<StreamWaterfallPanel>` /
 * `<StreamBinStatsPanel>` / `<StreamBin2dPanel>` with a fixed
 * `plotHeight={640}`.
 *
 * **Props** (App-local handlers + overlay helpers that haven't been
 * lifted yet):
 *
 * - `resolveTelemetryPanelOffset` — from `usePanelAutoRangeHandlers`;
 *   computes the freeze-or-auto offset for telemetry panels in delta
 *   mode.
 * - `streamTraceOverlaySeries` — App-local thin wrapper around the
 *   overlay-helpers module; binds the overlay refs.
 * - `streamBinStatsOverlaySeries` / `streamBinStatsFitOverlayCurves`
 *   — same shape, for the bin-stats panel.
 *
 * Context state (panels tick, buffers/frames/snapshots refs, stream
 * workspaces, color scheme) is pulled directly from the relevant
 * context hooks.
 */

export interface ExpandedPlotBodyProps {
  panel: PlotPanelState;
  resolveTelemetryPanelOffset: (
    panel: PlotTelemetryPanelState
  ) => number | null;
  streamTraceOverlaySeries: (
    panel: PlotStreamPanelState | PlotStreamWaterfallPanelState
  ) => Array<{ label: string; values: number[] }>;
  streamBinStatsOverlaySeries: (
    panel: PlotStreamBinStatsPanelState
  ) => Array<{ label: string; values: number[] }>;
  streamBinStatsFitOverlayCurves: (
    panel: PlotStreamBinStatsPanelState
  ) => Array<{ label: string; x: number[]; y: number[] }>;
}

const PLOT_HEIGHT = 640;

export function ExpandedPlotBody({
  panel,
  resolveTelemetryPanelOffset,
  streamTraceOverlaySeries,
  streamBinStatsOverlaySeries,
  streamBinStatsFitOverlayCurves,
}: ExpandedPlotBodyProps) {
  const { plotTick } = usePlotTick();
  const {
    buffersRef,
    streamFramesRef,
    streamBinStatsRef,
    streamBin2dRef,
  } = useTelemetry();
  const { streamWorkspaces } = useStreamAnalysis();
  const computedColorScheme = useComputedColorScheme("light");

  if (isTelemetryPanel(panel)) {
    return (
      <PlotPanel
        traces={panel.traces}
        buffers={buffersRef.get(panel.id) ?? new Map()}
        tick={plotTick}
        timeWindowS={panel.timeWindowS}
        colorScheme={computedColorScheme}
        plotHeight={PLOT_HEIGHT}
        yScaleMode={panel.yScaleMode}
        yMin={panel.yMin}
        yMax={panel.yMax}
        yDisplayMode={panel.yDisplayMode}
        yOffset={resolveTelemetryPanelOffset(panel)}
        smoothingMode={panel.smoothingMode}
        smoothingWindowS={panel.smoothingWindowS}
      />
    );
  }
  if (isStreamTracePanel(panel)) {
    if (isStreamRawPanel(panel)) {
      return (
        <StreamRawPanel
          frames={streamFramesRef.get(panel.id) ?? []}
          overlayCount={panel.overlayCount}
          channelIndex={panel.sourceMode === "raw" ? panel.channelIndex : 0}
          tick={plotTick}
          colorScheme={computedColorScheme}
          plotHeight={PLOT_HEIGHT}
          units={panel.stream?.units ?? null}
          extraSeries={
            panel.sourceMode === "dag" ? streamTraceOverlaySeries(panel) : []
          }
          yScaleMode={panel.yScaleMode}
          yMin={panel.yMin}
          yMax={panel.yMax}
        />
      );
    }
    return (
      <StreamWaterfallPanel
        frames={streamFramesRef.get(panel.id) ?? []}
        historyRows={panel.overlayCount}
        channelIndex={panel.sourceMode === "raw" ? panel.channelIndex : 0}
        tick={plotTick}
        colorScheme={computedColorScheme}
        plotHeight={PLOT_HEIGHT}
        zScaleMode={panel.yScaleMode}
        zMin={panel.yMin}
        zMax={panel.yMax}
      />
    );
  }
  if (isStreamScalarPanel(panel)) {
    return (
      <PlotPanel
        traces={[streamScalarTrace(panel)]}
        buffers={buffersRef.get(panel.id) ?? new Map()}
        tick={plotTick}
        timeWindowS={panel.timeWindowS}
        colorScheme={computedColorScheme}
        plotHeight={PLOT_HEIGHT}
        yScaleMode={panel.yScaleMode}
        yMin={panel.yMin}
        yMax={panel.yMax}
      />
    );
  }
  if (isStreamBinStatsPanel(panel)) {
    const streamWorkspace = streamWorkspaces[panel.workspaceId] ?? null;
    return (
      <StreamBinStatsPanel
        series={(streamBinStatsRef.get(panel.id) ?? null)?.series ?? null}
        overlaySeries={streamBinStatsOverlaySeries(panel)}
        fitOverlays={streamBinStatsFitOverlayCurves(panel)}
        xLabel={workspaceXAxisLabel(streamWorkspace, panel.outputId)}
        uncertaintyMode={panel.uncertaintyMode}
        uncertaintyScale={panel.uncertaintyScale}
        showBinMarkers={panel.showBinMarkers}
        tick={plotTick}
        colorScheme={computedColorScheme}
        plotHeight={PLOT_HEIGHT}
        yScaleMode={panel.yScaleMode}
        yMin={panel.yMin}
        yMax={panel.yMax}
      />
    );
  }
  if (isStreamBin2dPanel(panel)) {
    return (
      <StreamBin2dPanel
        series={(streamBin2dRef.get(panel.id) ?? null)?.series ?? null}
        reducer={panel.reducer}
        tick={plotTick}
        colorScheme={computedColorScheme}
        plotHeight={PLOT_HEIGHT}
        zScaleMode={panel.yScaleMode}
        zMin={panel.yMin}
        zMax={panel.yMax}
      />
    );
  }
  return null;
}
