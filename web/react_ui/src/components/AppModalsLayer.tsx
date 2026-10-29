import { IconPlayerPause, IconPlayerPlay } from "@tabler/icons-react";
import { lazy, Suspense } from "react";
import type {
  ComponentProps,
  Dispatch,
  MutableRefObject,
  SetStateAction,
} from "react";
import type { GatewaySettingsInfo } from "../api";
import type { StreamAnalysisWorkspaceConfig } from "../features/stream/types";
import type { useCommandHistoryController } from "../features/commands/useCommandHistoryController";
import type { useHdfController } from "../features/hdf/useHdfController";
import type { useInfluxController } from "../features/influx/useInfluxController";
import type { useInterlocksController } from "../features/interlocks/useInterlocksController";
import type { useWatchdogsController } from "../features/watchdogs/useWatchdogsController";
import type { useStateMachinesController } from "../features/state_machines/useStateMachinesController";
import type { useProcessCommandController } from "../features/processes/useProcessCommandController";
import type { useProcessesController } from "../features/processes/useProcessesController";
import { useSequencerContext } from "../features/sequencer/SequencerContext";
import type { SequencerAdaptiveStudyStatus } from "../features/sequencer/types";
import type { CapabilityMember, DeviceStatus, StreamCatalogEntry } from "../types";
import type { TelemetrySignal } from "../types";
import type { HdfMeasurementNoteModal } from "./HdfMeasurementNoteModal";

const CommandHistoryModalContainer = lazy(() =>
  import("./CommandHistoryModalContainer").then((module) => ({
    default: module.CommandHistoryModalContainer,
  }))
);
const HdfModalsLayer = lazy(() =>
  import("./HdfModalsLayer").then((module) => ({ default: module.HdfModalsLayer }))
);
const InfluxWriterModal = lazy(() =>
  import("./InfluxWriterModal").then((module) => ({
    default: module.InfluxWriterModal,
  }))
);
const LogsModalContainer = lazy(() =>
  import("./LogsModalContainer").then((module) => ({
    default: module.LogsModalContainer,
  }))
);
const ProcessCommandModal = lazy(() =>
  import("./ProcessCommandModal").then((module) => ({
    default: module.ProcessCommandModal,
  }))
);
const ProcessesModal = lazy(() =>
  import("./ProcessesModal").then((module) => ({ default: module.ProcessesModal }))
);
const SafetyModal = lazy(() =>
  import("./SafetyModal").then((module) => ({ default: module.SafetyModal }))
);
const StateMachinesModal = lazy(() =>
  import("./StateMachinesModal").then((module) => ({
    default: module.StateMachinesModal,
  }))
);
const SequencerModalContainer = lazy(() =>
  import("./SequencerModalContainer").then((module) => ({
    default: module.SequencerModalContainer,
  }))
);
const SettingsModal = lazy(() =>
  import("./SettingsModal").then((module) => ({ default: module.SettingsModal }))
);

type HdfControllerState = ReturnType<typeof useHdfController>;
type InfluxControllerState = ReturnType<typeof useInfluxController>;
type ProcessesControllerState = ReturnType<typeof useProcessesController>;
type ProcessCommandControllerState = ReturnType<typeof useProcessCommandController>;
type InterlocksControllerState = ReturnType<typeof useInterlocksController>;
type WatchdogsControllerState = ReturnType<typeof useWatchdogsController>;
type StateMachinesControllerState = ReturnType<typeof useStateMachinesController>;
type CommandHistoryControllerState = ReturnType<typeof useCommandHistoryController>;

type Props = {
  hdf: HdfControllerState;
  influx: InfluxControllerState;
  renderMeasurementFieldInput: ComponentProps<
    typeof HdfMeasurementNoteModal
  >["renderMeasurementFieldInput"];
  processesController: ProcessesControllerState;
  processCommandController: ProcessCommandControllerState;
  processCommandDeckDisabled: boolean;
  onAddProcessCommandToDeck: () => void;
  onProcessAction: ComponentProps<typeof ProcessesModal>["onProcessAction"];
  settingsOpen: boolean;
  setSettingsOpen: Dispatch<SetStateAction<boolean>>;
  settingsFileInputRef: ComponentProps<typeof SettingsModal>["settingsFileInputRef"];
  onImportUiProfile: ComponentProps<typeof SettingsModal>["onImportUiProfile"];
  onExportUiProfile: () => Promise<unknown> | void;
  onLoadDefaultUiProfile: () => Promise<boolean>;
  defaultUiProfileAvailable: boolean;
  defaultUiProfileLoading: boolean;
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
  watchdogsController: WatchdogsControllerState;
  stateMachinesController: StateMachinesControllerState;
  sequencerProcessId: string | null;
  colorScheme: "light" | "dark";
  commandHistoryController: CommandHistoryControllerState;
  commandHistoryScrollRef: MutableRefObject<HTMLDivElement | null>;
  copyJsonToClipboard: (label: string, payload: unknown) => Promise<unknown> | void;
  logsOpen: boolean;
  setLogsOpen: Dispatch<SetStateAction<boolean>>;
  logsWsConnected: boolean;
  logAutoScroll: boolean;
  setLogAutoScroll: Dispatch<SetStateAction<boolean>>;
  logSortNewestFirst: boolean;
  setLogSortNewestFirst: Dispatch<SetStateAction<boolean>>;
  logLoading: boolean;
  loadLogTail: () => Promise<unknown> | void;
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
  influx,
  renderMeasurementFieldInput,
  processesController,
  processCommandController,
  processCommandDeckDisabled,
  onAddProcessCommandToDeck,
  onProcessAction,
  settingsOpen,
  setSettingsOpen,
  settingsFileInputRef,
  onImportUiProfile,
  onExportUiProfile,
  onLoadDefaultUiProfile,
  defaultUiProfileAvailable,
  defaultUiProfileLoading,
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
  watchdogsController,
  stateMachinesController,
  sequencerProcessId,
  colorScheme,
  commandHistoryController,
  commandHistoryScrollRef,
  copyJsonToClipboard,
  logsOpen,
  setLogsOpen,
  logsWsConnected,
  logAutoScroll,
  setLogAutoScroll,
  logSortNewestFirst,
  setLogSortNewestFirst,
  logLoading,
  loadLogTail,
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
    <Suspense fallback={null}>
      {hdf.hdfModalOpen || hdf.hdfNoteModalOpen ? (
        <HdfModalsLayer
          hdf={hdf}
          renderMeasurementFieldInput={renderMeasurementFieldInput}
        />
      ) : null}
      {influx.influxModalOpen ? (
        <InfluxWriterModal
          opened={influx.influxModalOpen}
        onClose={() => influx.setInfluxModalOpen(false)}
        title={`Influx Writer ${influx.influxWriterProcessId ?? ""}`}
        influxWriterState={influx.influxWriterState}
        influxWriterProcessId={influx.influxWriterProcessId}
        influxWriterStatus={influx.influxWriterStatus}
        influxWriterLoading={influx.influxWriterLoading}
        influxProcessCapabilitiesError={influx.influxProcessCapabilitiesError}
        influxCommandsBlocked={influx.influxCommandsBlocked}
        influxAnyCommandBusy={influx.influxAnyCommandBusy}
        influxSupportsStatus={influx.influxSupportsStatus}
        influxSupportsEnable={influx.influxSupportsEnable}
        influxSupportsDisable={influx.influxSupportsDisable}
        influxSupportsFlush={influx.influxSupportsFlush}
        influxStatusBusy={influx.influxStatusBusy}
        influxEnableBusy={influx.influxEnableBusy}
        influxDisableBusy={influx.influxDisableBusy}
        influxFlushBusy={influx.influxFlushBusy}
        onRefreshStatus={influx.executeInfluxStatus}
        onEnable={influx.executeInfluxEnable}
        onDisable={influx.executeInfluxDisable}
        onFlush={influx.executeInfluxFlush}
        />
      ) : null}

      {processCommandController.processCommandOpen ? (
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
        deckDisabled={processCommandDeckDisabled}
        onAddToDeck={onAddProcessCommandToDeck}
        onExecute={processCommandController.executeProcessCommand}
        />
      ) : null}

      {processesController.processOpen ? (
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
      ) : null}

      {settingsOpen ? (
        <SettingsModal
          opened={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          settingsFileInputRef={settingsFileInputRef}
          onImportUiProfile={onImportUiProfile}
          onExportUiProfile={onExportUiProfile}
          onLoadDefaultUiProfile={onLoadDefaultUiProfile}
          defaultUiProfileAvailable={defaultUiProfileAvailable}
          defaultUiProfileLoading={defaultUiProfileLoading}
          onReload={onReloadSettings}
          loading={settingsLoading}
          error={settingsError}
          gatewaySettings={gatewaySettings}
          resolvedApiBase={resolvedApiBase}
          resolvedWsBase={resolvedWsBase}
          telemetryStreamStatus={telemetryStreamStatus}
        />
      ) : null}

      {interlocksController.interlocksOpen ? (
        <SafetyModal
          opened={interlocksController.interlocksOpen}
        onClose={() => interlocksController.setInterlocksOpen(false)}
        interlocks={{
          onRefresh: interlocksController.refreshInterlocksModalData,
          devices,
          processes: interlocksController.interlocksPanelProcesses,
          followerRulesByProcessId: interlocksController.followerRulesByProcessId,
          interlockStatusByProcessId: interlocksController.interlockStatusByProcessId,
          interlocksLoadingByProcessId: interlocksController.interlocksLoadingByProcessId,
          interlocksErrorByProcessId: interlocksController.interlocksErrorByProcessId,
          interlockRuleBusyByKey: interlocksController.interlockRuleBusyByKey,
          commandInterceptorRoutes: interlocksController.commandInterceptorRoutes,
          commandInterceptorRoutesLoading: interlocksController.commandInterceptorRoutesLoading,
          commandInterceptorRoutesError: interlocksController.commandInterceptorRoutesError,
          onRefreshProcess: (processId) =>
            interlocksController.refreshInterlockProcessStatus(processId, undefined, {
              showLoading: true,
            }),
          onToggleFollowerRule: interlocksController.toggleFollowerRule,
          onToggleInterlockRule: interlocksController.toggleInterlockRule,
        }}
        watchdogs={{
          processes: watchdogsController.watchdogsPanelProcesses,
          watchdogStatusByProcessId: watchdogsController.watchdogStatusByProcessId,
          watchdogLoadingByProcessId: watchdogsController.watchdogLoadingByProcessId,
          watchdogErrorByProcessId: watchdogsController.watchdogErrorByProcessId,
          watchdogBusyByKey: watchdogsController.watchdogBusyByKey,
          onRefreshProcess: (processId) =>
            watchdogsController.refreshWatchdogProcessStatus(processId, undefined, {
              showLoading: true,
            }),
          onToggleWatchdog: watchdogsController.toggleWatchdog,
          onClearRuleLatch: watchdogsController.clearWatchdogRuleLatch,
        }}
        interlockSummary={interlocksController.interlockButtonSummary}
        watchdogSummary={watchdogsController.watchdogButtonSummary}
        />
      ) : null}

      {stateMachinesController.stateMachinesOpen ? (
        <StateMachinesModal
          opened={stateMachinesController.stateMachinesOpen}
        onClose={() => stateMachinesController.setStateMachinesOpen(false)}
        rows={stateMachinesController.stateMachineRows}
        selectedProcessId={stateMachinesController.selectedProcessId}
        onSelectProcess={stateMachinesController.setSelectedProcessId}
        onRefresh={stateMachinesController.refreshStateMachinesModalData}
        onRefreshProcess={(processId) =>
          stateMachinesController.refreshStateMachineProcess(processId, undefined, {
            showLoading: true,
          })
        }
        onRefreshGraph={(processId) =>
          stateMachinesController.refreshStateMachineGraph(processId, {
            showLoading: true,
          })
        }
        onExecuteAction={stateMachinesController.executeStateMachineAction}
        colorScheme={colorScheme}
        />
      ) : null}

      <SequencerModalSlot
        sequencerProcessId={sequencerProcessId}
        streamCatalog={streamCatalog}
        capabilitiesByDevice={capabilitiesByDevice}
        streamWorkspaces={streamWorkspaces}
        latestSignalsByDevice={latestSignalsByDevice}
        colorScheme={colorScheme}
      />

      {commandHistoryController.commandHistoryOpen ? (
        <CommandHistoryModalContainer
          opened={commandHistoryController.commandHistoryOpen}
        onClose={() => commandHistoryController.setCommandHistoryOpen(false)}
        controller={commandHistoryController}
        devices={devices}
        colorScheme={colorScheme}
        viewportRef={commandHistoryScrollRef}
        onCopyJson={(label, payload) => {
          void copyJsonToClipboard(label, payload);
        }}
        />
      ) : null}

      {logsOpen ? (
        <LogsModalContainer
          opened={logsOpen}
        onClose={() => setLogsOpen(false)}
        connected={logsWsConnected}
        autoScroll={logAutoScroll}
        onAutoScrollChange={setLogAutoScroll}
        sortNewestFirst={logSortNewestFirst}
        onSortNewestFirstChange={setLogSortNewestFirst}
        loading={logLoading}
        loadLogTail={loadLogTail}
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
        colorScheme={colorScheme}
        devices={devices}
        processes={processesController.processes}
        viewportRef={logScrollRef}
        expandedByKey={expandedLogByKey}
        copyTextToClipboard={copyTextToClipboard}
        />
      ) : null}
    </Suspense>
  );
}

// Shared empty defaults: a new {} or [] per render would re-render the memoized sidebar.
const NO_ADAPTIVE_STUDIES: Record<string, SequencerAdaptiveStudyStatus> = {};
const NO_ADAPTIVE_IDS: readonly string[] = [];

type SequencerModalSlotProps = {
  sequencerProcessId: string | null;
  streamCatalog: StreamCatalogEntry[];
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
  streamWorkspaces: Record<string, StreamAnalysisWorkspaceConfig>;
  latestSignalsByDevice: Record<string, Record<string, TelemetrySignal>>;
  colorScheme: "light" | "dark";
};

/** Reads the sequencer context itself: its changes re-render only the modal. */
function SequencerModalSlot({
  sequencerProcessId,
  streamCatalog,
  capabilitiesByDevice,
  streamWorkspaces,
  latestSignalsByDevice,
  colorScheme,
}: SequencerModalSlotProps) {
  const sequencerController = useSequencerContext();
  if (!sequencerController.sequencerOpen) {
    return null;
  }
  return (
        <SequencerModalContainer
          opened={sequencerController.sequencerOpen}
        onClose={() => sequencerController.setSequencerOpen(false)}
        processState={sequencerController.sequencerProcessState}
        runtimeState={sequencerController.sequencerRuntimeState}
        loaded={sequencerController.sequencerLoaded}
        currentStep={sequencerController.sequencerStatus?.currentStep ?? null}
        currentStepDetail={
          sequencerController.sequencerStatus?.currentStepDetail ?? null
        }
        errorDetail={sequencerController.sequencerStatus?.errorDetail ?? null}
        cleanupActive={sequencerController.sequencerStatus?.cleanupActive ?? null}
        progress={sequencerController.sequencerProgress}
        progressPercent={sequencerController.sequencerProgressPercent}
        totalSteps={sequencerController.sequencerTotalSteps}
        completedSteps={sequencerController.sequencerCompletedSteps}
        loadedSource={sequencerController.sequencerStatus?.loadedSource ?? null}
        editorLabel={sequencerController.sequencerEditorLabel}
        pauseInfo={sequencerController.sequencerStatus?.pause ?? null}
        runEvents={sequencerController.sequencerRunEvents}
        plan={sequencerController.sequencerPlan}
        planError={sequencerController.sequencerPlanError}
        planOverridesInvalid={sequencerController.sequencerPlanOverridesInvalid}
        onTimelineVisibleChange={sequencerController.setSequencerTimelineVisible}
        loops={sequencerController.sequencerLoops}
        autoloadError={sequencerController.sequencerStatus?.autoloadError ?? null}
        statusError={sequencerController.sequencerStatus?.error ?? null}
        modalError={sequencerController.sequencerModalError}
        primaryIcon={
          sequencerController.sequencerPrimaryAction === "pause" ? (
            <IconPlayerPause size={14} />
          ) : (
            <IconPlayerPlay size={14} />
          )
        }
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
        onSelectedSequenceIdChange={
          sequencerController.setSequencerSelectedSequenceIdForUi
        }
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
        adaptiveStudies={sequencerController.sequencerStatus?.adaptiveStudies ?? NO_ADAPTIVE_STUDIES}
        loadedAdaptiveIds={sequencerController.sequencerStatus?.loadedAdaptiveIds ?? NO_ADAPTIVE_IDS}
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
        onLoadSelectedLibrary={sequencerController.loadSelectedSequencerLibrary}
        yamlDirty={sequencerController.sequencerYamlDirty}
        editorStale={sequencerController.sequencerEditorStale}
        otherSequenceLoaded={sequencerController.sequencerRevisionAction === "notice"}
        reloadSourceBusy={sequencerController.sequencerReloadBusy}
        canReloadSource={sequencerController.sequencerCanReloadSource}
        reloadSourceLabel={sequencerController.sequencerReloadSourceLabel}
        onReloadLoadedSource={sequencerController.reloadSequencerLoadedSource}
        editorRef={sequencerController.sequencerEditorRef}
        yamlText={sequencerController.sequencerYamlText}
        onYamlTextChange={sequencerController.onSequencerYamlTextChange}
        streamCatalog={streamCatalog}
        capabilitiesByDevice={capabilitiesByDevice}
        streamWorkspaces={streamWorkspaces}
        latestSignalsByDevice={latestSignalsByDevice}
        colorScheme={colorScheme}
        diagnostics={sequencerController.sequencerDiagnostics}
        valueContext={sequencerController.sequencerValueContext}
        onJumpToDiagnostic={sequencerController.jumpToSequencerDiagnostic}
        />
  );
}
