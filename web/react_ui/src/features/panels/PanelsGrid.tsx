import { type CSSProperties, type MutableRefObject } from "react";
import { SortableContext, rectSortingStrategy } from "@dnd-kit/sortable";

import type {
  PlotPanelState,
  PlotStreamBinStatsPanelState,
  PlotStreamPanelState,
  PlotStreamWaterfallPanelState,
  PlotTelemetryPanelState,
  TelemetrySmoothingMode,
  YDisplayMode,
  YOffsetMode,
  YScaleMode,
} from "../stream/types";
import { useLayout } from "../layout/LayoutContext";
import { PanelCard } from "./PanelCard";
import { usePanels } from "./PanelsContext";

/**
 * Panel render loop — `<PanelsGrid>` owns the SortableContext wrapper
 * and iterates `panels`, rendering one `<PanelCard>` per entry. Each
 * card owns its own `<ReorderableCardShell>` containing the title
 * bar, plot-options popover, per-kind body, and clear/expand/remove
 * actions.
 *
 * App-side handlers and overlay helpers come in via `helpers` and
 * `handlers` prop bags that PanelsGrid passes straight through to
 * each PanelCard.
 */

const PANEL_SORTABLE_PREFIX = "panel:";
function panelSortableId(panelId: string): string {
  return `${PANEL_SORTABLE_PREFIX}${panelId}`;
}

export interface PanelsGridHelpers {
  resolveTelemetryPanelOffset: (
    panel: PlotTelemetryPanelState
  ) => number | null;
  streamTraceOverlaySeries: (
    panel: PlotStreamPanelState | PlotStreamWaterfallPanelState
  ) => Array<{ label: string; values: number[] }>;
  streamExtraChannelSeries: (
    panel: PlotStreamPanelState | PlotStreamWaterfallPanelState
  ) => Array<{ label: string; values: number[] }>;
  streamBinStatsOverlaySeries: (
    panel: PlotStreamBinStatsPanelState
  ) => Array<{ label: string; values: number[] }>;
  streamBinStatsFitOverlayCurves: (
    panel: PlotStreamBinStatsPanelState
  ) => Array<{ label: string; x: number[]; y: number[] }>;
  isExpandablePlotPanel: (panel: PlotPanelState) => boolean;
  copyTextToClipboard: (label: string, text: string) => Promise<void>;
}

export interface PanelsGridHandlers {
  startPanelTitleEdit: (panel: PlotPanelState) => void;
  commitPanelTitleEdit: () => void;
  cancelPanelTitleEdit: () => void;
  removePanel: (panelId: string) => void;
  removeTraceFromPanel: (
    panelId: string,
    trace: { deviceId: string; signal: string }
  ) => void;
  setPanelTimeWindow: (panelId: string, value: number) => void;
  openPlotOptions: (panelId: string) => void;
  closePlotOptions: () => void;
  applyPlotOptionsAxis: (panelId: string) => void;
  setPlotOptionsAxisMode: (panel: PlotPanelState, mode: YScaleMode) => void;
  setTelemetryYDisplayMode: (panelId: string, mode: YDisplayMode) => void;
  setTelemetryYOffsetMode: (
    panelId: string,
    mode: YOffsetMode,
    value?: number | null
  ) => void;
  setTelemetrySmoothingMode: (
    panelId: string,
    mode: TelemetrySmoothingMode
  ) => void;
  setTelemetrySmoothingWindow: (panelId: string, value: number) => void;
  clearPanelBuffers: (panelId: string) => void;
  clearStreamPanelFrames: (panelId: string) => void;
  clearStreamBinStatsPanel: (panelId: string) => Promise<void>;
  clearStreamBin2dPanel: (panelId: string) => Promise<void>;
  setStreamAnalysisPanelWorkspace: (
    panelId: string,
    workspaceId: string | null
  ) => void;
  setStreamAnalysisPanelOutput: (
    panelId: string,
    outputId: string | null
  ) => void;
  openExpandedPlot: (panelId: string) => void;
  openStreamTraceOptionsModal: (panelId: string) => void;
  openStreamBin2dOptionsModal: (panelId: string) => void;
  openStreamParamsOptionsModal: (panelId: string) => void;
  openStreamBinStatsOptionsModal: (panelId: string) => void;
}

export interface PanelsGridProps {
  streamWorkspaceOptions: Array<{ value: string; label: string }>;
  yAxisDraftInvalid: boolean;
  streamWsConnected: boolean;
  streamAnalysisWsConnected: boolean;
  activeUiDrag: { kind: string; panelId?: string } | null;
  helpers: PanelsGridHelpers;
  handlers: PanelsGridHandlers;
}

export function PanelsGrid({
  streamWorkspaceOptions,
  yAxisDraftInvalid,
  streamWsConnected,
  streamAnalysisWsConnected,
  activeUiDrag,
  helpers,
  handlers,
}: PanelsGridProps) {
  const { panels } = usePanels();
  const { plotGridStyle, plotGridRef } = useLayout();

  return (
    <SortableContext
      items={panels.map((panel) => panelSortableId(panel.id))}
      strategy={rectSortingStrategy}
    >
      <div
        className="plot-grid"
        style={plotGridStyle as CSSProperties}
        ref={plotGridRef as MutableRefObject<HTMLDivElement | null>}
      >
        {panels.map((panel) => (
          <PanelCard
            key={panel.id}
            panel={panel}
            streamWorkspaceOptions={streamWorkspaceOptions}
            yAxisDraftInvalid={yAxisDraftInvalid}
            streamWsConnected={streamWsConnected}
            streamAnalysisWsConnected={streamAnalysisWsConnected}
            activeUiDrag={activeUiDrag}
            helpers={helpers}
            handlers={handlers}
          />
        ))}
      </div>
    </SortableContext>
  );
}
