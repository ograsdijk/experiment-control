import type {
  ComponentProps,
  Dispatch,
  MutableRefObject,
  ReactNode,
  SetStateAction,
} from "react";
import type { GatewaySettingsInfo } from "../api";
import type { StreamAnalysisWorkspaceConfig } from "../features/stream/types";
import type { useCommandHistoryController } from "../features/commands/useCommandHistoryController";
import type { useHdfController } from "../features/hdf/useHdfController";
import type { useInterlocksController } from "../features/interlocks/useInterlocksController";
import type { useProcessCommandController } from "../features/processes/useProcessCommandController";
import type { useProcessesController } from "../features/processes/useProcessesController";
import type { useSequencerController } from "../features/sequencer/useSequencerController";
import type { CapabilityMember, DeviceStatus, LogEntry, StreamCatalogEntry } from "../types";
import type { TelemetrySignal } from "../types";
import { CommandHistoryModalContainer } from "./CommandHistoryModalContainer";
import { HdfModalsLayer } from "./HdfModalsLayer";
import { InterlocksModal } from "./InterlocksModal";
import { LogsModalContainer } from "./LogsModalContainer";
import { ProcessCommandModal } from "./ProcessCommandModal";
import { ProcessesModal } from "./ProcessesModal";
import { SequencerModalContainer } from "./SequencerModalContainer";
import { SettingsModal } from "./SettingsModal";

type HdfControllerState = ReturnType<typeof useHdfController>;
type ProcessesControllerState = ReturnType<typeof useProcessesController>;
type ProcessCommandControllerState = ReturnType<typeof useProcessCommandController>;
type InterlocksControllerState = ReturnType<typeof useInterlocksController>;
type SequencerControllerState = ReturnType<typeof useSequencerController>;
type CommandHistoryControllerState = ReturnType<typeof useCommandHistoryController>;

const MIN_COMMAND_HISTORY_LIMIT = 20;
const MAX_COMMAND_HISTORY_LIMIT = 2000;
const COMMAND_HISTORY_LIMIT_BOUNDS = {
  min: MIN_COMMAND_HISTORY_LIMIT,
  max: MAX_COMMAND_HISTORY_LIMIT,
} as const;

type Props = {
  hdf: HdfControllerState;
  renderMeasurementFieldInput: ComponentProps<
    typeof HdfModalsLayer
  >["renderMeasurementFieldInput"];
  processesController: ProcessesControllerState;
  processCommandController: ProcessCommandControllerState;
  onProcessAction: ComponentProps<typeof ProcessesModal>["onProcessAction"];
  settingsOpen: boolean;
  setSettingsOpen: Dispatch<SetStateAction<boolean>>;
  settingsFileInputRef: ComponentProps<typeof SettingsModal>["settingsFileInputRef"];
  onImportUiProfile: () => Promise<unknown> | void;
  onExportUiProfile: () => Promise<unknown> | void;
  onReloadSettings: () => Promise<unknown> | void;
  settingsLoading: boolean;
  settingsError: string | null;
  gatewaySettings: GatewaySettingsInfo | null;
  resolvedApiBase: string;
  resolvedWsBase: string;
  telemetryStreamStatus: string;
  devices: DeviceStatus[];
  streamCatalog: StreamCatalogEntry[];
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
  streamWorkspaces: Record<string, StreamAnalysisWorkspaceConfig>;
  latestSignalsByDevice: Record<string, Record<string, TelemetrySignal>>;
  interlocksController: InterlocksControllerState;
  sequencerController: SequencerControllerState;
  sequencerPrimaryIcon: ReactNode;
  sequencerProcessId: string | null;
  colorScheme: "light" | "dark";
  commandHistoryController: CommandHistoryControllerState;
  commandHistoryScrollRef: MutableRefObject<HTMLDivElement | null>;
  copyJsonToClipboard: (label: string, payload: unknown) => Promise<unknown> | void;
  logsOpen: boolean;
  setLogsOpen: Dispatch<SetStateAction<boolean>>;
  logsWsConnected: boolean;
  filteredLogRows: LogEntry[];
  logRows: LogEntry[];
  logAutoScroll: boolean;
  setLogAutoScroll: Dispatch<SetStateAction<boolean>>;
  logLoading: boolean;
  loadLogTail: () => Promise<unknown> | void;
  logSeenRef: MutableRefObject<Set<string>>;
  setLogRows: Dispatch<SetStateAction<LogEntry[]>>;
  setExpandedLogByKey: Dispatch<SetStateAction<Record<string, boolean>>>;
  logSeverityFilter: string;
  setLogSeverityFilter: Dispatch<SetStateAction<string>>;
  logSourceFilter: string;
  setLogSourceFilter: Dispatch<SetStateAction<string>>;
  logDeviceFilter: string;
  setLogDeviceFilter: Dispatch<SetStateAction<string>>;
  logProcessFilter: string;
  setLogProcessFilter: Dispatch<SetStateAction<string>>;
  logTextFilter: string;
  setLogTextFilter: Dispatch<SetStateAction<string>>;
  logScrollRef: MutableRefObject<HTMLDivElement | null>;
  expandedLogByKey: Record<string, boolean>;
  copyTextToClipboard: (label: string, value: string) => Promise<unknown> | void;
};

export function AppModalsLayer({
  hdf,
  renderMeasurementFieldInput,
  processesController,
  processCommandController,
  onProcessAction,
  settingsOpen,
  setSettingsOpen,
  settingsFileInputRef,
  onImportUiProfile,
  onExportUiProfile,
  onReloadSettings,
  settingsLoading,
  settingsError,
  gatewaySettings,
  resolvedApiBase,
  resolvedWsBase,
  telemetryStreamStatus,
  devices,
  streamCatalog,
  capabilitiesByDevice,
  streamWorkspaces,
  latestSignalsByDevice,
  interlocksController,
  sequencerController,
  sequencerPrimaryIcon,
  sequencerProcessId,
  colorScheme,
  commandHistoryController,
  commandHistoryScrollRef,
  copyJsonToClipboard,
  logsOpen,
  setLogsOpen,
  logsWsConnected,
  filteredLogRows,
  logRows,
  logAutoScroll,
  setLogAutoScroll,
  logLoading,
  loadLogTail,
  logSeenRef,
  setLogRows,
  setExpandedLogByKey,
  logSeverityFilter,
  setLogSeverityFilter,
  logSourceFilter,
  setLogSourceFilter,
  logDeviceFilter,
  setLogDeviceFilter,
  logProcessFilter,
  setLogProcessFilter,
  logTextFilter,
  setLogTextFilter,
  logScrollRef,
  expandedLogByKey,
  copyTextToClipboard,
}: Props) {
  return (
    <>
      <HdfModalsLayer
        hdf={hdf}
        renderMeasurementFieldInput={renderMeasurementFieldInput}
      />

      <ProcessCommandModal
        opened={processCommandController.processCommandOpen}
        onClose={() => processCommandController.setProcessCommandOpen(false)}
        title={processCommandController.processCommandTitle}
        capabilities={processCommandController.capabilitiesForProcessCommand}
        commandAction={processCommandController.processCommandAction}
        onActionChange={processCommandController.handleProcessCommandActionChange}
        showAdvancedParams={processCommandController.processShowAdvancedParams}
        onShowAdvancedParamsChange={
          processCommandController.setProcessShowAdvancedParams
        }
        activeParams={processCommandController.activeProcessParams}
        commandParamValues={processCommandController.processCommandParamValues}
        onParamValueChange={(name, value) =>
          processCommandController.setProcessCommandParamValues((prev) => ({
            ...prev,
            [name]: value,
          }))
        }
        commandParams={processCommandController.processCommandParams}
        onCommandParamsChange={processCommandController.setProcessCommandParams}
        onExecute={processCommandController.executeProcessCommand}
      />

      <ProcessesModal
        opened={processesController.processOpen}
        onClose={() => processesController.setProcessOpen(false)}
        processes={processesController.processes}
        capabilitiesByProcess={processesController.capabilitiesByProcess}
        busyByProcess={processesController.processBusyById}
        errorByProcess={processesController.processCapabilitiesErrorById}
        onRefresh={processesController.refreshProcesses}
        onProcessAction={onProcessAction}
        onOpenCommand={processCommandController.openProcessCommand}
      />

      <SettingsModal
        opened={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        settingsFileInputRef={settingsFileInputRef}
        onImportUiProfile={onImportUiProfile}
        onExportUiProfile={onExportUiProfile}
        onReload={onReloadSettings}
        loading={settingsLoading}
        error={settingsError}
        gatewaySettings={gatewaySettings}
        resolvedApiBase={resolvedApiBase}
        resolvedWsBase={resolvedWsBase}
        telemetryStreamStatus={telemetryStreamStatus}
      />

      <InterlocksModal
        opened={interlocksController.interlocksOpen}
        onClose={() => interlocksController.setInterlocksOpen(false)}
        onRefresh={interlocksController.refreshInterlocksModalData}
        devices={devices}
        processes={interlocksController.interlocksPanelProcesses}
        followerRulesByProcessId={interlocksController.followerRulesByProcessId}
        interlockStatusByProcessId={interlocksController.interlockStatusByProcessId}
        interlocksLoadingByProcessId={
          interlocksController.interlocksLoadingByProcessId
        }
        interlocksErrorByProcessId={interlocksController.interlocksErrorByProcessId}
        interlockRuleBusyByKey={interlocksController.interlockRuleBusyByKey}
        commandInterceptorRoutes={interlocksController.commandInterceptorRoutes}
        commandInterceptorRoutesLoading={
          interlocksController.commandInterceptorRoutesLoading
        }
        commandInterceptorRoutesError={
          interlocksController.commandInterceptorRoutesError
        }
        onRefreshProcess={(processId) =>
          interlocksController.refreshInterlockProcessStatus(processId, undefined, {
            showLoading: true,
          })
        }
        onToggleFollowerRule={interlocksController.toggleFollowerRule}
        onToggleInterlockRule={interlocksController.toggleInterlockRule}
      />

      <SequencerModalContainer
        opened={sequencerController.sequencerOpen}
        onClose={() => sequencerController.setSequencerOpen(false)}
        processState={sequencerController.sequencerProcessState}
        runtimeState={sequencerController.sequencerRuntimeState}
        loaded={sequencerController.sequencerLoaded}
        currentStep={sequencerController.sequencerStatus?.currentStep ?? null}
        progress={sequencerController.sequencerProgress}
        progressPercent={sequencerController.sequencerProgressPercent}
        totalSteps={sequencerController.sequencerTotalSteps}
        completedSteps={sequencerController.sequencerCompletedSteps}
        loadedSource={sequencerController.sequencerStatus?.loadedSource ?? null}
        autoloadError={sequencerController.sequencerStatus?.autoloadError ?? null}
        statusError={sequencerController.sequencerStatus?.error ?? null}
        modalError={sequencerController.sequencerModalError}
        primaryIcon={sequencerPrimaryIcon}
        primaryAction={sequencerController.sequencerPrimaryAction}
        primaryLabel={sequencerController.sequencerPrimaryLabel}
        primaryDisabled={sequencerController.sequencerPrimaryDisabled}
        actionBusy={sequencerController.sequencerActionBusy}
        runMode={sequencerController.sequencerRunMode}
        repeatCount={sequencerController.sequencerRepeatCount}
        onRunModeChange={sequencerController.setSequencerRunMode}
        onRepeatCountChange={sequencerController.setSequencerRepeatCount}
        libraryConfigured={sequencerController.sequencerLibraryConfigured}
        libraryEntries={sequencerController.sequencerLibraryEntries}
        libraryLoading={sequencerController.sequencerLibraryLoading}
        libraryError={sequencerController.sequencerLibraryError}
        selectedSequenceId={sequencerController.sequencerSelectedSequenceId}
        onSelectedSequenceIdChange={sequencerController.setSequencerSelectedSequenceId}
        onReloadLibrary={sequencerController.reloadSequencerLibrary}
        overrideRows={sequencerController.sequencerOverrideRows}
        overrideVarOptions={sequencerController.sequencerOverrideVarOptions}
        overrideErrors={sequencerController.sequencerOverrideErrors}
        overridePreview={sequencerController.sequencerOverridePreview}
        overridesValid={sequencerController.sequencerOverridesValid}
        onAddOverrideRow={sequencerController.addSequencerOverrideRow}
        onRemoveOverrideRow={sequencerController.removeSequencerOverrideRow}
        onUpdateOverrideRow={sequencerController.updateSequencerOverrideRow}
        onClearOverrides={sequencerController.clearSequencerOverrides}
        adaptiveModes={sequencerController.sequencerAdaptiveModes}
        adaptiveStudies={sequencerController.sequencerStatus?.adaptiveStudies ?? {}}
        loadedAdaptiveIds={sequencerController.sequencerStatus?.loadedAdaptiveIds ?? []}
        adaptiveClearBusyStudyId={sequencerController.sequencerAdaptiveClearBusy}
        onRunAction={sequencerController.runSequencerAction}
        onAdaptiveModeChange={sequencerController.setAdaptiveMode}
        onClearAdaptiveStudy={sequencerController.clearAdaptiveStudy}
        fileInputRef={sequencerController.sequencerFileInputRef}
        onFileInputChange={sequencerController.handleSequencerFileInput}
        yamlViewMode={sequencerController.sequencerYamlViewMode}
        onYamlViewModeChange={sequencerController.setSequencerYamlViewMode}
        loadedYamlBusy={sequencerController.sequencerLoadedYamlBusy}
        sequencerProcessId={sequencerProcessId}
        onShowLoadedYaml={sequencerController.fetchSequencerLoadedYaml}
        validateBusy={sequencerController.sequencerValidateBusy}
        onValidate={sequencerController.validateSequencerYaml}
        loadBusy={sequencerController.sequencerLoadBusy}
        onLoad={sequencerController.loadSequencerYaml}
        editorRef={sequencerController.sequencerEditorRef}
        yamlText={sequencerController.sequencerYamlText}
        onYamlTextChange={sequencerController.onSequencerYamlTextChange}
        streamCatalog={streamCatalog}
        capabilitiesByDevice={capabilitiesByDevice}
        streamWorkspaces={streamWorkspaces}
        latestSignalsByDevice={latestSignalsByDevice}
        colorScheme={colorScheme}
        diagnostics={sequencerController.sequencerDiagnostics}
        onJumpToDiagnostic={sequencerController.jumpToSequencerDiagnostic}
      />

      <CommandHistoryModalContainer
        opened={commandHistoryController.commandHistoryOpen}
        onClose={() => commandHistoryController.setCommandHistoryOpen(false)}
        filteredRows={commandHistoryController.filteredCommandHistoryRows}
        devices={devices}
        totalRows={commandHistoryController.commandHistoryRows.length}
        persistLimit={commandHistoryController.commandHistoryLimit}
        persistLimitMin={MIN_COMMAND_HISTORY_LIMIT}
        persistLimitMax={MAX_COMMAND_HISTORY_LIMIT}
        persistLimitBounds={COMMAND_HISTORY_LIMIT_BOUNDS}
        onPersistLimitChange={commandHistoryController.setCommandHistoryLimit}
        autoScroll={commandHistoryController.commandHistoryAutoScroll}
        onAutoScrollChange={commandHistoryController.setCommandHistoryAutoScroll}
        onClear={() => commandHistoryController.setCommandHistoryRows([])}
        targetFilter={commandHistoryController.commandHistoryTargetFilter}
        onTargetFilterChange={commandHistoryController.setCommandHistoryTargetFilter}
        statusFilter={commandHistoryController.commandHistoryStatusFilter}
        onStatusFilterChange={commandHistoryController.setCommandHistoryStatusFilter}
        sourceFilter={commandHistoryController.commandHistorySourceFilter}
        onSourceFilterChange={commandHistoryController.setCommandHistorySourceFilter}
        sourceOptions={commandHistoryController.commandHistorySourceOptions}
        textFilter={commandHistoryController.commandHistoryTextFilter}
        onTextFilterChange={commandHistoryController.setCommandHistoryTextFilter}
        viewportRef={commandHistoryScrollRef}
        onCopyJson={(label, payload) => {
          void copyJsonToClipboard(label, payload);
        }}
      />

      <LogsModalContainer
        opened={logsOpen}
        onClose={() => setLogsOpen(false)}
        connected={logsWsConnected}
        filteredRows={filteredLogRows}
        totalRows={logRows.length}
        autoScroll={logAutoScroll}
        onAutoScrollChange={setLogAutoScroll}
        loading={logLoading}
        loadLogTail={loadLogTail}
        logSeenRef={logSeenRef}
        setLogRows={setLogRows}
        setExpandedLogByKey={setExpandedLogByKey}
        severityFilter={logSeverityFilter}
        onSeverityFilterChange={setLogSeverityFilter}
        sourceFilter={logSourceFilter}
        onSourceFilterChange={setLogSourceFilter}
        deviceFilter={logDeviceFilter}
        onDeviceFilterChange={setLogDeviceFilter}
        processFilter={logProcessFilter}
        onProcessFilterChange={setLogProcessFilter}
        textFilter={logTextFilter}
        onTextFilterChange={setLogTextFilter}
        devices={devices}
        processes={processesController.processes}
        viewportRef={logScrollRef}
        expandedByKey={expandedLogByKey}
        copyTextToClipboard={copyTextToClipboard}
      />
    </>
  );
}
