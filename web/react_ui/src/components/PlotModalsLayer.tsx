import { Modal } from "@mantine/core";
import type { ComponentProps, ReactNode } from "react";
import { StreamBin2dOptionsModal } from "./StreamBin2dOptionsModal";
import { StreamBinStatsOptionsModal } from "./StreamBinStatsOptionsModal";
import { StreamParamsOptionsModal } from "./StreamParamsOptionsModal";
import { StreamTraceOptionsModal } from "./StreamTraceOptionsModal";
import { YAxisModal } from "./YAxisModal";

type Props = {
  expandedPlotOpened: boolean;
  onCloseExpandedPlot: () => void;
  expandedPlotTitle: string;
  expandedPlotContent: ReactNode;
  yAxisOpened: boolean;
  onCloseYAxis: () => void;
  yAxisTitle: string;
  yAxisAutoRange: boolean;
  yAxisDraftMin: string;
  onYAxisDraftMinChange: (value: string) => void;
  yAxisDraftMax: string;
  onYAxisDraftMaxChange: (value: string) => void;
  yAxisDraftInvalid: boolean;
  onApplyYAxis: () => void;
  streamTraceOpened: boolean;
  onCloseStreamTrace: () => void;
  streamTracePanel: ComponentProps<typeof StreamTraceOptionsModal>["panel"];
  streamTargetOptions: ComponentProps<
    typeof StreamTraceOptionsModal
  >["streamTargetOptions"];
  streamWorkspaceOptions: ComponentProps<
    typeof StreamTraceOptionsModal
  >["streamWorkspaceOptions"];
  streamTraceOutputOptions: ComponentProps<
    typeof StreamTraceOptionsModal
  >["traceOutputOptions"];
  streamTraceOverlayOutputOptions: ComponentProps<
    typeof StreamTraceOptionsModal
  >["overlayTraceOutputOptions"];
  onSetStreamTraceSourceMode: ComponentProps<
    typeof StreamTraceOptionsModal
  >["onSetSourceMode"];
  onSetStreamPanelOverlayCount: ComponentProps<
    typeof StreamTraceOptionsModal
  >["onSetOverlayCount"];
  onSetStreamPanelRollingWindow: ComponentProps<
    typeof StreamTraceOptionsModal
  >["onSetRollingWindow"];
  onSetStreamPanelAverageMode: ComponentProps<
    typeof StreamTraceOptionsModal
  >["onSetAverageMode"];
  onSetStreamPanelTargetFromKey: ComponentProps<
    typeof StreamTraceOptionsModal
  >["onRawTargetKeyChange"];
  onSetStreamPanelChannelIndex: ComponentProps<
    typeof StreamTraceOptionsModal
  >["onSetChannelIndex"];
  onSetStreamTraceWorkspace: ComponentProps<
    typeof StreamTraceOptionsModal
  >["onSetWorkspace"];
  onSetStreamTraceOutput: ComponentProps<
    typeof StreamTraceOptionsModal
  >["onSetOutput"];
  onSetStreamTraceOverlayOutputs: ComponentProps<
    typeof StreamTraceOptionsModal
  >["onSetOverlayOutputs"];
  onSetStreamPanelTraceDecimator: ComponentProps<
    typeof StreamTraceOptionsModal
  >["onSetTraceDecimator"];
  onSetStreamPanelTraceMaxPoints: ComponentProps<
    typeof StreamTraceOptionsModal
  >["onSetTraceMaxPoints"];
  onSetStreamPanelTraceMaxFps: ComponentProps<
    typeof StreamTraceOptionsModal
  >["onSetTraceMaxFps"];
  streamBinStatsOpened: boolean;
  onCloseStreamBinStats: () => void;
  streamBinStatsPanel: ComponentProps<typeof StreamBinStatsOptionsModal>["panel"];
  streamBinStatsOutputOptions: ComponentProps<
    typeof StreamBinStatsOptionsModal
  >["outputOptions"];
  streamBinStatsTraceOverlayOptions: ComponentProps<
    typeof StreamBinStatsOptionsModal
  >["overlayTraceOutputOptions"];
  streamBinStatsFitOverlayOptions: ComponentProps<
    typeof StreamBinStatsOptionsModal
  >["fitOverlayOutputOptions"];
  streamBinStatsXAxisLabel: string;
  onSetStreamAnalysisPanelWorkspace: ComponentProps<
    typeof StreamBinStatsOptionsModal
  >["onSetWorkspace"];
  onSetStreamAnalysisPanelOutput: ComponentProps<
    typeof StreamBinStatsOptionsModal
  >["onSetOutput"];
  onSetStreamBinStatsOverlayOutputs: ComponentProps<
    typeof StreamBinStatsOptionsModal
  >["onSetOverlayOutputs"];
  onSetStreamBinStatsFitOverlayOutputs: ComponentProps<
    typeof StreamBinStatsOptionsModal
  >["onSetFitOverlayOutputs"];
  onSetStreamBinStatsUncertainty: ComponentProps<
    typeof StreamBinStatsOptionsModal
  >["onSetUncertainty"];
  onSetStreamBinStatsShowBinMarkers: ComponentProps<
    typeof StreamBinStatsOptionsModal
  >["onSetShowBinMarkers"];
  streamParamsOpened: boolean;
  onCloseStreamParams: () => void;
  streamParamsPanel: ComponentProps<typeof StreamParamsOptionsModal>["panel"];
  streamParamsOutputOptions: ComponentProps<
    typeof StreamParamsOptionsModal
  >["outputOptions"];
  onSetStreamParamsOutputs: ComponentProps<
    typeof StreamParamsOptionsModal
  >["onSetOutputs"];
  streamBin2dOpened: boolean;
  onCloseStreamBin2d: () => void;
  streamBin2dPanel: ComponentProps<typeof StreamBin2dOptionsModal>["panel"];
  streamBin2dOutputOptions: ComponentProps<
    typeof StreamBin2dOptionsModal
  >["outputOptions"];
  streamBin2dXAxisLabel: string;
  streamBin2dYAxisLabel: string;
  onSetStreamBin2dReducer: ComponentProps<
    typeof StreamBin2dOptionsModal
  >["onSetReducer"];
};

export function PlotModalsLayer({
  expandedPlotOpened,
  onCloseExpandedPlot,
  expandedPlotTitle,
  expandedPlotContent,
  yAxisOpened,
  onCloseYAxis,
  yAxisTitle,
  yAxisAutoRange,
  yAxisDraftMin,
  onYAxisDraftMinChange,
  yAxisDraftMax,
  onYAxisDraftMaxChange,
  yAxisDraftInvalid,
  onApplyYAxis,
  streamTraceOpened,
  onCloseStreamTrace,
  streamTracePanel,
  streamTargetOptions,
  streamWorkspaceOptions,
  streamTraceOutputOptions,
  streamTraceOverlayOutputOptions,
  onSetStreamTraceSourceMode,
  onSetStreamPanelOverlayCount,
  onSetStreamPanelRollingWindow,
  onSetStreamPanelAverageMode,
  onSetStreamPanelTargetFromKey,
  onSetStreamPanelChannelIndex,
  onSetStreamTraceWorkspace,
  onSetStreamTraceOutput,
  onSetStreamTraceOverlayOutputs,
  onSetStreamPanelTraceDecimator,
  onSetStreamPanelTraceMaxPoints,
  onSetStreamPanelTraceMaxFps,
  streamBinStatsOpened,
  onCloseStreamBinStats,
  streamBinStatsPanel,
  streamBinStatsOutputOptions,
  streamBinStatsTraceOverlayOptions,
  streamBinStatsFitOverlayOptions,
  streamBinStatsXAxisLabel,
  onSetStreamAnalysisPanelWorkspace,
  onSetStreamAnalysisPanelOutput,
  onSetStreamBinStatsOverlayOutputs,
  onSetStreamBinStatsFitOverlayOutputs,
  onSetStreamBinStatsUncertainty,
  onSetStreamBinStatsShowBinMarkers,
  streamParamsOpened,
  onCloseStreamParams,
  streamParamsPanel,
  streamParamsOutputOptions,
  onSetStreamParamsOutputs,
  streamBin2dOpened,
  onCloseStreamBin2d,
  streamBin2dPanel,
  streamBin2dOutputOptions,
  streamBin2dXAxisLabel,
  streamBin2dYAxisLabel,
  onSetStreamBin2dReducer,
}: Props) {
  return (
    <>
      <Modal
        opened={expandedPlotOpened}
        onClose={onCloseExpandedPlot}
        title={expandedPlotTitle}
        size="clamp(48rem, 92vw, 110rem)"
        centered
      >
        {expandedPlotContent}
      </Modal>

      <YAxisModal
        opened={yAxisOpened}
        onClose={onCloseYAxis}
        title={yAxisTitle}
        autoRange={yAxisAutoRange}
        draftMin={yAxisDraftMin}
        onDraftMinChange={onYAxisDraftMinChange}
        draftMax={yAxisDraftMax}
        onDraftMaxChange={onYAxisDraftMaxChange}
        draftInvalid={yAxisDraftInvalid}
        onApply={onApplyYAxis}
      />

      <StreamTraceOptionsModal
        opened={streamTraceOpened}
        onClose={onCloseStreamTrace}
        panel={streamTracePanel}
        streamTargetOptions={streamTargetOptions}
        streamWorkspaceOptions={streamWorkspaceOptions}
        traceOutputOptions={streamTraceOutputOptions}
        overlayTraceOutputOptions={streamTraceOverlayOutputOptions}
        onSetSourceMode={onSetStreamTraceSourceMode}
        onSetOverlayCount={onSetStreamPanelOverlayCount}
        onSetRollingWindow={onSetStreamPanelRollingWindow}
        onSetAverageMode={onSetStreamPanelAverageMode}
        onRawTargetKeyChange={onSetStreamPanelTargetFromKey}
        onSetChannelIndex={onSetStreamPanelChannelIndex}
        onSetWorkspace={onSetStreamTraceWorkspace}
        onSetOutput={onSetStreamTraceOutput}
        onSetOverlayOutputs={onSetStreamTraceOverlayOutputs}
        onSetTraceDecimator={onSetStreamPanelTraceDecimator}
        onSetTraceMaxPoints={onSetStreamPanelTraceMaxPoints}
        onSetTraceMaxFps={onSetStreamPanelTraceMaxFps}
      />

      <StreamBinStatsOptionsModal
        opened={streamBinStatsOpened}
        onClose={onCloseStreamBinStats}
        panel={streamBinStatsPanel}
        streamWorkspaceOptions={streamWorkspaceOptions}
        outputOptions={streamBinStatsOutputOptions}
        overlayTraceOutputOptions={streamBinStatsTraceOverlayOptions}
        fitOverlayOutputOptions={streamBinStatsFitOverlayOptions}
        xAxisLabel={streamBinStatsXAxisLabel}
        onSetWorkspace={onSetStreamAnalysisPanelWorkspace}
        onSetOutput={onSetStreamAnalysisPanelOutput}
        onSetOverlayOutputs={onSetStreamBinStatsOverlayOutputs}
        onSetFitOverlayOutputs={onSetStreamBinStatsFitOverlayOutputs}
        onSetUncertainty={onSetStreamBinStatsUncertainty}
        onSetShowBinMarkers={onSetStreamBinStatsShowBinMarkers}
      />

      <StreamParamsOptionsModal
        opened={streamParamsOpened}
        onClose={onCloseStreamParams}
        panel={streamParamsPanel}
        streamWorkspaceOptions={streamWorkspaceOptions}
        outputOptions={streamParamsOutputOptions}
        onSetWorkspace={onSetStreamAnalysisPanelWorkspace}
        onSetOutputs={onSetStreamParamsOutputs}
      />

      <StreamBin2dOptionsModal
        opened={streamBin2dOpened}
        onClose={onCloseStreamBin2d}
        panel={streamBin2dPanel}
        streamWorkspaceOptions={streamWorkspaceOptions}
        outputOptions={streamBin2dOutputOptions}
        xAxisLabel={streamBin2dXAxisLabel}
        yAxisLabel={streamBin2dYAxisLabel}
        onSetWorkspace={onSetStreamAnalysisPanelWorkspace}
        onSetOutput={onSetStreamAnalysisPanelOutput}
        onSetReducer={onSetStreamBin2dReducer}
      />
    </>
  );
}
