import {
  isStreamBin2dPanel,
  isStreamBinStatsPanel,
  isStreamParamsPanel,
  isStreamTracePanel,
} from "../stream/panel_helpers";
import type {
  PlotPanelState,
  TelemetrySmoothingMode,
  YDisplayMode,
  YScaleMode,
} from "../stream/types";
import type { Bin2dReducer } from "../../components/StreamBin2dPanel";
import type { UncertaintyMode } from "../../components/StreamBinStatsPanel";
import {
  normalizeTelemetrySmoothingMode,
  normalizeTelemetrySmoothingWindow,
} from "../stream/utils";
import { isTelemetryPanel } from "../stream/panel_helpers";
import { usePanels } from "./PanelsContext";
import { usePlotTick } from "./PlotTickContext";

/**
 * Panel UI / Y-axis / modal-toggle handlers.
 *
 * This hook collects the "simple" mutation handlers — the ones that
 * only need PanelsContext (no telemetry refs, no DAQ workspace
 * coupling). App.tsx historically defined ~15 of these inline; they're
 * all small setPanels-wrappers or setXxxPanelId-toggle calls.
 *
 * **What's in this hook** (~15 handlers, all pure PanelsContext
 * mutations):
 *
 * - Plot Y-scale: `setPanelYScaleMode`, `setPanelManualYRange`.
 * - Telemetry display + smoothing: `setTelemetryYDisplayMode`,
 *   `setTelemetrySmoothingMode`, `setTelemetrySmoothingWindow`.
 * - Stream-binstats display: `setStreamBinStatsUncertainty`,
 *   `setStreamBinStatsShowBinMarkers`.
 * - Stream-bin2d display: `setStreamBin2dReducer`.
 * - Expand modal toggles: `openExpandedPlot`, `closeExpandedPlot`,
 *   `isExpandablePlotPanel`.
 * - Stream-options modal toggles (4 pairs): `openStreamTraceOptionsModal`,
 *   `closeStreamTraceOptionsModal`, `openStreamBinStatsOptionsModal`,
 *   `closeStreamBinStatsOptionsModal`, `openStreamParamsOptionsModal`,
 *   `closeStreamParamsOptionsModal`, `openStreamBin2dOptionsModal`,
 *   `closeStreamBin2dOptionsModal`.
 *
 * **What's deferred to follow-up rounds**:
 *
 * - Y-axis range editor handlers (`openPlotOptions`, `closePlotOptions`,
 *   `applyPlotOptionsAxis`, `setPlotOptionsAxisMode`,
 *   `setTelemetryYOffsetMode`, plus `resolveTelemetryPanelOffset` /
 *   `resolvePanelAutoYRange` helpers) — they need TelemetryContext
 *   refs + several auto-range compute helpers; cleaner as a separate
 *   `usePanelAutoRangeHandlers` extraction in the next round.
 * - Panel lifecycle (`createPanel`, `removePanel`, `addTraceToPanel`,
 *   `removeTraceFromPanel`) — touches PanelsContext +
 *   StreamAnalysisContext + TelemetryContext + latestByDevice; needs
 *   the most careful refactor.
 * - Stream-config (`setStreamTracePanelSourceMode/Workspace/Output`,
 *   `setStreamAnalysisPanelWorkspace/Output`,
 *   `setStreamParamsPanelOutputs`, `setStreamBinStatsOverlayOutputs`,
 *   `setStreamBinStatsFitOverlayOutputs`, the various
 *   `setStreamPanelXxx` decimator/fps/window setters) — cross-coupled
 *   to StreamAnalysisContext + buffer refs.
 *
 * No args needed; all state comes from `usePanels()` internally.
 */
export function usePanelUiHandlers() {
  const {
    setPanels,
    setExpandedPlotPanelId,
    setStreamTraceOptionsPanelId,
    setStreamBinStatsOptionsPanelId,
    setStreamParamsOptionsPanelId,
    setStreamBin2dOptionsPanelId,    panels,
  } = usePanels();
  const { setPlotTick } = usePlotTick();

  const setPanelYScaleMode = (panelId: string, mode: YScaleMode) => {
    setPanels((prev) =>
      prev.map((panel) => {
        if (panel.id !== panelId) {
          return panel;
        }
        if (mode === "auto") {
          return { ...panel, yScaleMode: "auto", yMin: null, yMax: null };
        }
        // PlotStreamParamsPanelState lacks yMin/yMax; narrow with `in`
        // so TS accepts the field reads. Same shape as App.tsx's
        // previous inline handler (which compiled under looser TS).
        const hasY = "yMin" in panel && "yMax" in panel;
        const nextMin = (hasY ? (panel as { yMin: number | null }).yMin : null) ?? 0;
        const nextMax = (hasY ? (panel as { yMax: number | null }).yMax : null) ?? (nextMin + 1);
        return {
          ...panel,
          yScaleMode: "manual",
          yMin: nextMin,
          yMax: nextMax > nextMin ? nextMax : nextMin + 1,
        };
      })
    );
  };

  const setPanelManualYRange = (panelId: string, min: number, max: number) => {
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId
          ? { ...panel, yScaleMode: "manual", yMin: min, yMax: max }
          : panel
      )
    );
  };

  const setTelemetryYDisplayMode = (panelId: string, mode: YDisplayMode) => {
    setPanels((prev) =>
      prev.map((panel) => {
        if (panel.id !== panelId || !isTelemetryPanel(panel)) {
          return panel;
        }
        if (mode === "absolute") {
          return { ...panel, yDisplayMode: "absolute" };
        }
        return { ...panel, yDisplayMode: "delta" };
      })
    );
  };

  const setTelemetrySmoothingMode = (
    panelId: string,
    mode: TelemetrySmoothingMode
  ) => {
    const nextMode = normalizeTelemetrySmoothingMode(mode);
    setPanels((prev) =>
      prev.map((panel) => {
        if (panel.id !== panelId || !isTelemetryPanel(panel)) {
          return panel;
        }
        return {
          ...panel,
          smoothingMode: nextMode,
          smoothingWindowS: normalizeTelemetrySmoothingWindow(panel.smoothingWindowS),
        };
      })
    );
  };

  const setTelemetrySmoothingWindow = (panelId: string, value: number) => {
    setPanels((prev) =>
      prev.map((panel) => {
        if (panel.id !== panelId || !isTelemetryPanel(panel)) {
          return panel;
        }
        return {
          ...panel,
          smoothingWindowS: normalizeTelemetrySmoothingWindow(value),
        };
      })
    );
  };

  const setStreamBinStatsUncertainty = (
    panelId: string,
    uncertaintyMode: UncertaintyMode,
    uncertaintyScale: number
  ) => {
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamBinStatsPanel(panel)
          ? {
              ...panel,
              uncertaintyMode,
              uncertaintyScale: Number.isFinite(uncertaintyScale)
                ? Math.max(0, uncertaintyScale)
                : panel.uncertaintyScale,
            }
          : panel
      )
    );
  };

  const setStreamBinStatsShowBinMarkers = (
    panelId: string,
    showBinMarkers: boolean
  ) => {
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamBinStatsPanel(panel)
          ? { ...panel, showBinMarkers }
          : panel
      )
    );
  };

  const setStreamBin2dReducer = (panelId: string, reducer: Bin2dReducer) => {
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamBin2dPanel(panel)
          ? { ...panel, reducer }
          : panel
      )
    );
    setPlotTick((tick) => tick + 1);
  };

  const isExpandablePlotPanel = (panel: PlotPanelState) =>
    !isStreamParamsPanel(panel);

  const openExpandedPlot = (panelId: string) => {
    const panel = panels.find((entry) => entry.id === panelId);
    if (!panel || !isExpandablePlotPanel(panel)) {
      return;
    }
    setExpandedPlotPanelId(panelId);
  };

  const closeExpandedPlot = () => {
    setExpandedPlotPanelId(null);
  };

  const openStreamTraceOptionsModal = (panelId: string) => {
    const panel = panels.find((entry) => entry.id === panelId);
    if (!panel || !isStreamTracePanel(panel)) {
      return;
    }
    setStreamTraceOptionsPanelId(panelId);
  };

  const closeStreamTraceOptionsModal = () => {
    setStreamTraceOptionsPanelId(null);
  };

  const openStreamBinStatsOptionsModal = (panelId: string) => {
    const panel = panels.find((entry) => entry.id === panelId);
    if (!panel || !isStreamBinStatsPanel(panel)) {
      return;
    }
    setStreamBinStatsOptionsPanelId(panelId);
  };

  const closeStreamBinStatsOptionsModal = () => {
    setStreamBinStatsOptionsPanelId(null);
  };

  const openStreamParamsOptionsModal = (panelId: string) => {
    const panel = panels.find((entry) => entry.id === panelId);
    if (!panel || !isStreamParamsPanel(panel)) {
      return;
    }
    setStreamParamsOptionsPanelId(panelId);
  };

  const closeStreamParamsOptionsModal = () => {
    setStreamParamsOptionsPanelId(null);
  };

  const openStreamBin2dOptionsModal = (panelId: string) => {
    const panel = panels.find((entry) => entry.id === panelId);
    if (!panel || !isStreamBin2dPanel(panel)) {
      return;
    }
    setStreamBin2dOptionsPanelId(panelId);
  };

  const closeStreamBin2dOptionsModal = () => {
    setStreamBin2dOptionsPanelId(null);
  };

  return {
    setPanelYScaleMode,
    setPanelManualYRange,
    setTelemetryYDisplayMode,
    setTelemetrySmoothingMode,
    setTelemetrySmoothingWindow,
    setStreamBinStatsUncertainty,
    setStreamBinStatsShowBinMarkers,
    setStreamBin2dReducer,
    isExpandablePlotPanel,
    openExpandedPlot,
    closeExpandedPlot,
    openStreamTraceOptionsModal,
    closeStreamTraceOptionsModal,
    openStreamBinStatsOptionsModal,
    closeStreamBinStatsOptionsModal,
    openStreamParamsOptionsModal,
    closeStreamParamsOptionsModal,
    openStreamBin2dOptionsModal,
    closeStreamBin2dOptionsModal,
  };
}
