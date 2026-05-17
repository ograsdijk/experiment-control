import {
  AppShell,
  ActionIcon,
  Badge,
  Button,
  Card,
  Group,
  ScrollArea,
  Stack,
  Text,
  Textarea,
  TextInput,
  MultiSelect,
  Menu,
  Select,
  NumberInput,
  Popover,
  Switch,
  SegmentedControl,
  useComputedColorScheme,
  useMantineColorScheme,
} from "@mantine/core";
import { DndContext } from "@dnd-kit/core";
import { SortableContext, rectSortingStrategy } from "@dnd-kit/sortable";
import { notifications } from "@mantine/notifications";
import {
  IconCheck,
  IconChevronLeft,
  IconChevronRight,
  IconCpu,
  IconFileText,
  IconArrowsMaximize,
  IconPlayerPause,
  IconPlayerPlay,
  IconShieldCheck,
  IconSettings,
  IconPencil,
  IconPlug,
  IconRefresh,
  IconSquarePlus,
  IconStar,
  IconTerminal2,
  IconTrash,
  IconX,
} from "@tabler/icons-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ChangeEvent,
  type ReactNode,
} from "react";
import {
  callDevice,
  cleanupInstanceOrphans,
  fetchLogTail,
  fetchCapabilities,
  fetchDevices,
  fetchExtraUis,
  fetchGatewaySettings,
  fetchInstanceRuntimeStatus,
  fetchStreams,
  callProcess,
  type GatewaySettingsInfo,
  type InstanceRuntimeStatus,
  type ExtraUiInfo,
} from "./api";
import { AppModalsLayer } from "./components/AppModalsLayer";
import { CommandDeckPanel } from "./components/CommandDeckPanel";
import { DashboardHeaderBar } from "./components/DashboardHeaderBar";
import { DeviceCard } from "./components/DeviceCard";
import { DeviceNameInline } from "./components/DeviceNameInline";
import { coerceParamValue } from "./components/ParamInput";
import { PlotPanel, computeTelemetryAutoYRange } from "./components/PlotPanel";
import { PlotModalsLayer } from "./components/PlotModalsLayer";
import { StreamParamsPanel } from "./components/StreamParamsPanel";
import {
  StreamRawPanel,
  computeStreamRawAutoYRange,
} from "./components/StreamRawPanel";
import {
  StreamWaterfallPanel,
  computeStreamWaterfallAutoZRange,
} from "./components/StreamWaterfallPanel";
import {
  StreamBinStatsPanel,
  computeStreamBinStatsAutoYRange,
  type StreamBinStatsSeries,
  type UncertaintyMode,
} from "./components/StreamBinStatsPanel";
import {
  StreamBin2dPanel,
  computeStreamBin2dAutoZRange,
  type Bin2dReducer,
  type StreamBin2dSeries,
} from "./components/StreamBin2dPanel";
import { DagGraphPreview } from "./components/DagGraphPreview";
import { WorkspaceCommandLayer } from "./components/WorkspaceCommandLayer";
import { useCommandHistoryController } from "./features/commands/useCommandHistoryController";
import { formatApiErrorToastMessage } from "./features/common/api_error";
import { sameStringArray, sameStringRecord } from "./features/common/compare";
import {
  normalizeBooleanMap,
  normalizeStringList,
} from "./features/common/normalize";
import { detectGridColumns } from "./features/layout/serpentine";
import { ReorderableCardShell } from "./features/layout/ReorderableCardShell";
import {
  logEntryKey,
  normalizeLogEntry,
  toPrettyJson,
} from "./features/logs/utils";
import type {
  MeasurementFieldSchema,
} from "./features/hdf/types";
import type {
  PinnedCommandMap,
  PinnedParamDrafts,
  PlotState,
  PlotWorkspaceColumnsSetting,
  UiProfileState,
} from "./features/profile/types";
import {
  clampNavWidth,
  normalizeCommandDeck,
  normalizePinnedCommands,
  normalizePlotWorkspaceColumnsSetting,
} from "./features/profile/utils";
import { useInterlocksController } from "./features/interlocks/useInterlocksController";
import { useWatchdogsController } from "./features/watchdogs/useWatchdogsController";
import { useStateMachinesController } from "./features/state_machines/useStateMachinesController";
import { useHdfController } from "./features/hdf/useHdfController";
import { useInfluxController } from "./features/influx/useInfluxController";
import { useDeviceCapabilitiesController } from "./features/devices/useDeviceCapabilitiesController";
import { useDeviceLifecycleController } from "./features/devices/useDeviceLifecycleController";
import { useDeviceCommandController } from "./features/devices/useDeviceCommandController";
import {
  buildParamDefaults,
  effectiveDeviceMemberParams,
  mapDeviceActionForMember,
} from "./features/devices/command_schema";
import { useProcessCommandController } from "./features/processes/useProcessCommandController";
import { useProcessLifecycleController } from "./features/processes/useProcessLifecycleController";
import { useProcessesController } from "./features/processes/useProcessesController";
import { useTelemetryPipeline } from "./features/telemetry/useTelemetryPipeline";
import { useRawStreamSubscriptions } from "./features/telemetry/useRawStreamSubscriptions";
import { useStreamAnalysisSubscriptions } from "./features/stream_analysis/useStreamAnalysisSubscriptions";
import { useTelemetry } from "./features/telemetry/TelemetryContext";
import { useStreamAnalysis } from "./features/stream_analysis/StreamAnalysisContext";
import { useDaqDraftEditors } from "./features/stream_analysis/useDaqDraftEditors";
import { useDaqModalLifecycle } from "./features/stream_analysis/useDaqModalLifecycle";
import { useWorkspaceStoreActions } from "./features/stream_analysis/useWorkspaceStoreActions";
import { useWorkspaceListManagement } from "./features/stream_analysis/useWorkspaceListManagement";
import { useDaqWorkspaceApply } from "./features/stream_analysis/useDaqWorkspaceApply";
import { useDevicesContext } from "./features/devices/DevicesContext";
import { useCommands } from "./features/commands/CommandsContext";
import { useCommandDeckMutations } from "./features/commands/useCommandDeckMutations";
import { useCommandDeckRunner } from "./features/commands/useCommandDeckRunner";
import { usePinnedParamsReconciler } from "./features/commands/usePinnedParamsReconciler";
import {
  isCommandDeckCommandEntry,
  isCommandDeckTelemetryEntry,
  normalizeDeckGroup,
} from "./features/commands/utils";
import { useLayout } from "./features/layout/LayoutContext";
import { deviceSortableId } from "./features/layout/drag_helpers";
import { useNavResizer } from "./features/layout/useNavResizer";
import { useUiDragController } from "./features/layout/useUiDragController";
import { useLogs } from "./features/logs/LogsContext";
import { ExpandedPlotBody } from "./features/panels/ExpandedPlotBody";
import { useStreamWorkspacePanelReconciler } from "./features/panels/useStreamWorkspacePanelReconciler";
import {
  PanelsGrid,
  type PanelsGridHandlers,
  type PanelsGridHelpers,
} from "./features/panels/PanelsGrid";
import { DraggableTraceChip } from "./components/DraggableTraceChip";
import { usePanels } from "./features/panels/PanelsContext";
import { usePanelDerivations } from "./features/panels/usePanelDerivations";
import { usePanelUiHandlers } from "./features/panels/usePanelUiHandlers";
import { usePanelAutoRangeHandlers } from "./features/panels/usePanelAutoRangeHandlers";
import { useStreamPanelHandlers } from "./features/panels/useStreamPanelHandlers";
import { useStreamWorkspaceHandlers } from "./features/panels/useStreamWorkspaceHandlers";
import { usePanelLifecycle } from "./features/panels/usePanelLifecycle";
import { usePanelTitleEditor } from "./features/panels/usePanelTitleEditor";
import {
  streamBinStatsFitOverlayCurves as streamBinStatsFitOverlayCurvesImpl,
  streamBinStatsOverlaySeries as streamBinStatsOverlaySeriesImpl,
  streamTraceOverlaySeries as streamTraceOverlaySeriesImpl,
} from "./features/panels/overlayHelpers";
import {
  applyRawStreamFrameToPanels as applyRawStreamFrameToPanelsImpl,
  applyStreamAnalysisOutputToPanels as applyStreamAnalysisOutputToPanelsImpl,
  ensurePanelBuffers as ensurePanelBuffersImpl,
  panelCapacity as panelCapacityImpl,
  type ApplyHelpersDeps,
} from "./features/panels/applyToPanels";
import { useSettings } from "./features/runtime/SettingsContext";
import { useRuntimeRefreshers } from "./features/runtime/useRuntimeRefreshers";
import { useUiProfile } from "./features/runtime/useUiProfile";
import { useLogsStream } from "./features/logs/useLogsStream";
import type {
  PanelKind,
  PlotPanelState,
  PlotStreamBin2dPanelState,
  PlotStreamBinStatsPanelState,
  PlotStreamPanelState,
  PlotStreamParamsPanelState,
  PlotStreamScalarPanelState,
  PlotStreamWaterfallPanelState,
  PlotTelemetryPanelState,
  RawStreamSubscription,
  StreamAnalysisSettings,
  StreamAnalysisWorkspaceConfig,
  StreamBin2dSnapshot,
  StreamBinStatsSettings,
  StreamBinStatsSnapshot,
  StreamFitCurveSnapshot,
  StreamDagNodeConfig,
  StreamDagOutputConfig,
  StreamFrameSample,
  StreamTarget,
  StreamTraceAverageMode,
  StreamTraceDecimator,
  StreamTraceSourceMode,
  StreamParamsOutputValue,
  StreamWorkspaceStoreStatus,
  StreamWorkspaceSummary,
  TelemetrySmoothingMode,
  YDisplayMode,
  YOffsetMode,
  YScaleMode,
} from "./features/stream/types";
import {
  cloneDagNodes,
  cloneDagOutputs,
  isPublishableNodeKind,
  nodeKindFromOp,
  STREAM_DAG_INPUT_KINDS,
  STREAM_DAG_OP_OPTIONS,
  STREAM_DAG_OPS,
} from "./features/stream/dag";
import {
  normalizeFitCurveValue,
  normalizeHistAggValue,
  normalizeHist2dValue,
  normalizeFitParamsMapValue,
  normalizeStreamAnalysisOutputMessage,
  normalizeTime,
  normalizeTraceValues,
} from "./features/stream/messages";
import {
  isStreamBin2dPanel,
  isStreamBinStatsPanel,
  isStreamRawPanel,
  isStreamParamsPanel,
  isStreamScalarPanel,
  isStreamTracePanel,
  isStreamWaterfallPanel,
  isTelemetryPanel,
  normalizeAutoRange,
  streamScalarTrace,
} from "./features/stream/panel_helpers";
import {
  dagOutputKindColor,
  DEFAULT_BIN2D_OUTPUT_ID,
  DEFAULT_BIN2D_REDUCER,
  DEFAULT_BIN_COUNT,
  DEFAULT_BIN_OUTPUT_ID,
  DEFAULT_BIN_X_MAX,
  DEFAULT_BIN_X_MIN,
  DEFAULT_TELEMETRY_SMOOTHING_MODE,
  DEFAULT_TELEMETRY_SMOOTHING_WINDOW_S,
  DEFAULT_INTEGRAL_OUTPUT_ID,
  DEFAULT_STREAM_CONTEXT_FIELD,
  DEFAULT_STREAM_OVERLAY_COUNT,
  DEFAULT_TRACE_AVERAGE_MODE,
  DEFAULT_TRACE_DECIMATOR,
  DEFAULT_TRACE_MAX_FPS,
  DEFAULT_TRACE_MAX_POINTS,
  DEFAULT_TRACE_ROLLING_WINDOW,
  DEFAULT_UNCERTAINTY_SCALE,
  DEFAULT_WATERFALL_ROWS,
  inferChannelCountFromShape,
  normalizeShape,
  normalizeTelemetrySmoothingMode,
  normalizeTelemetrySmoothingWindow,
  normalizeTraceAverageMode,
  normalizeTraceDecimator,
  normalizeTraceMaxFps,
  normalizeTraceMaxPoints,
  normalizeTraceRollingWindow,
  normalizeYBound,
  normalizeYScaleMode,
  parseNumberInput,
  streamTargetKey,
  traceKeyId,
} from "./features/stream/utils";
import {
  isInfluxWriterProcess,
  isHdfWriterProcess,
  isProcessRpcStateAvailable,
  isSequencerProcess,
  pinnedCommandKey,
  processStateColor,
  sequencerRuntimeStateColor,
  supportsProcessCapability,
} from "./features/runtime/helpers";
import {
  defaultOutputForKind,
  defaultStreamAnalysisSettings,
  defaultStreamAnalysisWorkspaceConfig,
  defaultStreamBinStatsSettings,
  nextWorkspaceCounter,
  normalizeStreamAnalysisSettings,
  normalizeStreamBinStatsSettings,
  normalizeStreamWorkspaceRecord,
  normalizeUncertaintyMode,
  normalizeWorkspaceStoreStatus,
  streamWorkspaceSort,
  workspaceBin2dAxisLabel,
  workspaceOutputKind,
  workspaceOutputOptionsByKind,
  workspaceXAxisLabel,
} from "./features/stream/workspace";
import { useSequencerController } from "./features/sequencer/useSequencerController";
import { RingBuffer } from "./utils/ringBuffer";
import { colorWithAlpha, traceColorAt } from "./utils/traceColors";
import {
  CommandDeckCommandEntry,
  CommandDeckEntry,
  CommandDeckTelemetryEntry,
  CommandDeckTargetKind,
  CapabilityMember,
  DeviceStatus,
  LogEntry,
  PinnedCommand,
  ProcessStatus,
  StreamCatalogEntry,
  TelemetryMessage,
  TelemetrySignal,
  TraceKey,
} from "./types";

const DEFAULT_WINDOW_S = 60;
const DEFAULT_BUFFER_POINTS = 500;
const DEFAULT_NAV_WIDTH = 360;
const NAV_MIN_WIDTH = 260;
const NAV_MAX_WIDTH = 900;
const PLOT_GRID_MOBILE_BREAKPOINT = 900;
const MAX_LOG_ROWS = 2000;
const MAX_STREAM_FRAME_BUFFER = 240;
const STREAM_ANALYSIS_PROCESS_ID = "stream_analysis";
type LatestSignals = Record<string, Record<string, TelemetrySignal>>;

function trimNumericString(raw: string): string {
  const text = raw.trim();
  if (!text) {
    return text;
  }
  const expIdx = Math.max(text.indexOf("e"), text.indexOf("E"));
  if (expIdx >= 0) {
    const mantissa = text.slice(0, expIdx).replace(/\.?0+$/, "");
    const exponent = text.slice(expIdx + 1).replace(/^\+/, "");
    return `${mantissa}e${exponent}`;
  }
  return text.replace(/\.?0+$/, "");
}

function formatOffsetCompact(value: number): string {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  const rounded = Math.round(value);
  const abs = Math.abs(rounded);
  if (abs > 0 && (abs >= 1e6 || abs < 1e-3)) {
    return trimNumericString(rounded.toExponential(0));
  }
  return String(rounded);
}

function formatOffsetFull(value: number): string {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  return String(Math.round(value));
}

function isErrorSeverity(severity: unknown): boolean {
  const normalized = String(severity ?? "").trim().toLowerCase();
  return (
    normalized === "error" ||
    normalized === "fatal" ||
    normalized === "critical"
  );
}

// normalizeDeckGroup / isCommandDeckCommandEntry /
// isCommandDeckTelemetryEntry now live in features/commands/utils.ts
// (round 26 — needed by useCommandDeckMutations).

// UiDragData / panelSortableId / parseSortablePrefixedId moved to
// features/layout/drag_helpers.ts (round 31). deviceSortableId is
// imported from the same module below.

// DraggableTraceChip moved to components/DraggableTraceChip.tsx
// (round 30 — needed by PanelsGrid as well as remaining App-side uses).

export function App() {
  // Layout / viewport / sidebar-resize state moved to LayoutContext
  // (features/layout/LayoutContext.tsx). Resize event handlers and the
  // window-resize listener stay in App.tsx for now — they're tied to
  // DOM events that are easier to manage where the rest of App lives
  // until the layout shell (header / sidebar / grids) extracts.
  const {
    navWidth,
    setNavWidth,
    isResizing,
    setIsResizing,
    resizeRef,
    resizePendingWidthRef,
    resizeRafRef,
    isDevicePanelCollapsed,
    setIsDevicePanelCollapsed,
    devicePanelTab,
    setDevicePanelTab,
    plotWorkspaceColumns,
    setPlotWorkspaceColumns,
    plotWorkspaceOptionsOpen,
    setPlotWorkspaceOptionsOpen,
    viewportWidth,
    setViewportWidth,
    isNarrowPlotViewport,
    plotGridStyle,
    plotGridRef,
    dragColumnsRef,
  } = useLayout();
  // initialPlotState (localStorage rehydration) now lives in
  // PanelsContext (features/panels/PanelsContext.tsx). The panel state,
  // refs, modal-panel-id state, Y-axis editor state, plotTick, and the
  // autosave useEffect all moved into the Provider — see the
  // usePanels() destructure below.
  const initialStreamWorkspaceState = useMemo(() => {
    try {
      const raw = localStorage.getItem("ecui.streamWorkspaces");
      const workspaces = normalizeStreamWorkspaceRecord(
        raw ? JSON.parse(raw) : null
      );
      return {
        workspaces,
        nextId: nextWorkspaceCounter(workspaces),
      };
    } catch {
      return {
        workspaces: {} as Record<string, StreamAnalysisWorkspaceConfig>,
        nextId: 1,
      };
    }
  }, []);
  // Device roster + ordering + per-device UI collapse state moved to
  // DevicesContext (features/devices/DevicesContext.tsx). The names below
  // are kept identical so the existing call sites in App.tsx don't need
  // touch-ups. `orderedDevices` (the sorted derivation) now lives in the
  // Provider too — see the destructure below.
  const {
    devices,
    setDevices,
    orderedDevices,
    deviceOrder,
    setDeviceOrder,
    telemetryCollapsedByDevice,
    setTelemetryCollapsedByDevice,
    deviceGridRef,
  } = useDevicesContext();
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const computedColorScheme = useComputedColorScheme("light");
  // Panel state moved to PanelsContext — see the usePanels() destructure
  // below for `panels`, `setPanels`, `activePanelId`, `panelIdRef`,
  // `panelsRef`, `plotTick`, modal-panel-id state, and Y-axis editor.
  // Stream-analysis (DAQ workspace) state moved to StreamAnalysisContext
  // (features/stream_analysis/StreamAnalysisContext.tsx). The names below
  // are kept identical to the inline declarations they replaced so the
  // ~180 existing call sites in App.tsx don't need touch-ups.
  //
  // Network handlers (load/persist/reset) stay in App.tsx for now — they
  // call into helpers that haven't been extracted. The Context owns the
  // state container only, matching the round-8 TelemetryContext shape.
  const {
    streamWorkspaces,
    setStreamWorkspaces,
    streamWorkspaceRevisions,
    setStreamWorkspaceRevisions,
    workspaceStoreStatus,
    setWorkspaceStoreStatus,
    workspaceStoreBusyAction,
    setWorkspaceStoreBusyAction,
    daqOpen,
    setDaqOpen,
    daqWorkspaceId,
    setDaqWorkspaceId,
    daqDraftName,
    setDaqDraftName,
    daqDraftNodes,
    setDaqDraftNodes,
    daqDraftOutputs,
    setDaqDraftOutputs,
    daqDraftEnabled,
    setDaqDraftEnabled,
    daqResetNodeBusyId,
    setDaqResetNodeBusyId,
    daqFocusedNodeId,
    setDaqFocusedNodeId,
    streamWorkspaceIdRef,
    streamWorkspacesRef,
    streamWorkspaceRevisionsRef,
    daqNodeCardRefs,
    daqNodeFocusTimeoutRef,
  } = useStreamAnalysis();
  // Panel state container — see features/panels/PanelsContext.tsx.
  // Names kept identical so the ~37 setPanels(prev => ...) sites and
  // the modal handlers throughout this file don't need touch-ups.
  const {
    panels,
    setPanels,
    activePanelId,
    setActivePanelId,
    panelsRef,
    panelIdRef,
    plotTick,
    setPlotTick,
    plotOptionsPanelId,
    setPlotOptionsPanelId,
    expandedPlotPanelId,
    setExpandedPlotPanelId,
    streamTraceOptionsPanelId,
    setStreamTraceOptionsPanelId,
    streamBinStatsOptionsPanelId,
    setStreamBinStatsOptionsPanelId,
    streamParamsOptionsPanelId,
    setStreamParamsOptionsPanelId,
    streamBin2dOptionsPanelId,
    setStreamBin2dOptionsPanelId,
    yAxisDraftMin,
    setYAxisDraftMin,
    yAxisDraftMax,
    setYAxisDraftMax,
    yAxisAutoRange,
    setYAxisAutoRange,
    editingPanelId,
    panelTitleDraft,
    setPanelTitleDraft,
  } = usePanels();
  // streamWsConnected + streamAnalysisWsConnected moved out of useState
  // and into useRawStreamSubscriptions / useStreamAnalysisSubscriptions
  // hook returns (round 34). See the hook calls further down.
  const [logsOpen, setLogsOpen] = useState(false);
  const [commandUnreadError, setCommandUnreadError] = useState(false);
  const [logsUnreadError, setLogsUnreadError] = useState(false);
  // Settings modal + instance-runtime state moved to SettingsContext
  // (features/runtime/SettingsContext.tsx). Network handlers stay in
  // App.tsx — they call into other state that hasn't been extracted.
  const {
    settingsOpen,
    setSettingsOpen,
    settingsLoading,
    setSettingsLoading,
    settingsError,
    setSettingsError,
    gatewaySettings,
    setGatewaySettings,
    extraUis,
    setExtraUis,
    instanceRuntimeStatus,
    setInstanceRuntimeStatus,
    instanceRuntimeLoading,
    setInstanceRuntimeLoading,
    instanceRuntimeError,
    setInstanceRuntimeError,
    instanceCleanupBusy,
    setInstanceCleanupBusy,
    settingsFileInputRef,
  } = useSettings();
  // Log viewer state + the filtered-rows derivation moved to LogsContext
  // (features/logs/LogsContext.tsx). WS subscription stays in App.tsx;
  // useLogsStream is unchanged for downstream compatibility.
  const {
    logRows,
    setLogRows,
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
    logAutoScroll,
    setLogAutoScroll,
    logLoading,
    setLogLoading,
    expandedLogByKey,
    setExpandedLogByKey,
    filteredLogRows,
    logSeenRef,
    logScrollRef,
    logRowsBaselineReadyRef,
    logRowsLastKeyRef,
  } = useLogs();
  const [streamCatalog, setStreamCatalog] = useState<StreamCatalogEntry[]>([]);
  // activeUiDrag + setActiveUiDrag moved to useUiDragController (round 31).
  // editingPanelId + panelTitleDraft moved to PanelsContext (round 20).
  // Pulled out alongside usePanelTitleEditor's 3 handlers so
  // usePanelLifecycle's removePanel can clear the editor via context
  // rather than taking the setters as args.
  // deviceOrder + telemetryCollapsedByDevice now provided by
  // DevicesContext (destructured at the top of the function).
  // Pinned commands + command deck state moved to CommandsContext
  // (features/commands/CommandsContext.tsx). The names below are kept
  // identical so existing call sites in App.tsx don't need touch-ups.
  // Network/CRUD handlers stay in App.tsx for now — they call into
  // device + process command controllers that haven't been extracted.
  const {
    pinnedCommands,
    setPinnedCommands,
    pinnedParamDrafts,
    setPinnedParamDrafts,
    pinnedBusyByKey,
    setPinnedBusyByKey,
    commandDeck,
    setCommandDeck,
    commandDeckCollapsedByGroup,
    setCommandDeckCollapsedByGroup,
    commandDeckBusyById,
    setCommandDeckBusyById,
    commandDeckIdRef,
  } = useCommands();
  // deviceGridRef now provided by DevicesContext.
  // plotGridRef + dragColumnsRef now provided by LayoutContext.
  // pinnedParamDrafts + pinnedBusyByKey now provided by CommandsContext
  // (destructured above).
  // logSeenRef + logScrollRef + logRowsBaselineReadyRef +
  // logRowsLastKeyRef now provided by LogsContext (destructured above).
  const commandHistoryScrollRef = useRef<HTMLDivElement | null>(null);
  const commandHistoryNearBottomRef = useRef(true);
  const commandHistoryBaselineReadyRef = useRef(false);
  const commandHistoryLastIdRef = useRef<string | null>(null);
  // settingsFileInputRef now provided by SettingsContext (destructured above).
  // pinnedCommands + commandDeck + commandDeckBusyById + commandDeckIdRef
  // now provided by CommandsContext (destructured above).
  // Plot buffers + per-stream overlay caches now live in TelemetryContext
  // (features/telemetry/TelemetryContext.tsx) so future feature-module
  // extractions can subscribe to them via useTelemetry() without prop-
  // drilling refs through. The names below are kept identical so the
  // hundreds of existing call sites in App.tsx don't need touch-ups.
  const {
    buffersRef,
    streamFramesRef,
    streamTraceOverlayRef,
    streamBinStatsOverlayRef,
    streamBinStatsFitOverlayRef,
    streamParamsLatestRef,
    streamBinStatsRef,
    streamBin2dRef,
    panelBuffersByTraceKey,
    registerPanelTraces,
    unregisterPanel: unregisterPanelTelemetry,
  } = useTelemetry();
  // streamWorkspacesRef / streamWorkspaceRevisionsRef / daqNodeCardRefs /
  // daqNodeFocusTimeoutRef now provided by StreamAnalysisContext (see
  // destructure above).
  // panelsRef now provided by PanelsContext (destructured above).
  const streamAnalysisReadyRef = useRef(false);
  const rawSnapshotHydratedRef = useRef<Set<string>>(new Set());
  const workspaceSnapshotHydratedRef = useRef<Set<string>>(new Set());
  // isNarrowPlotViewport + plotGridStyle now provided by LayoutContext
  // (destructured at the top of App()).
  // dndSensors moved to useUiDragController (round 31).

  useEffect(() => {
    return () => {
      if (daqNodeFocusTimeoutRef.current !== null) {
        window.clearTimeout(daqNodeFocusTimeoutRef.current);
        daqNodeFocusTimeoutRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const onResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
    };
  }, []);

  const processesController = useProcessesController({
    callProcessFn: callProcess,
  });

  const {
    processes,
    processOpen,
    setProcessOpen,
    processBusyById,
    setProcessBusy,
    capabilitiesByProcess,
    processCapabilitiesErrorById,
    refreshProcesses,
    ensureProcessCapabilitiesLoaded,
    invalidateProcessCapabilities,
  } = processesController;

  const commandHistoryController = useCommandHistoryController({
    callDeviceFn: callDevice,
    callProcessFn: callProcess,
  });

  const {
    commandHistoryOpen,
    setCommandHistoryOpen,
    commandHistoryMode,
    commandHistoryRows,
    commandJournalRows,
    setCommandHistoryRows,
    commandHistoryLimit,
    setCommandHistoryLimit,
    commandHistoryAutoScroll,
    setCommandHistoryAutoScroll,
    commandHistoryStatusFilter,
    setCommandHistoryStatusFilter,
    commandHistoryTargetFilter,
    setCommandHistoryTargetFilter,
    commandHistorySourceFilter,
    setCommandHistorySourceFilter,
    commandHistoryTextFilter,
    setCommandHistoryTextFilter,
    commandHistorySourceOptions,
    filteredCommandHistoryRows,
    filteredCommandJournalRows,
    sendDeviceCommand,
    sendProcessCommand,
  } = commandHistoryController;

  const {
    capabilitiesByDevice,
    setCapabilitiesByDevice,
    invalidateDeviceCapabilities,
  } = useDeviceCapabilitiesController(devices);

  // orderedDevices now lives in DevicesContext (destructured above).
  const streamCatalogByKey = useMemo(() => {
    const out = new Map<string, StreamCatalogEntry>();
    for (const entry of streamCatalog) {
      const deviceId = String(entry.device_id ?? "").trim();
      const stream = String(entry.stream ?? "").trim();
      if (!deviceId || !stream) {
        continue;
      }
      out.set(streamTargetKey(deviceId, stream), entry);
    }
    return out;
  }, [streamCatalog]);
  const streamTargetOptions = useMemo(() => {
    return streamCatalog
      .map((entry) => {
        const deviceId = String(entry.device_id ?? "").trim();
        const stream = String(entry.stream ?? "").trim();
        if (!deviceId || !stream) {
          return null;
        }
        const shapeText =
          Array.isArray(entry.shape) && entry.shape.length > 0
            ? ` [${entry.shape.join("x")}]`
            : "";
        return {
          value: streamTargetKey(deviceId, stream),
          label: `${deviceId}.${stream}${shapeText}`,
        };
      })
      .filter(
        (item): item is { value: string; label: string } => item !== null
      )
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [streamCatalog]);
  const streamWorkspaceOptions = useMemo(() => {
    return Object.values(streamWorkspaces)
      .sort(streamWorkspaceSort)
      .map((workspace) => {
        const streamLabel = workspace.stream
          ? `${workspace.stream.deviceId}.${workspace.stream.stream}`
          : "unbound";
        return {
          value: workspace.workspaceId,
          label: `${workspace.name} (${streamLabel})`,
        };
      });
  }, [streamWorkspaces]);
  const daqWorkspace = useMemo(() => {
    if (!daqWorkspaceId) {
      return null;
    }
    return streamWorkspaces[daqWorkspaceId] ?? null;
  }, [streamWorkspaces, daqWorkspaceId]);
  const daqPublishableNodeOptions = useMemo(
    () =>
      daqDraftNodes
        .filter((node) => isPublishableNodeKind(nodeKindFromOp(node.op)))
        .map((node) => ({
          value: node.id,
          label: `${node.id} (${node.op})`,
        })),
    [daqDraftNodes]
  );
  const daqResettableNodeIds = useMemo(
    () =>
      new Set(
        daqDraftNodes
          .filter(
            (node) =>
              node.op === "aggregate.bin_stats" ||
              node.op === "aggregate.bin2d_stats"
          )
          .map((n) => n.id)
      ),
    [daqDraftNodes]
  );
  // The 18-entry modal-resolution memo tree + the two subscription
  // derivations all live in usePanelDerivations() (round-13 extraction
  // — features/panels/usePanelDerivations.ts). Same names so existing
  // call sites in App.tsx don't need touch-ups.
  const {
    expandedPlotPanel,
    streamTraceOptionsPanel,
    streamTraceOptionsWorkspace,
    streamTraceOptionsTraceOutputOptions,
    streamTraceOptionsOverlayOutputOptions,
    streamBinStatsOptionsPanel,
    streamBinStatsOptionsWorkspace,
    streamBinStatsOptionsOutputOptions,
    streamBinStatsOptionsTraceOverlayOptions,
    streamBinStatsOptionsFitOverlayOptions,
    streamBinStatsOptionsXLabel,
    streamParamsOptionsPanel,
    streamParamsOptionsWorkspace,
    streamParamsOutputOptions,
    streamBin2dOptionsPanel,
    streamBin2dOptionsWorkspace,
    streamBin2dOptionsOutputOptions,
    streamBin2dOptionsXLabel,
    streamBin2dOptionsYLabel,
    activeRawStreamSubscriptions,
    activeStreamAnalysisWorkspaceSubscriptions,
  } = usePanelDerivations();

  // Simple panel UI / Y-axis / modal-toggle handlers (round 15
  // extraction). See features/panels/usePanelUiHandlers.ts for the
  // hook body; names kept identical so existing call sites in App.tsx
  // don't need touch-ups.
  const {
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
  } = usePanelUiHandlers();

  // Y-axis editor + auto-range handlers (round 16 extraction). See
  // features/panels/usePanelAutoRangeHandlers.ts. Takes the simple
  // Y-axis setters from usePanelUiHandlers as args so the
  // state-mutation stays in one place.
  const {
    resolveTelemetryPanelOffset,
    resolvePanelAutoYRange,
    setTelemetryYOffsetMode,
    openPlotOptions,
    closePlotOptions,
    applyPlotOptionsAxis,
    setPlotOptionsAxisMode,
  } = usePanelAutoRangeHandlers({
    setPanelYScaleMode,
    setPanelManualYRange,
  });

  // Per-panel stream-trace config setters + buffer-clear utilities
  // (round 17). See features/panels/useStreamPanelHandlers.ts.
  // Also restores setStreamPanelTarget which was accidentally removed
  // in round 16.
  const {
    clearPanelBuffers,
    clearStreamPanelFrames,
    clearStreamBinStatsPanel,
    clearStreamBin2dPanel,
    clearWorkspaceBinPanels,
    setStreamPanelTarget,
    setStreamPanelTargetFromKey,
    setStreamPanelOverlayCount,
    setStreamPanelChannelIndex,
    setStreamPanelTraceDecimator,
    setStreamPanelTraceMaxPoints,
    setStreamPanelTraceMaxFps,
    setStreamPanelRollingWindow,
    setStreamPanelAverageMode,
  } = useStreamPanelHandlers({
    streamCatalogByKey,
    streamAnalysisReadyRef,
  });

  // Stream-trace source/workspace/output + stream-analysis panel
  // workspace/output switches + binstats overlay/fit-overlay setters
  // (round 18). See features/panels/useStreamWorkspaceHandlers.ts.
  // Takes clearPanelBuffers from useStreamPanelHandlers because the
  // workspace switches clear accumulated buffers on scalar panels.
  const {
    setStreamTracePanelSourceMode,
    setStreamTracePanelWorkspace,
    setStreamTracePanelOutput,
    setStreamTracePanelOverlayOutputs,
    setStreamAnalysisPanelWorkspace,
    setStreamAnalysisPanelOutput,
    setStreamParamsPanelOutputs,
    setStreamBinStatsOverlayOutputs,
    setStreamBinStatsFitOverlayOutputs,
  } = useStreamWorkspaceHandlers({
    clearPanelBuffers,
  });

  // Overlay-series helpers (round 16 extraction). The render loop +
  // applyPlotOptionsAxis both use these; the pure functions live in
  // features/panels/overlayHelpers.ts. App.tsx wraps them with the
  // local overlay refs already destructured from TelemetryContext so
  // call sites keep the same panel-only signature they had before.
  const streamTraceOverlaySeries = (
    panel: Parameters<typeof streamTraceOverlaySeriesImpl>[0]
  ) => streamTraceOverlaySeriesImpl(panel, streamTraceOverlayRef);
  const streamBinStatsOverlaySeries = (
    panel: Parameters<typeof streamBinStatsOverlaySeriesImpl>[0]
  ) => streamBinStatsOverlaySeriesImpl(panel, streamBinStatsOverlayRef);
  const streamBinStatsFitOverlayCurves = (
    panel: Parameters<typeof streamBinStatsFitOverlayCurvesImpl>[0]
  ) => streamBinStatsFitOverlayCurvesImpl(panel, streamBinStatsFitOverlayRef);
  const hdfWriterProcess = useMemo(
    () => processes.find(isHdfWriterProcess) ?? null,
    [processes]
  );
  const influxWriterProcess = useMemo(
    () => processes.find(isInfluxWriterProcess) ?? null,
    [processes]
  );
  const sequencerProcess = useMemo(
    () => processes.find(isSequencerProcess) ?? null,
    [processes]
  );
  const streamAnalysisProcess = useMemo(
    () =>
      processes.find(
        (process) =>
          String(process.process_id ?? "").toLowerCase() ===
          STREAM_ANALYSIS_PROCESS_ID
      ) ?? null,
    [processes]
  );
  const streamAnalysisRpcReady =
    String(streamAnalysisProcess?.state ?? "").toUpperCase() === "RUNNING";
  const showDaqUi = streamAnalysisRpcReady;
  // activeRawStreamSubscriptions + activeStreamAnalysisWorkspaceSubscriptions
  // now live in usePanelDerivations() (destructured above).
  // filteredLogRows now lives in LogsContext (destructured above).
  const resolvedApiBase = useMemo(() => {
    const configured = String(import.meta.env.VITE_API_BASE ?? "").trim();
    if (configured) {
      return configured;
    }
    return `${window.location.protocol}//${window.location.host}`;
  }, []);

  const resolvedWsBase = useMemo(() => {
    const configured = String(import.meta.env.VITE_WS_BASE ?? "").trim();
    if (configured) {
      return configured;
    }
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    return `${scheme}://${window.location.host}`;
  }, []);
  const instanceLabel = useMemo(() => {
    const raw = gatewaySettings?.instance_id;
    if (typeof raw !== "string") {
      return "unknown";
    }
    const value = raw.trim();
    return value.length > 0 ? value : "unknown";
  }, [gatewaySettings]);

  // Runtime / settings / device-list refreshers (round 27). See
  // features/runtime/useRuntimeRefreshers.ts. Five thin async wrappers
  // around the corresponding API endpoints, sharing the same loading
  // / error-state surface in SettingsContext.
  const {
    refreshDevices,
    refreshStreams,
    loadGatewayRuntimeSettings,
    refreshInstanceRuntime,
    runInstanceCleanup,
  } = useRuntimeRefreshers();

  useEffect(() => {
    void refreshInstanceRuntime();
  }, []);

  // UI profile import/export is wired below — see the useUiProfile
  // call after setDevicePanelCollapsed is defined (round 29).

  const appendLogEntries = (entries: LogEntry[]) => {
    if (entries.length === 0) {
      return;
    }
    const accepted: LogEntry[] = [];
    for (const entry of entries) {
      const key = logEntryKey(entry);
      if (logSeenRef.current.has(key)) {
        continue;
      }
      logSeenRef.current.add(key);
      accepted.push(entry);
    }
    if (accepted.length === 0) {
      return;
    }
    setLogRows((prev) => {
      const next = [...prev, ...accepted];
      if (next.length <= MAX_LOG_ROWS) {
        return next;
      }
      const trimmed = next.slice(next.length - MAX_LOG_ROWS);
      const keep = new Set(trimmed.map((entry) => logEntryKey(entry)));
      logSeenRef.current = keep;
      return trimmed;
    });
  };

  const loadLogTail = async () => {
    setLogLoading(true);
    try {
      const resp = await fetchLogTail({ limit: 1000 });
      if (!resp.ok || !resp.result || typeof resp.result !== "object") {
        notifications.show({
          color: "red",
          title: "Log fetch failed",
          message: resp.error?.message ?? resp.error?.code ?? "Unknown error",
        });
        return;
      }
      const rawEntries = Array.isArray(resp.result.entries)
        ? resp.result.entries
        : [];
      const normalized = rawEntries
        .map((entry) => normalizeLogEntry(entry))
        .filter((entry): entry is LogEntry => entry !== null);
      logSeenRef.current = new Set(normalized.map((entry) => logEntryKey(entry)));
      setLogRows(normalized.slice(Math.max(0, normalized.length - MAX_LOG_ROWS)));
      setExpandedLogByKey({});
    } catch (error) {
      notifications.show({
        color: "red",
        title: "Log fetch failed",
        message: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setLogLoading(false);
    }
  };

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const next = await fetchDevices();
      if (alive) {
        setDevices(next);
      }
    };
    load();
    const interval = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    const availableIds = devices.map((device) => device.device_id);
    setDeviceOrder((prev) => {
      const kept = prev.filter((deviceId) => availableIds.includes(deviceId));
      const missing = availableIds.filter((deviceId) => !kept.includes(deviceId));
      const next = [...kept, ...missing];
      return sameStringArray(prev, next) ? prev : next;
    });
    setTelemetryCollapsedByDevice((prev) => {
      const next: Record<string, boolean> = {};
      let changed = false;
      for (const deviceId of availableIds) {
        if (prev[deviceId] === true) {
          next[deviceId] = true;
        }
      }
      const prevKeys = Object.keys(prev);
      const nextKeys = Object.keys(next);
      if (prevKeys.length !== nextKeys.length) {
        changed = true;
      } else {
        for (const key of prevKeys) {
          if (prev[key] !== next[key]) {
            changed = true;
            break;
          }
        }
      }
      return changed ? next : prev;
    });
  }, [devices]);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const next = await refreshStreams();
      if (!alive) {
        return;
      }
      setStreamCatalog(next);
    };
    load();
    const interval = setInterval(load, 7000);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem("ecui.pinnedCommands", JSON.stringify(pinnedCommands));
    } catch {
      // ignore storage errors
    }
  }, [pinnedCommands]);

  useEffect(() => {
    try {
      localStorage.setItem("ecui.commandDeck", JSON.stringify(commandDeck));
    } catch {
      // ignore storage errors
    }
  }, [commandDeck]);

  useEffect(() => {
    try {
      localStorage.setItem("ecui.deviceOrder", JSON.stringify(deviceOrder));
    } catch {
      // ignore storage errors
    }
  }, [deviceOrder]);

  useEffect(() => {
    try {
      localStorage.setItem(
        "ecui.telemetryCollapsedByDevice",
        JSON.stringify(telemetryCollapsedByDevice)
      );
    } catch {
      // ignore storage errors
    }
  }, [telemetryCollapsedByDevice]);

  useEffect(() => {
    try {
      localStorage.setItem(
        "ecui.commandDeck.collapsedByGroup",
        JSON.stringify(commandDeckCollapsedByGroup)
      );
    } catch {
      // ignore storage errors
    }
  }, [commandDeckCollapsedByGroup]);

  // Pinned-params draft reconciliation moved to
  // features/commands/usePinnedParamsReconciler.ts (round 32).
  usePinnedParamsReconciler({
    pinnedCommands,
    capabilitiesByDevice,
    setPinnedParamDrafts,
    setPinnedBusyByKey,
  });

  useEffect(() => {
    commandDeckIdRef.current = Math.max(
      commandDeckIdRef.current,
      commandDeck.length + 1
    );
    setCommandDeckBusyById((prev) => {
      const valid = new Set(commandDeck.map((entry) => entry.id));
      let changed = false;
      const next = { ...prev };
      for (const key of Object.keys(next)) {
        if (!valid.has(key)) {
          delete next[key];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [commandDeck]);

  useEffect(() => {
    const processById = new Map(
      processes.map((process) => [process.process_id, process])
    );
    const processIds = [
      ...new Set(
        commandDeck
          .filter(
            (entry) =>
              isCommandDeckCommandEntry(entry) && entry.targetKind === "process"
          )
          .map((entry) => String(entry.targetId ?? "").trim())
          .filter((processId) => processId.length > 0)
      ),
    ];
    for (const processId of processIds) {
      if ((capabilitiesByProcess[processId] ?? []).length > 0) {
        continue;
      }
      const process = processById.get(processId);
      if (!process) {
        continue;
      }
      const state = String(process.state ?? "").toUpperCase();
      if (!["RUNNING", "STARTING", "STOPPING"].includes(state)) {
        continue;
      }
      void ensureProcessCapabilitiesLoaded(processId);
    }
  }, [commandDeck, capabilitiesByProcess, ensureProcessCapabilitiesLoaded, processes]);

  useEffect(() => {
    try {
      localStorage.setItem("ecui.navWidth", String(navWidth));
    } catch {
      // ignore storage errors
    }
  }, [navWidth]);

  useEffect(() => {
    try {
      localStorage.setItem(
        "ecui.devicePanelCollapsed",
        isDevicePanelCollapsed ? "1" : "0"
      );
    } catch {
      // ignore storage errors
    }
  }, [isDevicePanelCollapsed]);

  useEffect(() => {
    try {
      localStorage.setItem("ecui.devicePanelTab", devicePanelTab);
    } catch {
      // ignore storage errors
    }
  }, [devicePanelTab]);

  useEffect(() => {
    try {
      localStorage.setItem("ecui.plotWorkspaceColumns", plotWorkspaceColumns);
    } catch {
      // ignore storage errors
    }
  }, [plotWorkspaceColumns]);

  // panels/activePanelId autosave to ecui.plotState now lives in
  // PanelsContext. panelsRef sync also moved there.

  useEffect(() => {
    try {
      localStorage.setItem(
        "ecui.streamWorkspaces",
        JSON.stringify(streamWorkspaces)
      );
    } catch {
      // ignore storage errors
    }
  }, [streamWorkspaces]);

  useEffect(() => {
    streamWorkspacesRef.current = streamWorkspaces;
  }, [streamWorkspaces]);

  useEffect(() => {
    streamWorkspaceRevisionsRef.current = streamWorkspaceRevisions;
  }, [streamWorkspaceRevisions]);

  useEffect(() => {
    streamWorkspaceIdRef.current = Math.max(
      streamWorkspaceIdRef.current,
      nextWorkspaceCounter(streamWorkspaces)
    );
  }, [streamWorkspaces]);

  useEffect(() => {
    const ids = Object.keys(streamWorkspaces).sort();
    if (ids.length === 0) {
      return;
    }
    if (!daqWorkspaceId || !streamWorkspaces[daqWorkspaceId]) {
      setDaqWorkspaceId(ids[0]);
    }
  }, [streamWorkspaces, daqWorkspaceId]);

  // Stream-workspace → panel output reconciliation moved to
  // features/panels/useStreamWorkspacePanelReconciler.ts (round 32).
  useStreamWorkspacePanelReconciler();

  useEffect(() => {
    const wasReady = streamAnalysisReadyRef.current;
    streamAnalysisReadyRef.current = streamAnalysisRpcReady;
    if (!streamAnalysisRpcReady) {
      setDaqOpen(false);
      setWorkspaceStoreStatus(normalizeWorkspaceStoreStatus(null));
      return;
    }
    if (wasReady) {
      return;
    }
    void loadStreamAnalysisWorkspaces("stream-analysis-ready", {
      notifyOnError: false,
    });
  }, [streamAnalysisRpcReady]);

  // Resize-while-dragging window listeners moved to useNavResizer (round 31).

  useEffect(() => {
    const handleResize = () => {
      setNavWidth((current) =>
        clampNavWidth(current, { min: NAV_MIN_WIDTH, max: NAV_MAX_WIDTH })
      );
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  useEffect(() => {
    const ids = new Set(panels.map((panel) => panel.id));
    for (const id of buffersRef.keys()) {
      if (!ids.has(id)) {
        buffersRef.delete(id);
      }
    }
    for (const id of streamFramesRef.keys()) {
      if (!ids.has(id)) {
        streamFramesRef.delete(id);
      }
    }
    for (const id of streamTraceOverlayRef.keys()) {
      if (!ids.has(id)) {
        streamTraceOverlayRef.delete(id);
      }
    }
    for (const id of streamBinStatsOverlayRef.keys()) {
      if (!ids.has(id)) {
        streamBinStatsOverlayRef.delete(id);
      }
    }
    for (const id of streamBinStatsFitOverlayRef.keys()) {
      if (!ids.has(id)) {
        streamBinStatsFitOverlayRef.delete(id);
      }
    }
    for (const id of streamParamsLatestRef.keys()) {
      if (!ids.has(id)) {
        streamParamsLatestRef.delete(id);
      }
    }
    for (const id of streamBinStatsRef.keys()) {
      if (!ids.has(id)) {
        streamBinStatsRef.delete(id);
      }
    }
    for (const id of streamBin2dRef.keys()) {
      if (!ids.has(id)) {
        streamBin2dRef.delete(id);
      }
    }
    for (const panel of panels) {
      if (isTelemetryPanel(panel) || isStreamScalarPanel(panel)) {
        streamFramesRef.delete(panel.id);
        streamTraceOverlayRef.delete(panel.id);
        streamBinStatsOverlayRef.delete(panel.id);
        streamBinStatsFitOverlayRef.delete(panel.id);
        streamParamsLatestRef.delete(panel.id);
        streamBinStatsRef.delete(panel.id);
        streamBin2dRef.delete(panel.id);
        if (!buffersRef.has(panel.id)) {
          buffersRef.set(panel.id, new Map());
        }
      } else if (isStreamTracePanel(panel)) {
        streamBinStatsRef.delete(panel.id);
        streamBin2dRef.delete(panel.id);
        streamBinStatsOverlayRef.delete(panel.id);
        streamBinStatsFitOverlayRef.delete(panel.id);
        streamParamsLatestRef.delete(panel.id);
        buffersRef.delete(panel.id);
        if (!streamFramesRef.has(panel.id)) {
          streamFramesRef.set(panel.id, []);
        }
        if (!streamTraceOverlayRef.has(panel.id)) {
          streamTraceOverlayRef.set(panel.id, new Map());
        }
      } else if (isStreamBinStatsPanel(panel)) {
        buffersRef.delete(panel.id);
        streamFramesRef.delete(panel.id);
        streamTraceOverlayRef.delete(panel.id);
        if (!streamBinStatsOverlayRef.has(panel.id)) {
          streamBinStatsOverlayRef.set(panel.id, new Map());
        }
        if (!streamBinStatsFitOverlayRef.has(panel.id)) {
          streamBinStatsFitOverlayRef.set(panel.id, new Map());
        }
        streamBin2dRef.delete(panel.id);
      } else if (isStreamParamsPanel(panel)) {
        buffersRef.delete(panel.id);
        streamFramesRef.delete(panel.id);
        streamTraceOverlayRef.delete(panel.id);
        streamBinStatsOverlayRef.delete(panel.id);
        streamBinStatsFitOverlayRef.delete(panel.id);
        streamBinStatsRef.delete(panel.id);
        streamBin2dRef.delete(panel.id);
        if (!streamParamsLatestRef.has(panel.id)) {
          streamParamsLatestRef.set(panel.id, {});
        }
      } else if (isStreamBin2dPanel(panel)) {
        buffersRef.delete(panel.id);
        streamFramesRef.delete(panel.id);
        streamTraceOverlayRef.delete(panel.id);
        streamBinStatsOverlayRef.delete(panel.id);
        streamBinStatsFitOverlayRef.delete(panel.id);
        streamParamsLatestRef.delete(panel.id);
        streamBinStatsRef.delete(panel.id);
      } else {
        buffersRef.delete(panel.id);
        streamFramesRef.delete(panel.id);
        streamTraceOverlayRef.delete(panel.id);
        streamBinStatsOverlayRef.delete(panel.id);
        streamBinStatsFitOverlayRef.delete(panel.id);
        streamParamsLatestRef.delete(panel.id);
        streamBinStatsRef.delete(panel.id);
        streamBin2dRef.delete(panel.id);
      }
    }
    // P5: keep the trace-key reverse index in sync with the current panel
    // set so the telemetry message handler can route O(1) per signal
    // instead of walking buffersRef.values() per message. Telemetry
    // panels register their (deviceId:signal) trace keys; other panel
    // kinds and removed panels unregister.
    const seenPanelIds = new Set<string>();
    for (const panel of panels) {
      if (isTelemetryPanel(panel)) {
        const traceKeys = panel.traces.map(
          (trace) => `${trace.deviceId}:${trace.signal}`
        );
        registerPanelTraces(panel.id, traceKeys);
        seenPanelIds.add(panel.id);
      }
    }
    for (const id of ids) {
      if (!seenPanelIds.has(id)) {
        unregisterPanelTelemetry(id);
      }
    }
  }, [
    panels,
    buffersRef,
    streamFramesRef,
    streamTraceOverlayRef,
    streamBinStatsOverlayRef,
    streamBinStatsFitOverlayRef,
    streamParamsLatestRef,
    streamBinStatsRef,
    streamBin2dRef,
    registerPanelTraces,
    unregisterPanelTelemetry,
  ]);

  // Telemetry pipeline (round 33). See features/telemetry/useTelemetryPipeline.ts.
  // Owns the /ws/telemetry connection + per-sample fan-out into panel
  // buffers + the boolean-promotion logic on telemetry traces.
  const { latestByDevice, wsConnected, telemetryActive } = useTelemetryPipeline();



  useEffect(() => {
    const currentLastId =
      commandHistoryRows.length > 0
        ? commandHistoryRows[commandHistoryRows.length - 1].id
        : null;
    const previousLastId = commandHistoryLastIdRef.current;
    if (!commandHistoryBaselineReadyRef.current) {
      commandHistoryBaselineReadyRef.current = true;
      commandHistoryLastIdRef.current = currentLastId;
      return;
    }
    if (
      !commandHistoryOpen &&
      currentLastId !== previousLastId
    ) {
      const previousIndex = commandHistoryRows.findIndex(
        (row) => row.id === previousLastId
      );
      const appended =
        previousIndex >= 0
          ? commandHistoryRows.slice(previousIndex + 1)
          : commandHistoryRows;
      if (appended.some((row) => row.response.ok !== true)) {
        setCommandUnreadError(true);
      }
    }
    commandHistoryLastIdRef.current = currentLastId;
  }, [commandHistoryRows, commandHistoryOpen]);

  useEffect(() => {
    const currentLastKey =
      logRows.length > 0 ? logEntryKey(logRows[logRows.length - 1]) : null;
    const previousLastKey = logRowsLastKeyRef.current;
    if (!logRowsBaselineReadyRef.current) {
      logRowsBaselineReadyRef.current = true;
      logRowsLastKeyRef.current = currentLastKey;
      return;
    }
    if (!logsOpen && currentLastKey !== previousLastKey) {
      const previousIndex = logRows.findIndex(
        (entry) => logEntryKey(entry) === previousLastKey
      );
      const appended =
        previousIndex >= 0 ? logRows.slice(previousIndex + 1) : logRows;
      if (appended.some((entry) => isErrorSeverity(entry.severity))) {
        setLogsUnreadError(true);
      }
    }
    logRowsLastKeyRef.current = currentLastKey;
  }, [logRows, logsOpen]);

  useEffect(() => {
    if (commandHistoryOpen) {
      setCommandUnreadError(false);
    }
  }, [commandHistoryOpen]);

  useEffect(() => {
    if (logsOpen) {
      setLogsUnreadError(false);
    }
  }, [logsOpen]);

  const telemetryBadgeColor = !wsConnected
    ? "red"
    : telemetryActive
    ? "teal"
    : "yellow";
  const telemetryBadgeLabel = !wsConnected
    ? "Disconnected"
    : telemetryActive
    ? "Telemetry live"
    : "Telemetry idle";
  const telemetryStreamStatus = !wsConnected
    ? "Disconnected"
    : telemetryActive
    ? "Connected (live)"
    : "Connected (idle)";
  const connectedDeviceCount = useMemo(
    () =>
      devices.reduce((count, device) => {
        return String(device.liveness ?? "").toUpperCase() === "DISCONNECTED"
          ? count
          : count + 1;
      }, 0),
    [devices]
  );

  useEffect(() => {
    if (!logsOpen) {
      return;
    }
    void loadLogTail();
  }, [logsOpen]);

  useEffect(() => {
    if (!settingsOpen) {
      return;
    }
    void loadGatewayRuntimeSettings();
  }, [settingsOpen]);

  useEffect(() => {
    let cancelled = false;
    const bootstrapSettings = async () => {
      try {
        const [next, nextExtraUis] = await Promise.all([
          fetchGatewaySettings(),
          fetchExtraUis(),
        ]);
        if (cancelled || next === null) {
          return;
        }
        setGatewaySettings(next);
        setExtraUis(nextExtraUis);
      } catch {
        return;
      }
    };
    void bootstrapSettings();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    document.title = `Experiment Control ${instanceLabel}`;
  }, [instanceLabel]);

  const { wsConnected: logsWsConnected } = useLogsStream({
    onEntry: (entry) => appendLogEntries([entry]),
  });

  useEffect(() => {
    if (!logsOpen || !logAutoScroll) {
      return;
    }
    const host = logScrollRef.current;
    if (!host) {
      return;
    }
    host.scrollTop = host.scrollHeight;
  }, [filteredLogRows, logsOpen, logAutoScroll]);

  useEffect(() => {
    if (!commandHistoryOpen) {
      commandHistoryNearBottomRef.current = true;
      return;
    }
    const host = commandHistoryScrollRef.current;
    if (!host) {
      return;
    }
    const thresholdPx = 24;
    const updateNearBottom = () => {
      const offset = host.scrollHeight - (host.scrollTop + host.clientHeight);
      commandHistoryNearBottomRef.current = offset <= thresholdPx;
    };
    updateNearBottom();
    host.addEventListener("scroll", updateNearBottom, { passive: true });
    return () => {
      host.removeEventListener("scroll", updateNearBottom);
    };
  }, [
    commandHistoryOpen,
    commandHistoryMode,
    filteredCommandHistoryRows,
    filteredCommandJournalRows,
  ]);

  useEffect(() => {
    if (!commandHistoryOpen || !commandHistoryAutoScroll) {
      return;
    }
    if (commandHistoryMode === "restore") {
      return;
    }
    const host = commandHistoryScrollRef.current;
    if (!host) {
      return;
    }
    if (!commandHistoryNearBottomRef.current) {
      return;
    }
    host.scrollTop = host.scrollHeight;
  }, [
    filteredCommandHistoryRows,
    filteredCommandJournalRows,
    commandHistoryOpen,
    commandHistoryAutoScroll,
    commandHistoryMode,
  ]);

  // Apply-helpers + buffer/capacity helpers moved to
  // features/panels/applyToPanels.ts in round 14. Bind the refs they
  // need into a `deps` object; the imported pure functions take it as
  // their first argument. Wrapping in arrow functions matches the
  // previous inline behavior (no useCallback memoization).
  const applyDeps: ApplyHelpersDeps = {
    panelsRef,
    buffersRef,
    streamFramesRef,
    streamTraceOverlayRef,
    streamBinStatsOverlayRef,
    streamBinStatsFitOverlayRef,
    streamParamsLatestRef,
    streamBinStatsRef,
    streamBin2dRef,
  };
  const panelCapacity = (timeWindow: number) => panelCapacityImpl(timeWindow);
  const ensurePanelBuffers = (panelId: string) =>
    ensurePanelBuffersImpl(buffersRef, panelId);

  const hdfController = useHdfController({
    hdfWriterProcess,
    capabilitiesByProcess,
    processCapabilitiesErrorById,
    latestByDevice: latestByDevice as Record<string, unknown>,
    deviceOrder,
    devices,
    orderedDevices,
    callProcessFn: callProcess,
    sendProcessCommand,
    refreshProcesses,
    refreshDevices,
    ensureProcessCapabilitiesLoaded,
  });
  const influxController = useInfluxController({
    influxWriterProcess,
    capabilitiesByProcess,
    processCapabilitiesErrorById,
    callProcessFn: callProcess,
    sendProcessCommand,
    ensureProcessCapabilitiesLoaded,
  });

  const {
    hdfModalOpen,
    setHdfModalOpen,
    hdfNoteModalOpen,
    setHdfNoteModalOpen,
    hdfRotateFilenameDraft,
    setHdfRotateFilenameDraft,
    hdfRotateDisabledDevicesDraft,
    setHdfRotateDisabledDevicesDraft,
    hdfEnableDevicesDraft,
    setHdfEnableDevicesDraft,
    hdfDisableDevicesDraft,
    setHdfDisableDevicesDraft,
    hdfRotateMeasurementProfileDraft,
    hdfRotateMeasurementValuesDraft,
    hdfRotateMeasurementCustomByField,
    hdfNoteValuesDraft,
    hdfNoteCustomByField,
    executeHdfStatus,
    executeHdfRotate,
    executeHdfMeasurementNote,
    executeHdfDevicesGet,
    executeHdfDevicesEnable,
    executeHdfDevicesDisable,
    hdfWriterProcessId,
    hdfWriterStatus,
    hdfWriterLoading,
    hdfWriterState,
    hdfProcessCapabilitiesError,
    hdfSupportsStatus,
    hdfSupportsDevicesGet,
    hdfSupportsDevicesEnable,
    hdfSupportsDevicesDisable,
    hdfSupportsRotate,
    hdfSupportsMeasurementSchemaGet,
    hdfSupportsMeasurementNote,
    hdfStatusBusy,
    hdfDevicesGetBusy,
    hdfDevicesEnableBusy,
    hdfDevicesDisableBusy,
    hdfRotateBusy,
    hdfMeasurementNoteBusy,
    hdfAnyCommandBusy,
    hdfCommandsBlocked,
    hdfMeasurementSchemaLoading,
    hdfMeasurementSchema,
    hdfMeasurementSchemaConfigured,
    hdfMeasurementSchemaAvailable,
    hdfShowMeasurementUi,
    hdfMeasurementSchemaDisplayPath,
    hdfMeasurementSchemaDisplayError,
    hdfRotateSelectedProfile,
    hdfRotateProfileOptions,
    hdfShowNoteChiplet,
    hdfSelectableDeviceIds,
    hdfSelectableDeviceOptions,
    hdfWriterFileLabel,
    hdfWriterChipColor,
    refreshHdfWriterStatus,
    fetchHdfMeasurementSchema,
    openHdfWriterCommands,
    openHdfMeasurementNoteModal,
    selectHdfRotateMeasurementProfile,
    setHdfRotateFieldValue,
    setHdfRotateFieldUseCustom,
    setHdfNoteFieldValue,
    setHdfNoteFieldUseCustom,
  } = hdfController;
  const {
    influxWriterProcessId,
    influxWriterLoading,
    influxWriterChipColor,
    influxChipLabel,
    influxWriterStatus,
    refreshInfluxStatus,
    openInfluxWriterCommands,
  } = influxController;

  const processCommandController = useProcessCommandController({
    capabilitiesByProcess,
    sendProcessCommand,
    refreshProcesses,
    refreshHdfWriterStatus,
    hdfWriterProcessId,
  });

  const {
    processCommandOpen,
    setProcessCommandOpen,
    processCommandProcessId,
    processCommandAction,
    setProcessCommandParams,
    processCommandParams,
    processCommandParamValues,
    setProcessCommandParamValues,
    processShowAdvancedParams,
    setProcessShowAdvancedParams,
    capabilitiesForProcessCommand,
    activeProcessParams,
    openProcessCommand,
    handleProcessCommandActionChange,
    executeProcessCommand,
    processCommandTitle,
  } = processCommandController;

  const interlocksController = useInterlocksController({
    processes,
    capabilitiesByProcess,
    refreshProcesses,
    ensureProcessCapabilitiesLoaded,
  });

  const {
    interlocksOpen,
    setInterlocksOpen,
    followerRulesByProcessId,
    interlockStatusByProcessId,
    interlocksLoadingByProcessId,
    interlocksErrorByProcessId,
    interlockRuleBusyByKey,
    commandInterceptorRoutes,
    commandInterceptorRoutesLoading,
    commandInterceptorRoutesError,
    interlocksPanelProcesses,
    interlockButtonSummary,
    refreshInterlockProcessStatus,
    refreshInterlocksModalData,
    toggleFollowerRule,
    toggleInterlockRule,
  } = interlocksController;

  const watchdogsController = useWatchdogsController({
    safetyOpen: interlocksOpen,
    processes,
    capabilitiesByProcess,
    refreshProcesses,
    ensureProcessCapabilitiesLoaded,
  });

  const safetyButtonSummary = useMemo(() => {
    const interlockError = interlockButtonSummary.status === "error";
    const watchdogError = watchdogsController.watchdogButtonSummary.status === "error";
    const interlockActive = interlockButtonSummary.activeRuleCount > 0;
    const watchdogActive =
      watchdogsController.watchdogButtonSummary.activeLatchCount +
        watchdogsController.watchdogButtonSummary.activeAlarmCount +
        watchdogsController.watchdogButtonSummary.unknownRuleCount +
        watchdogsController.watchdogButtonSummary.pendingRuleCount >
      0;
    const watchdogActiveCount =
      watchdogsController.watchdogButtonSummary.activeLatchCount +
      watchdogsController.watchdogButtonSummary.activeAlarmCount +
      watchdogsController.watchdogButtonSummary.unknownRuleCount +
      watchdogsController.watchdogButtonSummary.pendingRuleCount;
    const totalActive =
      interlockButtonSummary.activeRuleCount +
      watchdogActiveCount;
    const status = interlockError || watchdogError
      ? "error"
      : interlockActive || watchdogActive
        ? "active"
        : "idle";
    const color = status === "error"
      ? "red"
      : interlockActive
        ? "teal"
      : watchdogActive
          ? watchdogsController.watchdogButtonSummary.color
          : interlockButtonSummary.activeRuleCount === 0 &&
              watchdogsController.watchdogButtonSummary.color === "teal"
            ? "teal"
            : "gray";
    const labelSuffix = totalActive > 0 ? ` (${totalActive})` : "";
    const tooltip = status === "error"
      ? interlockError
        ? interlockButtonSummary.tooltip
        : watchdogsController.watchdogButtonSummary.tooltip
      : totalActive > 0
        ? [
            interlockActive
              ? `${interlockButtonSummary.activeRuleCount} active interlock rule${interlockButtonSummary.activeRuleCount === 1 ? "" : "s"}`
              : null,
            watchdogActive
              ? watchdogsController.watchdogButtonSummary.tooltip
              : null,
          ]
            .filter((part): part is string => Boolean(part))
            .join(" | ")
        : "No active safety rules";
    return {
      color,
      label: `Safety${labelSuffix}`,
      tooltip,
    };
  }, [interlockButtonSummary, watchdogsController.watchdogButtonSummary]);

  const stateMachinesController = useStateMachinesController({
    processes,
    capabilitiesByProcess,
    refreshProcesses,
    ensureProcessCapabilitiesLoaded,
    callProcessFn: callProcess,
  });

  const sequencerController = useSequencerController({
    sequencerProcess,
    callProcessFn: callProcess,
    sendProcessCommand,
    refreshProcesses,
  });

  const {
    sequencerOpen,
    setSequencerOpen,
    sequencerStatus,
    sequencerStatusLoading,
    sequencerProcessState,
    sequencerRuntimeState,
    sequencerLoaded,
    sequencerProgress,
    sequencerProgressPercent,
    sequencerCompletedSteps,
    sequencerTotalSteps,
    sequencerChipSuffix,
    sequencerChipTooltip,
    sequencerPrimaryAction,
    sequencerPrimaryLabel,
    sequencerPrimaryDisabled,
    sequencerActionBusy,
    sequencerValidateBusy,
    sequencerLoadBusy,
    sequencerLoadedYamlBusy,
    sequencerYamlText,
    sequencerYamlViewMode,
    setSequencerYamlViewMode,
    sequencerDiagnostics,
    sequencerModalError,
    sequencerAdaptiveModes,
    sequencerAdaptiveClearBusy,
    sequencerEditorRef,
    sequencerFileInputRef,
    onSequencerYamlTextChange,
    refreshSequencerStatus,
    fetchSequencerLoadedYaml,
    openSequencerModal,
    runSequencerAction,
    setAdaptiveMode,
    clearAdaptiveStudy,
    jumpToSequencerDiagnostic,
    handleSequencerFileInput,
    validateSequencerYaml,
    loadSequencerYaml,
  } = sequencerController;

  const { handleProcessAction } = useProcessLifecycleController({
    processBusyById,
    setProcessBusy,
    invalidateProcessCapabilities,
    refreshProcesses,
    refreshHdfWriterStatus,
    refreshSequencerStatus,
  });

  const {
    deviceBusyById,
    deviceStartAllBusy,
    deviceConnectAllBusy,
    disableStartAllButton,
    disableConnectAllButton,
    handleDeviceConnect,
    handleDeviceDisconnect,
    handleDeviceRestart,
    handleStartAllDevices,
    handleConnectAllDevices,
  } = useDeviceLifecycleController({
    orderedDevices,
    refreshDevices,
    invalidateDeviceCapabilities,
  });

  const handleDeviceTelemetryToggle = useCallback(
    (deviceId: string) => {
      setTelemetryCollapsedByDevice((prev) => ({
        ...prev,
        [deviceId]: !Boolean(prev[deviceId]),
      }));
    },
    []
  );

  const anyDeviceTelemetryExpanded = useMemo(
    () =>
      orderedDevices.some(
        (device) => !Boolean(telemetryCollapsedByDevice[device.device_id])
      ),
    [orderedDevices, telemetryCollapsedByDevice]
  );

  const handleToggleAllDeviceTelemetry = useCallback(() => {
    setTelemetryCollapsedByDevice((prev) => {
      const collapseAll = orderedDevices.some(
        (device) => !Boolean(prev[device.device_id])
      );
      const next: Record<string, boolean> = { ...prev };
      for (const device of orderedDevices) {
        next[device.device_id] = collapseAll;
      }
      return next;
    });
  }, [orderedDevices]);

  const {
    commandOpen,
    setCommandOpen,
    commandDevice,
    commandAction,
    commandParams,
    setCommandParams,
    commandLabel,
    commandParamValues,
    setCommandParamValues,
    showAdvancedParams,
    setShowAdvancedParams,
    commandResponse,
    capabilitiesForActive,
    activeParams,
    isPinned,
    openCommand,
    handleActionChange,
    handleLabelChange,
    handlePinClick,
    executeCommand,
    handlePinnedParamChange,
    handlePinnedCommandSend,
  } = useDeviceCommandController({
    capabilitiesByDevice,
    setCapabilitiesByDevice,
    invalidateDeviceCapabilities,
    pinnedCommands,
    setPinnedCommands,
    pinnedParamDrafts,
    setPinnedParamDrafts,
    pinnedBusyByKey,
    setPinnedBusyByKey,
    sendDeviceCommand,
  });

  // Command-deck pure mutators (round 26). See
  // features/commands/useCommandDeckMutations.ts.
  const {
    updateCommandDeckCommandEntry,
    updateCommandDeckTelemetryEntry,
    removeCommandDeckEntry,
    moveCommandDeckEntryWithinGroup,
    reorderCommandDeckEntryWithinGroup,
    setCommandDeckEntryGroup,
    setCommandDeckGroupEntries,
  } = useCommandDeckMutations();

  // Command-deck create / add-from-modal / run handlers (round 30). See
  // features/commands/useCommandDeckRunner.ts. The "active" half of
  // the deck surface that touches API + capability stores +
  // writer-status refresh.
  const {
    createCommandDeckCommandEntry: _createCommandDeckCommandEntry,
    createCommandDeckTelemetryEntry: _createCommandDeckTelemetryEntry,
    addCommandDeckCommandEntry,
    addCommandDeckTelemetryEntry,
    addToDeckFromCommandModal,
    addToDeckFromProcessCommandModal,
    setCommandDeckEntryTargetKind,
    runCommandDeckEntry,
  } = useCommandDeckRunner({
    processes,
    capabilitiesByProcess,
    ensureProcessCapabilitiesLoaded,
    refreshProcesses,
    setCapabilitiesByDevice,
    latestByDevice,
    sendDeviceCommand,
    sendProcessCommand,
    commandDevice,
    commandAction,
    commandLabel,
    commandParamValues,
    processCommandProcessId,
    processCommandAction,
    processCommandParamValues,
    updateCommandDeckCommandEntry,
    hdfWriterProcessId,
    refreshHdfWriterStatus,
    influxWriterProcessId,
    refreshInfluxStatus,
  });
  void _createCommandDeckCommandEntry;
  void _createCommandDeckTelemetryEntry;

  const copyJsonToClipboard = async (label: string, payload: unknown) => {
    if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) {
      notifications.show({
        color: "red",
        title: "Clipboard unavailable",
        message: "Clipboard API is not available in this browser context.",
      });
      return;
    }
    const text = toPrettyJson(payload);
    try {
      await navigator.clipboard.writeText(text);
      notifications.show({
        color: "teal",
        title: `${label} copied`,
        message: "Copied JSON to clipboard.",
      });
    } catch (error) {
      notifications.show({
        color: "red",
        title: `Failed to copy ${label.toLowerCase()}`,
        message: error instanceof Error ? error.message : "Clipboard write failed.",
      });
    }
  };

  const copyTextToClipboard = async (label: string, text: string) => {
    if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) {
      notifications.show({
        color: "red",
        title: "Clipboard unavailable",
        message: "Clipboard API is not available in this browser context.",
      });
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      notifications.show({
        color: "teal",
        title: `${label} copied`,
        message: "Copied text to clipboard.",
      });
    } catch (error) {
      notifications.show({
        color: "red",
        title: `Failed to copy ${label.toLowerCase()}`,
        message: error instanceof Error ? error.message : "Clipboard write failed.",
      });
    }
  };

  // ensurePanelBuffers / applyRawStreamFrameToPanels /
  // applyStreamAnalysisOutputToPanels were defined here before round 14.
  // They now live in features/panels/applyToPanels.ts and are bound to
  // the local `applyDeps` via thin wrappers earlier in this function
  // (search for `applyDeps:`).

  const applyRawStreamFrameToPanels = (
    subscription: RawStreamSubscription,
    frame: { seq: number; shape: number[]; values: unknown }
  ) => applyRawStreamFrameToPanelsImpl(applyDeps, subscription, frame);

  const applyStreamAnalysisOutputToPanels = (
    output: NonNullable<ReturnType<typeof normalizeStreamAnalysisOutputMessage>>,
    traceFilter?:
      | {
          traceDecimator: StreamTraceDecimator;
          traceMaxPoints: number;
          traceMaxFps: number;
          traceRollingWindow: number;
          traceAverageMode: StreamTraceAverageMode;
        }
      | undefined
  ) => applyStreamAnalysisOutputToPanelsImpl(applyDeps, output, traceFilter);

  // Raw-stream WebSocket subscriptions (round 34). See
  // features/telemetry/useRawStreamSubscriptions.ts. Owns the per-
  // subscription snapshot hydration + live-WS plumbing that used to
  // be two inline useEffects in App.tsx.
  const bumpPlotTick = useCallback(
    () => setPlotTick((tick) => tick + 1),
    [setPlotTick]
  );
  const { wsConnected: streamWsConnected } = useRawStreamSubscriptions({
    activeSubscriptions: activeRawStreamSubscriptions,
    applyFrame: applyRawStreamFrameToPanels,
    bumpPlotTick,
  });
  const { wsConnected: streamAnalysisWsConnected } =
    useStreamAnalysisSubscriptions({
      activeSubscriptions: activeStreamAnalysisWorkspaceSubscriptions,
      streamAnalysisRpcReady,
      applyOutput: applyStreamAnalysisOutputToPanels,
      bumpPlotTick,
    });

  // Workspace-list management (round 24). See
  // features/stream_analysis/useWorkspaceListManagement.ts. Four
  // async handlers (refresh / load / delete / sync) + the
  // buildStreamAnalysisWorkspacePayload helper used by both sync
  // and the still-inline applyDaqWorkspace.
  const {
    refreshWorkspaceStoreStatus,
    loadStreamAnalysisWorkspaces,
    deleteStreamAnalysisWorkspace,
    buildStreamAnalysisWorkspacePayload,
    syncStreamAnalysisWorkspace,
  } = useWorkspaceListManagement();

  useEffect(() => {
    for (const panel of panels) {
      if (isTelemetryPanel(panel)) {
        const panelBuffers = ensurePanelBuffers(panel.id);
        const capacity = panelCapacity(panel.timeWindowS);
        for (const trace of panel.traces) {
          const key = traceKeyId(trace);
          const buffer = panelBuffers.get(key);
          if (buffer) {
            buffer.resize(capacity);
          } else {
            panelBuffers.set(key, new RingBuffer(capacity));
          }
        }
        continue;
      }
      if (isStreamScalarPanel(panel)) {
        const panelBuffers = ensurePanelBuffers(panel.id);
        const capacity = panelCapacity(panel.timeWindowS);
        const key = traceKeyId(streamScalarTrace(panel));
        for (const [bufferKey, buffer] of panelBuffers.entries()) {
          if (bufferKey === key) {
            buffer.resize(capacity);
          } else {
            panelBuffers.delete(bufferKey);
          }
        }
        if (!panelBuffers.has(key)) {
          panelBuffers.set(key, new RingBuffer(capacity));
        }
        continue;
      }
      if (isStreamParamsPanel(panel)) {
        buffersRef.delete(panel.id);
        streamFramesRef.delete(panel.id);
        streamTraceOverlayRef.delete(panel.id);
        streamBinStatsOverlayRef.delete(panel.id);
        streamBinStatsRef.delete(panel.id);
        streamBin2dRef.delete(panel.id);
        continue;
      }
      if (isStreamBinStatsPanel(panel)) {
        buffersRef.delete(panel.id);
        streamBin2dRef.delete(panel.id);
      }
      if (isStreamBin2dPanel(panel)) {
        buffersRef.delete(panel.id);
        streamBinStatsRef.delete(panel.id);
      }
    }
  }, [panels, buffersRef, streamBinStatsRef, streamBin2dRef]);

  // createPanel / removePanel / addTraceToPanel / removeTraceFromPanel /
  // setPanelTimeWindow (round 19). See features/panels/usePanelLifecycle.ts.
  // These are the most cross-context-coupled handlers — createPanel reads
  // PanelsContext + StreamAnalysisContext + TelemetryContext + default
  // constants. The hook takes the App-local editor state + closePlotOptions
  // as args because those haven't been extracted yet.
  // clearPanelBuffers now provided by useStreamPanelHandlers.
  // clearStreamPanelFrames now provided by useStreamPanelHandlers.
  // clearStreamBinStatsPanel now provided by useStreamPanelHandlers.
  // clearStreamBin2dPanel now provided by useStreamPanelHandlers.
  const {
    createPanel,
    removePanel,
    addTraceToPanel,
    removeTraceFromPanel,
    setPanelTimeWindow,
  } = usePanelLifecycle({
    latestByDevice,
    closePlotOptions,
  });

  // setPanelYScaleMode + setPanelManualYRange now provided by
  // usePanelUiHandlers (destructured below alongside the other simple
  // panel-config + modal handlers).

  // resolveTelemetryPanelOffset, setTelemetryYOffsetMode,
  // resolvePanelAutoYRange, openPlotOptions, closePlotOptions,
  // applyPlotOptionsAxis, and setPlotOptionsAxisMode are now
  // provided by usePanelAutoRangeHandlers (destructured above).
  // streamTraceOverlaySeries, streamBinStatsOverlaySeries, and
  // streamBinStatsFitOverlayCurves are pure functions in
  // features/panels/overlayHelpers.ts; the local arrow wrappers near
  // the top of this function bind the overlay refs from
  // TelemetryContext.

  // renderExpandedPlot moved to ExpandedPlotBody (round 28).
  // The expanded-plot modal now renders <ExpandedPlotBody panel={...} />
  // — see App.tsx PlotModalsLayer wiring below.
  // setStreamPanelTargetFromKey now provided by useStreamPanelHandlers.
  // setStreamPanelOverlayCount now provided by useStreamPanelHandlers.
  // setStreamPanelChannelIndex now provided by useStreamPanelHandlers.
  // setStreamPanelTraceDecimator now provided by useStreamPanelHandlers.
  // setStreamPanelTraceMaxPoints now provided by useStreamPanelHandlers.
  // setStreamPanelTraceMaxFps now provided by useStreamPanelHandlers.
  // setStreamPanelRollingWindow now provided by useStreamPanelHandlers.
  // setStreamPanelAverageMode now provided by useStreamPanelHandlers.
  // setStreamTracePanelSourceMode now provided by useStreamWorkspaceHandlers.
  // setStreamTracePanelWorkspace now provided by useStreamWorkspaceHandlers.
  // setStreamTracePanelOutput now provided by useStreamWorkspaceHandlers.
  // setStreamTracePanelOverlayOutputs now provided by useStreamWorkspaceHandlers.
  // setStreamAnalysisPanelWorkspace now provided by useStreamWorkspaceHandlers.
  // setStreamAnalysisPanelOutput now provided by useStreamWorkspaceHandlers.
  // setStreamBinStatsUncertainty + setStreamBinStatsShowBinMarkers now
  // provided by usePanelUiHandlers.
  // setStreamParamsPanelOutputs now provided by useStreamWorkspaceHandlers.
  // setStreamBinStatsOverlayOutputs now provided by useStreamWorkspaceHandlers.
  // setStreamBinStatsFitOverlayOutputs now provided by useStreamWorkspaceHandlers.
  // setStreamBin2dReducer now provided by usePanelUiHandlers.
  // clearWorkspaceBinPanels now provided by useStreamPanelHandlers.
  // Workspace-store CRUD + node-aggregate reset (round 23). See
  // features/stream_analysis/useWorkspaceStoreActions.ts.
  const {
    resetDaqNodeAggregate,
    saveDaqWorkspaceStore,
    reloadDaqWorkspaceStore,
  } = useWorkspaceStoreActions({
    clearWorkspaceBinPanels,
    refreshWorkspaceStoreStatus,
    loadStreamAnalysisWorkspaces,
  });

  // DAQ workspace modal lifecycle (round 22). See
  // features/stream_analysis/useDaqModalLifecycle.ts. Open / close /
  // load / create + the focus-highlight helper. Takes
  // loadStreamAnalysisWorkspaces from App as an arg because it's
  // still defined inline above.
  const {
    focusDaqNodeCard,
    loadDaqWorkspaceDraft,
    createStreamWorkspace,
    openDaqModal,
    closeDaqModal,
  } = useDaqModalLifecycle({
    loadStreamAnalysisWorkspaces,
  });

  // DAQ draft node/output editors (round 21). See
  // features/stream_analysis/useDaqDraftEditors.ts. Pure draft-state
  // mutators — every handler here only touches the draft state in
  // StreamAnalysisContext, never the API or panel/output cascade.
  const {
    setDaqNodeId,
    setDaqNodeOp,
    setDaqNodeInput,
    setDaqNodeParam,
    addDaqNode,
    removeDaqNode,
    setDaqOutputId,
    setDaqOutputNode,
    addDaqOutput,
    removeDaqOutput,
  } = useDaqDraftEditors();

  // applyDaqWorkspace (round 25). See
  // features/stream_analysis/useDaqWorkspaceApply.ts. The heaviest
  // single DAQ handler: validates the draft, commits the workspace
  // locally, cascades the new output set into every panel bound to
  // this workspace, and pushes the result to the runtime.
  const { applyDaqWorkspace } = useDaqWorkspaceApply({
    streamCatalogByKey,
    buildStreamAnalysisWorkspacePayload,
    syncStreamAnalysisWorkspace,
    clearPanelBuffers,
  });

  // saveDaqWorkspaceStore + reloadDaqWorkspaceStore now provided by
  // useWorkspaceStoreActions (round 23).

  // Panel title editor (round 20). See features/panels/usePanelTitleEditor.ts.
  // The editor state itself (editingPanelId, panelTitleDraft) lives in
  // PanelsContext so usePanelLifecycle's removePanel can clear it.
  const {
    startPanelTitleEdit,
    cancelPanelTitleEdit,
    commitPanelTitleEdit,
  } = usePanelTitleEditor();

  const onPlotSignal = useCallback(
    (deviceId: string, signal: string) => {
      const active = panels.find((panel) => panel.id === activePanelId);
      const target =
        (active && isTelemetryPanel(active) ? active : null) ??
        panels.find((panel): panel is PlotTelemetryPanelState =>
          isTelemetryPanel(panel)
        );
      if (!target) {
        notifications.show({
          color: "yellow",
          title: "No telemetry panel",
          message: "Add a telemetry panel first to plot telemetry signals.",
        });
        return;
      }
      addTraceToPanel(target.id, deviceId, signal);
    },
    [panels, activePanelId, addTraceToPanel]
  );

  const sequencerChipProgressStyle = (() => {
    const isRunning =
      sequencerRuntimeState === "RUNNING" ||
      sequencerRuntimeState === "STOP_REQUESTED";
    if (!isRunning || sequencerProgressPercent === null) {
      return undefined;
    }
    const pct = Math.max(0, Math.min(100, sequencerProgressPercent));
    const fill =
      computedColorScheme === "dark"
        ? colorWithAlpha("#12b886", 0.52)
        : colorWithAlpha("#12b886", 0.30);
    return {
      backgroundImage: `linear-gradient(90deg, ${fill} 0%, ${fill} ${pct}%, transparent ${pct}%, transparent 100%)`,
      backgroundRepeat: "no-repeat",
      transition: "background-image 120ms linear",
    };
  })();
  const sequencerPrimaryIcon =
    sequencerPrimaryAction === "pause" ? (
      <IconPlayerPause size={14} />
    ) : (
      <IconPlayerPlay size={14} />
    );
  const sequencerChipColor = sequencerRuntimeStateColor(
    sequencerRuntimeState,
    sequencerProcessState
  );

  const resolvePanelGridColumns = useCallback((): number => {
    if (isNarrowPlotViewport) {
      return 1;
    }
    if (plotWorkspaceColumns !== "auto") {
      const parsed = Number(plotWorkspaceColumns);
      if (Number.isFinite(parsed) && parsed >= 1) {
        return Math.max(1, Math.trunc(parsed));
      }
    }
    return detectGridColumns(plotGridRef.current, "data-panel-card-id");
  }, [isNarrowPlotViewport, plotWorkspaceColumns]);

  const resolveDeviceGridColumns = useCallback((): number => {
    return detectGridColumns(deviceGridRef.current, "data-device-card-id");
  }, []);

  // UI drag controller (round 31). See features/layout/useUiDragController.ts.
  // Owns the dnd-kit sensors + the four drag lifecycle handlers +
  // transient activeUiDrag state.
  const {
    dndSensors,
    activeUiDrag,
    handleUiDragStart,
    handleUiDragOver,
    handleUiDragEnd,
    handleUiDragCancel,
  } = useUiDragController({
    resolveDeviceGridColumns,
    resolvePanelGridColumns,
    addTraceToPanel,
    removeTraceFromPanel,
    reorderCommandDeckEntryWithinGroup,
  });

  // Nav-sidebar resizer (round 31). See features/layout/useNavResizer.ts.
  // Owns pointer-drag resize + collapse/expand helpers that need to
  // cancel any in-flight RAF first.
  const {
    handleNavResizeStart,
    setDevicePanelCollapsed,
    collapseDevicePanel,
    expandDevicePanel,
  } = useNavResizer();

  // UI profile import/export (round 29). See
  // features/runtime/useUiProfile.ts. Wired here (rather than near the
  // other refresh helpers) because applyUiProfileRaw needs the
  // App-local setDevicePanelCollapsed wrapper that lives just above.
  const {
    exportUiProfile,
    importUiProfile,
    loadDefaultUiProfile,
    defaultProfileAvailable,
    defaultProfileLoading,
  } = useUiProfile({
    syncStreamAnalysisWorkspace,
    loadStreamAnalysisWorkspaces,
    setDevicePanelCollapsed,
  });

  // Stable identities for the panel helpers/handlers bags passed to
  // <PanelsGrid>. Without this, every App.tsx render builds new
  // function references and React.memo on <PanelCard> can never hit.
  // Pattern: keep a mutable ref to the latest functions, then expose
  // a single useMemo-stable bag whose methods deref the ref.
  const panelHelpersRef = useRef<PanelsGridHelpers | null>(null);
  panelHelpersRef.current = {
    resolveTelemetryPanelOffset,
    streamTraceOverlaySeries,
    streamBinStatsOverlaySeries,
    streamBinStatsFitOverlayCurves,
    isExpandablePlotPanel,
    copyTextToClipboard,
  };
  const panelHandlersRef = useRef<PanelsGridHandlers | null>(null);
  panelHandlersRef.current = {
    startPanelTitleEdit,
    commitPanelTitleEdit,
    cancelPanelTitleEdit,
    removePanel,
    removeTraceFromPanel,
    setPanelTimeWindow,
    openPlotOptions,
    closePlotOptions,
    applyPlotOptionsAxis,
    setPlotOptionsAxisMode,
    setTelemetryYDisplayMode,
    setTelemetryYOffsetMode,
    setTelemetrySmoothingMode,
    setTelemetrySmoothingWindow,
    clearPanelBuffers,
    clearStreamPanelFrames,
    clearStreamBinStatsPanel,
    clearStreamBin2dPanel,
    setStreamAnalysisPanelWorkspace,
    setStreamAnalysisPanelOutput,
    openExpandedPlot,
    openStreamTraceOptionsModal,
    openStreamBin2dOptionsModal,
    openStreamParamsOptionsModal,
    openStreamBinStatsOptionsModal,
  };
  const stablePanelHelpers = useMemo<PanelsGridHelpers>(
    () => ({
      resolveTelemetryPanelOffset: (panel) =>
        panelHelpersRef.current!.resolveTelemetryPanelOffset(panel),
      streamTraceOverlaySeries: (panel) =>
        panelHelpersRef.current!.streamTraceOverlaySeries(panel),
      streamBinStatsOverlaySeries: (panel) =>
        panelHelpersRef.current!.streamBinStatsOverlaySeries(panel),
      streamBinStatsFitOverlayCurves: (panel) =>
        panelHelpersRef.current!.streamBinStatsFitOverlayCurves(panel),
      isExpandablePlotPanel: (panel) =>
        panelHelpersRef.current!.isExpandablePlotPanel(panel),
      copyTextToClipboard: (label, text) =>
        panelHelpersRef.current!.copyTextToClipboard(label, text),
    }),
    []
  );
  const stablePanelHandlers = useMemo<PanelsGridHandlers>(
    () => ({
      startPanelTitleEdit: (panel) =>
        panelHandlersRef.current!.startPanelTitleEdit(panel),
      commitPanelTitleEdit: () =>
        panelHandlersRef.current!.commitPanelTitleEdit(),
      cancelPanelTitleEdit: () =>
        panelHandlersRef.current!.cancelPanelTitleEdit(),
      removePanel: (panelId) => panelHandlersRef.current!.removePanel(panelId),
      removeTraceFromPanel: (panelId, trace) =>
        panelHandlersRef.current!.removeTraceFromPanel(panelId, trace),
      setPanelTimeWindow: (panelId, value) =>
        panelHandlersRef.current!.setPanelTimeWindow(panelId, value),
      openPlotOptions: (panelId) =>
        panelHandlersRef.current!.openPlotOptions(panelId),
      closePlotOptions: () => panelHandlersRef.current!.closePlotOptions(),
      applyPlotOptionsAxis: (panelId) =>
        panelHandlersRef.current!.applyPlotOptionsAxis(panelId),
      setPlotOptionsAxisMode: (panel, mode) =>
        panelHandlersRef.current!.setPlotOptionsAxisMode(panel, mode),
      setTelemetryYDisplayMode: (panelId, mode) =>
        panelHandlersRef.current!.setTelemetryYDisplayMode(panelId, mode),
      setTelemetryYOffsetMode: (panelId, mode, value) =>
        panelHandlersRef.current!.setTelemetryYOffsetMode(panelId, mode, value),
      setTelemetrySmoothingMode: (panelId, mode) =>
        panelHandlersRef.current!.setTelemetrySmoothingMode(panelId, mode),
      setTelemetrySmoothingWindow: (panelId, value) =>
        panelHandlersRef.current!.setTelemetrySmoothingWindow(panelId, value),
      clearPanelBuffers: (panelId) =>
        panelHandlersRef.current!.clearPanelBuffers(panelId),
      clearStreamPanelFrames: (panelId) =>
        panelHandlersRef.current!.clearStreamPanelFrames(panelId),
      clearStreamBinStatsPanel: (panelId) =>
        panelHandlersRef.current!.clearStreamBinStatsPanel(panelId),
      clearStreamBin2dPanel: (panelId) =>
        panelHandlersRef.current!.clearStreamBin2dPanel(panelId),
      setStreamAnalysisPanelWorkspace: (panelId, workspaceId) =>
        panelHandlersRef.current!.setStreamAnalysisPanelWorkspace(
          panelId,
          workspaceId
        ),
      setStreamAnalysisPanelOutput: (panelId, outputId) =>
        panelHandlersRef.current!.setStreamAnalysisPanelOutput(
          panelId,
          outputId
        ),
      openExpandedPlot: (panelId) =>
        panelHandlersRef.current!.openExpandedPlot(panelId),
      openStreamTraceOptionsModal: (panelId) =>
        panelHandlersRef.current!.openStreamTraceOptionsModal(panelId),
      openStreamBin2dOptionsModal: (panelId) =>
        panelHandlersRef.current!.openStreamBin2dOptionsModal(panelId),
      openStreamParamsOptionsModal: (panelId) =>
        panelHandlersRef.current!.openStreamParamsOptionsModal(panelId),
      openStreamBinStatsOptionsModal: (panelId) =>
        panelHandlersRef.current!.openStreamBinStatsOptionsModal(panelId),
    }),
    []
  );

  const yAxisDraftMinNum = parseNumberInput(yAxisDraftMin);
  const yAxisDraftMaxNum = parseNumberInput(yAxisDraftMax);
  const yAxisDraftInvalid =
    yAxisDraftMinNum === null ||
    yAxisDraftMaxNum === null ||
    yAxisDraftMinNum >= yAxisDraftMaxNum;
  const daqSectionCardStyle =
    computedColorScheme === "dark"
      ? {
          border: "1px solid rgba(148, 163, 184, 0.55)",
          background: "rgba(15, 23, 42, 0.30)",
          boxShadow: "inset 0 1px 0 rgba(226, 232, 240, 0.04)",
        }
      : {
          border: "1px solid rgba(100, 116, 139, 0.35)",
          background: "rgba(248, 250, 252, 0.92)",
          boxShadow: "inset 0 1px 0 rgba(15, 23, 42, 0.03)",
        };
  const daqNodeCardBaseStyle =
    computedColorScheme === "dark"
      ? {
          border: "1px solid rgba(100, 116, 139, 0.70)",
          background: "rgba(15, 23, 42, 0.34)",
        }
      : {
          border: "1px solid rgba(100, 116, 139, 0.45)",
          background: "rgba(255, 255, 255, 0.96)",
        };

  const renderMeasurementFieldInput = (
    field: MeasurementFieldSchema,
    value: string,
    useCustom: boolean,
    onValueChange: (next: string) => void,
    onUseCustomChange: (next: boolean) => void
  ) => {
    const label = field.required ? `${field.label} *` : field.label;
    if (field.options.length > 0) {
      if (field.allowCustom) {
        const selectValue = useCustom
          ? "__custom__"
          : field.options.includes(value)
          ? value
          : null;
        return (
          <Stack gap={4}>
            <Select
              size="xs"
              label={label}
              value={selectValue}
              data={[
                ...field.options.map((option) => ({ value: option, label: option })),
                { value: "__custom__", label: "Custom..." },
              ]}
              searchable
              comboboxProps={{ zIndex: 500 }}
              onChange={(next) => {
                if (next === "__custom__") {
                  onUseCustomChange(true);
                  return;
                }
                onUseCustomChange(false);
                onValueChange(String(next ?? ""));
              }}
              placeholder={field.placeholder ?? "Select value"}
            />
            {useCustom && (
              <TextInput
                size="xs"
                label={`${field.label} (custom)`}
                value={value}
                onChange={(event) => onValueChange(event.currentTarget.value)}
                placeholder={field.placeholder ?? "Enter custom value"}
              />
            )}
          </Stack>
        );
      }
      return (
        <Select
          size="xs"
          label={label}
          value={value || null}
          data={field.options.map((option) => ({ value: option, label: option }))}
          searchable
          comboboxProps={{ zIndex: 500 }}
          onChange={(next) => onValueChange(String(next ?? ""))}
          placeholder={field.placeholder ?? "Select value"}
        />
      );
    }
    if (field.type === "boolean") {
      return (
        <Select
          size="xs"
          label={label}
          value={value || null}
          data={[
            { value: "true", label: "true" },
            { value: "false", label: "false" },
          ]}
          comboboxProps={{ zIndex: 500 }}
          onChange={(next) => onValueChange(String(next ?? ""))}
          placeholder={field.placeholder ?? "Select true/false"}
        />
      );
    }
    if (field.multiline) {
      return (
        <Textarea
          size="xs"
          label={label}
          minRows={2}
          value={value}
          onChange={(event) => onValueChange(event.currentTarget.value)}
          placeholder={field.placeholder ?? undefined}
        />
      );
    }
    return (
      <TextInput
        size="xs"
        label={label}
        value={value}
        onChange={(event) => onValueChange(event.currentTarget.value)}
        placeholder={field.placeholder ?? undefined}
      />
    );
  };

  return (
    <DndContext
      sensors={dndSensors}
      onDragStart={handleUiDragStart}
      onDragOver={handleUiDragOver}
      onDragEnd={handleUiDragEnd}
      onDragCancel={handleUiDragCancel}
    >
      <AppShell
        className="app-shell"
        header={{ height: 72 }}
        padding="lg"
      >
        <DashboardHeaderBar
        instanceLabel={instanceLabel}
        showHdfWriter={Boolean(hdfWriterProcess)}
        hdfWriterChipColor={hdfWriterChipColor}
        hdfWriterLoading={hdfWriterLoading}
        onOpenHdfWriter={openHdfWriterCommands}
        hdfWriterTitle={hdfWriterStatus?.error ?? "Open HDF writer commands"}
        hdfWriterState={hdfWriterState}
        hdfWriterFileLabel={hdfWriterFileLabel}
        showHdfNoteChiplet={hdfShowNoteChiplet}
        hdfMeasurementSchemaLoading={hdfMeasurementSchemaLoading}
        hdfCommandsBlocked={hdfCommandsBlocked}
        hdfMeasurementNoteBusy={hdfMeasurementNoteBusy}
        onOpenHdfMeasurementNote={openHdfMeasurementNoteModal}
        hdfMeasurementSchemaDisplayError={hdfMeasurementSchemaDisplayError}
        hdfMeasurementNotesRows={hdfWriterStatus?.measurementNotesRows ?? 0}
        showInfluxWriter={Boolean(
          influxWriterProcess && isProcessRpcStateAvailable(influxWriterProcess)
        )}
        influxWriterChipColor={influxWriterChipColor}
        influxWriterLoading={influxWriterLoading}
        onOpenInfluxWriter={openInfluxWriterCommands}
        influxWriterTitle={influxWriterStatus?.error ?? "Open Influx writer status"}
        influxWriterLabel={influxChipLabel}
        showSequencer={Boolean(sequencerProcess)}
        sequencerChipColor={sequencerChipColor}
        sequencerStatusLoading={sequencerStatusLoading}
        onOpenSequencer={openSequencerModal}
        sequencerStatusError={sequencerStatus?.error ?? null}
        sequencerChipTooltip={sequencerChipTooltip}
        sequencerRuntimeState={sequencerRuntimeState}
        sequencerChipSuffix={sequencerChipSuffix}
        sequencerChipProgressStyle={sequencerChipProgressStyle}
        sequencerPrimaryAction={sequencerPrimaryAction}
        sequencerPrimaryLabel={sequencerPrimaryLabel}
        sequencerPrimaryDisabled={sequencerPrimaryDisabled}
        sequencerActionBusy={sequencerActionBusy}
        sequencerPrimaryIcon={sequencerPrimaryIcon}
        onRunSequencerPrimaryAction={() => runSequencerAction(sequencerPrimaryAction)}
        sequencerLoaded={sequencerLoaded}
        onOpenProcesses={async () => {
          setProcessOpen(true);
          await refreshProcesses();
        }}
        stateMachineButtonSummary={stateMachinesController.stateMachineButtonSummary}
        onOpenStateMachines={() => {
          stateMachinesController.setStateMachinesOpen(true);
          void stateMachinesController.refreshStateMachinesModalData();
        }}
        interlockButtonSummary={safetyButtonSummary}
        onOpenInterlocks={() => setInterlocksOpen(true)}
        showDaqUi={showDaqUi}
        onOpenDaq={openDaqModal}
        daqWorkspaceCount={Object.keys(streamWorkspaces).length}
        commandUnreadError={commandUnreadError}
        onOpenCommandHistory={() => {
          setCommandUnreadError(false);
          setCommandHistoryOpen(true);
        }}
        commandHistoryCount={Math.max(commandHistoryRows.length, commandJournalRows.length)}
        logsUnreadError={logsUnreadError}
        onOpenLogs={() => {
          setLogsUnreadError(false);
          setLogsOpen(true);
        }}
        extraUis={extraUis}
        onOpenSettings={() => setSettingsOpen(true)}
        onRefreshStatus={async () => {
          const [, nextProcesses] = await Promise.all([
            refreshDevices(),
            refreshProcesses(),
            refreshInstanceRuntime(),
          ]);
          const hdfProcess = nextProcesses.find(isHdfWriterProcess);
          if (hdfProcess) {
            await refreshHdfWriterStatus(hdfProcess.process_id);
          }
          const influxProcess = nextProcesses.find(isInfluxWriterProcess);
          if (influxProcess) {
            await refreshInfluxStatus(influxProcess.process_id);
          }
          const seqProcess = nextProcesses.find(isSequencerProcess);
          if (seqProcess) {
            await refreshSequencerStatus(seqProcess.process_id);
          }
        }}
        colorScheme={colorScheme}
        onColorSchemeChange={setColorScheme}
        telemetryBadgeColor={telemetryBadgeColor}
        telemetryBadgeLabel={telemetryBadgeLabel}
        instanceRuntimeStatus={instanceRuntimeStatus}
        instanceRuntimeLoading={instanceRuntimeLoading}
        instanceRuntimeError={instanceRuntimeError}
        onRefreshInstanceRuntimeStatus={refreshInstanceRuntime}
        instanceCleanupBusy={instanceCleanupBusy}
        onRunInstanceCleanupDryRun={() => runInstanceCleanup(true)}
        onRunInstanceCleanupApply={() => runInstanceCleanup(false)}
      />
      <AppShell.Main>
        <div
          className={`app-layout${
            isDevicePanelCollapsed ? " app-layout-device-collapsed" : ""
          }`}
        >
          {isDevicePanelCollapsed ? (
            <div className="device-panel-collapsed-card">
              <button
                type="button"
                className={`device-panel-collapsed-tab${
                  devicePanelTab === "devices"
                    ? " device-panel-collapsed-tab-active"
                    : ""
                }`}
                onClick={() => {
                  setDevicePanelTab("devices");
                  expandDevicePanel();
                }}
                aria-label={`Open devices panel (${connectedDeviceCount} connected)`}
                title={`Devices (${connectedDeviceCount} connected / ${devices.length} total)`}
              >
                <span className="device-panel-collapsed-tab-count">
                  <IconPlug size={12} />
                  {connectedDeviceCount}
                </span>
                <span className="device-panel-collapsed-tab-label">Devices</span>
              </button>
              <button
                type="button"
                className={`device-panel-collapsed-tab${
                  devicePanelTab === "deck"
                    ? " device-panel-collapsed-tab-active"
                    : ""
                }`}
                onClick={() => {
                  setDevicePanelTab("deck");
                  expandDevicePanel();
                }}
                aria-label={`Open command deck (${commandDeck.length} entries)`}
                title={`Command deck (${commandDeck.length} entries)`}
              >
                <span className="device-panel-collapsed-tab-count">
                  <IconTerminal2 size={12} />
                  {commandDeck.length}
                </span>
                <span className="device-panel-collapsed-tab-label">Deck</span>
              </button>
            </div>
          ) : null}
          <section
            className={`device-panel${
              isDevicePanelCollapsed ? " device-panel-collapsed" : ""
            }${isResizing ? " device-panel-resizing" : ""}`}
            style={{ width: isDevicePanelCollapsed ? 0 : navWidth }}
          >
            <Group mb="sm" justify="space-between" align="center">
              <Group gap={8} align="center">
                <SegmentedControl
                  size="xs"
                  value={devicePanelTab}
                  onChange={(value) =>
                    setDevicePanelTab(value === "deck" ? "deck" : "devices")
                  }
                  data={[
                    { value: "devices", label: "Devices" },
                    { value: "deck", label: "Command Deck" },
                  ]}
                />
                {devicePanelTab === "devices" ? (
                  <>
                    <Button
                      size="compact-xs"
                      variant="light"
                      color="indigo"
                      loading={deviceStartAllBusy}
                      disabled={disableStartAllButton}
                      onClick={async () => {
                        await handleStartAllDevices();
                      }}
                    >
                      Start all
                    </Button>
                    <Button
                      size="compact-xs"
                      variant="light"
                      color="teal"
                      loading={deviceConnectAllBusy}
                      disabled={disableConnectAllButton}
                      onClick={async () => {
                        await handleConnectAllDevices();
                      }}
                    >
                      Connect all
                    </Button>
                    {orderedDevices.length > 0 ? (
                      <Button
                        size="compact-xs"
                        variant="subtle"
                        color="gray"
                        onClick={handleToggleAllDeviceTelemetry}
                        title={
                          anyDeviceTelemetryExpanded
                            ? "Collapse telemetry on every device"
                            : "Expand telemetry on every device"
                        }
                      >
                        {anyDeviceTelemetryExpanded ? "Hide all" : "Show all"}
                      </Button>
                    ) : null}
                  </>
                ) : null}
              </Group>
              <Group
                gap={6}
                title={
                  devicePanelTab === "devices"
                    ? `${connectedDeviceCount} connected / ${devices.length} total`
                    : `${commandDeck.length} command deck entries`
                }
              >
                <ActionIcon
                  size="sm"
                  variant="subtle"
                  color="gray"
                  onClick={collapseDevicePanel}
                  aria-label="Collapse device panel"
                  title="Collapse device panel"
                >
                  <IconChevronLeft size={16} />
                </ActionIcon>
                {devicePanelTab === "devices" ? (
                  <IconPlug size={16} />
                ) : (
                  <IconTerminal2 size={16} />
                )}
                <Text size="xs" c="dimmed">
                  {devicePanelTab === "devices"
                    ? connectedDeviceCount
                    : commandDeck.length}
                </Text>
              </Group>
            </Group>
            <ScrollArea h="calc(100vh - 180px)" type="never">
              {devicePanelTab === "devices" ? (
                <SortableContext
                  items={orderedDevices.map((device) =>
                    deviceSortableId(device.device_id)
                  )}
                  strategy={rectSortingStrategy}
                >
                <div
                  className="device-grid"
                  ref={deviceGridRef}
                >
                  {orderedDevices.map((device, idx) => (
                    <ReorderableCardShell
                      key={device.device_id}
                      id={deviceSortableId(device.device_id)}
                      data={{ kind: "device", deviceId: device.device_id }}
                      className="device-card"
                      dataDeviceCardId={device.device_id}
                      dragHandleTitle="Drag from border to reorder devices"
                      style={{
                        position: "relative",
                        animationDelay: `${idx * 45}ms`,
                      }}
                    >
                      <DeviceCard
                        device={device}
                        signals={latestByDevice[device.device_id]}
                        busy={Boolean(deviceBusyById[device.device_id])}
                        onConnect={() => handleDeviceConnect(device.device_id)}
                        onDisconnect={() => handleDeviceDisconnect(device.device_id)}
                        onRestart={() => handleDeviceRestart(device.device_id)}
                        onPlot={(signal) => onPlotSignal(device.device_id, signal)}
                        onCommand={() => openCommand(device.device_id)}
                        telemetryCollapsed={Boolean(
                          telemetryCollapsedByDevice[device.device_id]
                        )}
                        onTelemetryToggle={() =>
                          handleDeviceTelemetryToggle(device.device_id)
                        }
                        pinnedCommands={pinnedCommands[device.device_id] ?? []}
                        onPinnedCommand={(action) => openCommand(device.device_id, action)}
                        onAddPinnedToDeck={(action) => {
                          const draftKey = pinnedCommandKey(device.device_id, action);
                          const label =
                            (pinnedCommands[device.device_id] ?? []).find(
                              (entry) => entry.action === action
                            )?.label ?? undefined;
                          addCommandDeckCommandEntry({
                            targetKind: "device",
                            targetId: device.device_id,
                            action,
                            label,
                            paramsDraft: { ...(pinnedParamDrafts[draftKey] ?? {}) },
                          });
                          notifications.show({
                            color: "teal",
                            title: "Added to command deck",
                            message: `${device.device_id}.${action}`,
                          });
                        }}
                        onAddAllPinnedToDeck={() => {
                          const entries = pinnedCommands[device.device_id] ?? [];
                          if (entries.length === 0) {
                            notifications.show({
                              color: "yellow",
                              title: "No pinned commands",
                              message: `${device.device_id} has no pinned commands.`,
                            });
                            return;
                          }
                          for (const entry of entries) {
                            const action = entry.action;
                            const draftKey = pinnedCommandKey(device.device_id, action);
                            addCommandDeckCommandEntry({
                              targetKind: "device",
                              targetId: device.device_id,
                              action,
                              label: entry.label ?? undefined,
                              paramsDraft: { ...(pinnedParamDrafts[draftKey] ?? {}) },
                            });
                          }
                          notifications.show({
                            color: "teal",
                            title: "Added pinned commands to deck",
                            message: `${device.device_id}: ${entries.length} command${
                              entries.length === 1 ? "" : "s"
                            }`,
                          });
                        }}
                        capabilities={capabilitiesByDevice[device.device_id] ?? []}
                        pinnedParamValuesByAction={Object.fromEntries(
                          (pinnedCommands[device.device_id] ?? []).map((entry) => [
                            entry.action,
                            pinnedParamDrafts[
                              pinnedCommandKey(device.device_id, entry.action)
                            ] ?? {},
                          ])
                        )}
                        pinnedBusyByAction={Object.fromEntries(
                          (pinnedCommands[device.device_id] ?? []).map((entry) => [
                            entry.action,
                            Boolean(
                              pinnedBusyByKey[
                                pinnedCommandKey(device.device_id, entry.action)
                              ]
                            ),
                          ])
                        )}
                        onPinnedParamChange={(action, paramName, value) =>
                          handlePinnedParamChange(
                            device.device_id,
                            action,
                            paramName,
                            value
                          )
                        }
                        onPinnedSend={(action) =>
                          handlePinnedCommandSend(device.device_id, action)
                        }
                      />
                    </ReorderableCardShell>
                  ))}
                </div>
                </SortableContext>
              ) : (
                <CommandDeckPanel
                  entries={commandDeck}
                  devices={orderedDevices}
                  processes={processes}
                  latestSignalsByDevice={latestByDevice}
                  capabilitiesByDevice={capabilitiesByDevice}
                  capabilitiesByProcess={capabilitiesByProcess}
                  busyById={commandDeckBusyById}
                  onAddCommandEntry={() => {
                    const defaultDeviceId = orderedDevices[0]?.device_id ?? "";
                    const defaultProcessId = processes[0]?.process_id ?? "";
                    const created = addCommandDeckCommandEntry(
                      defaultDeviceId
                        ? { targetKind: "device", targetId: defaultDeviceId }
                        : defaultProcessId
                        ? { targetKind: "process", targetId: defaultProcessId }
                        : undefined
                    );
                    if (
                      isCommandDeckCommandEntry(created) &&
                      created.targetKind === "process" &&
                      created.targetId
                    ) {
                      void ensureProcessCapabilitiesLoaded(created.targetId);
                    }
                    return created;
                  }}
                  onAddTelemetryEntry={() => {
                    return addCommandDeckTelemetryEntry({
                      deviceId: orderedDevices[0]?.device_id ?? "",
                    });
                  }}
                  onRunEntry={(entryId) => {
                    void runCommandDeckEntry(entryId);
                  }}
                  onRemoveEntry={removeCommandDeckEntry}
                  onMoveEntryUp={(entryId) =>
                    moveCommandDeckEntryWithinGroup(entryId, -1)
                  }
                  onMoveEntryDown={(entryId) =>
                    moveCommandDeckEntryWithinGroup(entryId, 1)
                  }
                  onUpdateCommandEntryTargetKind={(entryId, targetKind) =>
                    setCommandDeckEntryTargetKind(entryId, targetKind)
                  }
                  onUpdateCommandEntryTarget={(entryId, targetId) => {
                    const entry =
                      commandDeck.find((candidate) => candidate.id === entryId) ?? null;
                    const nextTargetId = targetId.trim();
                    updateCommandDeckCommandEntry(entryId, { targetId });
                    if (
                      entry &&
                      isCommandDeckCommandEntry(entry) &&
                      nextTargetId &&
                      (entry?.targetKind === "process" ||
                        processes.some(
                          (process) => process.process_id === nextTargetId
                        ))
                    ) {
                      void ensureProcessCapabilitiesLoaded(nextTargetId);
                    }
                  }}
                  onUpdateCommandEntryAction={(entryId, action) =>
                    updateCommandDeckCommandEntry(entryId, { action })
                  }
                  onUpdateEntryLabel={(entryId, label) =>
                    commandDeck.find((candidate) => candidate.id === entryId)?.kind ===
                    "telemetry"
                      ? updateCommandDeckTelemetryEntry(entryId, { label })
                      : updateCommandDeckCommandEntry(entryId, { label })
                  }
                  onUpdateEntryGroup={(entryId, group) =>
                    setCommandDeckEntryGroup(entryId, group)
                  }
                  onUpdateGroupEntries={(fromGroup, toGroupRaw) =>
                    setCommandDeckGroupEntries(fromGroup, toGroupRaw)
                  }
                  onUpdateCommandEntryParam={(entryId, paramName, value) => {
                    const entry =
                      commandDeck.find((candidate) => candidate.id === entryId) ?? null;
                    if (!entry || !isCommandDeckCommandEntry(entry)) {
                      return;
                    }
                    updateCommandDeckCommandEntry(entryId, {
                      paramsDraft: {
                        ...(entry.paramsDraft ?? {}),
                        [paramName]: value,
                      },
                    });
                  }}
                  onUpdateTelemetryEntryDevice={(entryId, deviceId) => {
                    const signals = Object.keys(latestByDevice[deviceId] ?? {}).sort(
                      (a, b) => a.localeCompare(b)
                    );
                    const current =
                      commandDeck.find((candidate) => candidate.id === entryId) ?? null;
                    const nextSignal =
                      current && isCommandDeckTelemetryEntry(current)
                        ? current.signal && signals.includes(current.signal)
                          ? current.signal
                          : signals[0] ?? ""
                        : signals[0] ?? "";
                    updateCommandDeckTelemetryEntry(entryId, {
                      deviceId,
                      signal: nextSignal,
                    });
                  }}
                  onUpdateTelemetryEntrySignal={(entryId, signal) =>
                    updateCommandDeckTelemetryEntry(entryId, { signal })
                  }
                  onUpdateTelemetryEntryFormat={(entryId, format) =>
                    updateCommandDeckTelemetryEntry(entryId, { format })
                  }
                  onUpdateTelemetryEntryDecimals={(entryId, decimals) =>
                    updateCommandDeckTelemetryEntry(entryId, { decimals })
                  }
                  collapsedByGroup={commandDeckCollapsedByGroup}
                  setCollapsedByGroup={setCommandDeckCollapsedByGroup}
                />
              )}
            </ScrollArea>
          </section>
          {isDevicePanelCollapsed ? null : (
            <div
              className="layout-resizer"
              onPointerDown={handleNavResizeStart}
              role="separator"
              aria-orientation="vertical"
            />
          )}
          <section
            className={`plot-panel-area${
              isDevicePanelCollapsed ? " plot-panel-area-expanded" : ""
            }`}
          >
            <Stack gap="lg">
              <Group justify="space-between">
                <Group gap={6} align="center">
                  <Text fw={600}>Plot workspace</Text>
                  <Popover
                    opened={plotWorkspaceOptionsOpen}
                    onChange={setPlotWorkspaceOptionsOpen}
                    position="bottom-start"
                    withArrow
                    shadow="md"
                    withinPortal
                    zIndex={700}
                    width={260}
                  >
                    <Popover.Target>
                      <ActionIcon
                        size="sm"
                        variant="light"
                        color="gray"
                        onClick={() =>
                          setPlotWorkspaceOptionsOpen((current) => !current)
                        }
                        aria-label="Plot workspace options"
                        title="Plot workspace options"
                      >
                        <IconSettings size={14} />
                      </ActionIcon>
                    </Popover.Target>
                    <Popover.Dropdown>
                      <Stack gap="xs">
                        <Group justify="space-between" align="center">
                          <Text size="xs" c="dimmed">
                            Columns
                          </Text>
                          <SegmentedControl
                            size="xs"
                            value={plotWorkspaceColumns}
                            onChange={(value) =>
                              setPlotWorkspaceColumns(
                                normalizePlotWorkspaceColumnsSetting(value)
                              )
                            }
                            data={[
                              { value: "auto", label: "Auto" },
                              { value: "1", label: "1" },
                              { value: "2", label: "2" },
                              { value: "3", label: "3" },
                              { value: "4", label: "4" },
                            ]}
                          />
                        </Group>
                        {isNarrowPlotViewport && plotWorkspaceColumns !== "auto" ? (
                          <Text size="xs" c="dimmed">
                            Narrow viewport: forcing 1 column.
                          </Text>
                        ) : null}
                      </Stack>
                    </Popover.Dropdown>
                  </Popover>
                </Group>
                <Menu
                  shadow="md"
                  width={220}
                  position="bottom-end"
                  withArrow
                  withinPortal
                >
                  <Menu.Target>
                    <Button
                      size="xs"
                      variant="light"
                      leftSection={<IconSquarePlus size={14} />}
                    >
                      Add panel
                    </Button>
                  </Menu.Target>
                  <Menu.Dropdown>
                    <Menu.Label>Panel type</Menu.Label>
                    <Menu.Item onClick={() => createPanel("telemetry")}>
                      Telemetry
                    </Menu.Item>
                    <Menu.Item onClick={() => createPanel("stream_raw")}>
                      Stream trace
                    </Menu.Item>
                    <Menu.Item onClick={() => createPanel("stream_waterfall")}>
                      Waterfall
                    </Menu.Item>
                    <Menu.Item onClick={() => createPanel("stream_scalar")}>
                      Stream scalar
                    </Menu.Item>
                    <Menu.Item onClick={() => createPanel("stream_params")}>
                      Stream params
                    </Menu.Item>
                    <Menu.Item onClick={() => createPanel("stream_bin_stats")}>
                      Stream bin stats
                    </Menu.Item>
                    <Menu.Item onClick={() => createPanel("stream_bin2d")}>
                      Stream 2D bins
                    </Menu.Item>
                  </Menu.Dropdown>
                </Menu>
              </Group>
              <PanelsGrid
                streamWorkspaceOptions={streamWorkspaceOptions}
                yAxisDraftInvalid={yAxisDraftInvalid}
                streamWsConnected={streamWsConnected}
                streamAnalysisWsConnected={streamAnalysisWsConnected}
                activeUiDrag={activeUiDrag}
                helpers={stablePanelHelpers}
                handlers={stablePanelHandlers}
              />
            </Stack>
          </section>
        </div>
      </AppShell.Main>

      <PlotModalsLayer
        expandedPlotOpened={expandedPlotPanel !== null}
        onCloseExpandedPlot={closeExpandedPlot}
        expandedPlotTitle={expandedPlotPanel ? `Plot ${expandedPlotPanel.title}` : "Plot"}
        expandedPlotContent={
          expandedPlotPanel ? (
            <ExpandedPlotBody
              panel={expandedPlotPanel}
              resolveTelemetryPanelOffset={resolveTelemetryPanelOffset}
              streamTraceOverlaySeries={streamTraceOverlaySeries}
              streamBinStatsOverlaySeries={streamBinStatsOverlaySeries}
              streamBinStatsFitOverlayCurves={streamBinStatsFitOverlayCurves}
            />
          ) : null
        }
        streamTraceOpened={streamTraceOptionsPanel !== null}
        onCloseStreamTrace={closeStreamTraceOptionsModal}
        streamTracePanel={streamTraceOptionsPanel}
        streamTargetOptions={streamTargetOptions}
        streamWorkspaceOptions={streamWorkspaceOptions}
        streamTraceOutputOptions={streamTraceOptionsTraceOutputOptions}
        streamTraceOverlayOutputOptions={streamTraceOptionsOverlayOutputOptions}
        onSetStreamTraceSourceMode={setStreamTracePanelSourceMode}
        onSetStreamPanelOverlayCount={setStreamPanelOverlayCount}
        onSetStreamPanelRollingWindow={setStreamPanelRollingWindow}
        onSetStreamPanelAverageMode={setStreamPanelAverageMode}
        onSetStreamPanelTargetFromKey={setStreamPanelTargetFromKey}
        onSetStreamPanelChannelIndex={setStreamPanelChannelIndex}
        onSetStreamTraceWorkspace={setStreamTracePanelWorkspace}
        onSetStreamTraceOutput={setStreamTracePanelOutput}
        onSetStreamTraceOverlayOutputs={setStreamTracePanelOverlayOutputs}
        onSetStreamPanelTraceDecimator={setStreamPanelTraceDecimator}
        onSetStreamPanelTraceMaxPoints={setStreamPanelTraceMaxPoints}
        onSetStreamPanelTraceMaxFps={setStreamPanelTraceMaxFps}
        streamBinStatsOpened={streamBinStatsOptionsPanel !== null}
        onCloseStreamBinStats={closeStreamBinStatsOptionsModal}
        streamBinStatsPanel={streamBinStatsOptionsPanel}
        streamBinStatsOutputOptions={streamBinStatsOptionsOutputOptions}
        streamBinStatsTraceOverlayOptions={streamBinStatsOptionsTraceOverlayOptions}
        streamBinStatsFitOverlayOptions={streamBinStatsOptionsFitOverlayOptions}
        streamBinStatsXAxisLabel={streamBinStatsOptionsXLabel}
        onSetStreamAnalysisPanelWorkspace={setStreamAnalysisPanelWorkspace}
        onSetStreamAnalysisPanelOutput={setStreamAnalysisPanelOutput}
        onSetStreamBinStatsOverlayOutputs={setStreamBinStatsOverlayOutputs}
        onSetStreamBinStatsFitOverlayOutputs={setStreamBinStatsFitOverlayOutputs}
        onSetStreamBinStatsUncertainty={setStreamBinStatsUncertainty}
        onSetStreamBinStatsShowBinMarkers={setStreamBinStatsShowBinMarkers}
        streamParamsOpened={streamParamsOptionsPanel !== null}
        onCloseStreamParams={closeStreamParamsOptionsModal}
        streamParamsPanel={streamParamsOptionsPanel}
        streamParamsOutputOptions={streamParamsOutputOptions}
        onSetStreamParamsOutputs={setStreamParamsPanelOutputs}
        streamBin2dOpened={streamBin2dOptionsPanel !== null}
        onCloseStreamBin2d={closeStreamBin2dOptionsModal}
        streamBin2dPanel={streamBin2dOptionsPanel}
        streamBin2dOutputOptions={streamBin2dOptionsOutputOptions}
        streamBin2dXAxisLabel={streamBin2dOptionsXLabel}
        streamBin2dYAxisLabel={streamBin2dOptionsYLabel}
        onSetStreamBin2dReducer={setStreamBin2dReducer}
      />

      <WorkspaceCommandLayer
        daq={{
          opened: daqOpen,
          onClose: closeDaqModal,
          streamWorkspaceOptions,
          streamCatalogByKey,
          daqWorkspaceId,
          onWorkspaceChange: loadDaqWorkspaceDraft,
          onCreateWorkspace: createStreamWorkspace,
          workspaceStoreStatus,
          daqWorkspace,
          daqDraftName,
          onDraftNameChange: setDaqDraftName,
          daqDraftEnabled,
          onDraftEnabledChange: setDaqDraftEnabled,
          daqSectionCardStyle,
          daqNodeCardBaseStyle,
          daqDraftNodes,
          daqDraftOutputs,
          daqFocusedNodeId,
          daqNodeCardRefs,
          daqResetNodeBusyId,
          streamAnalysisRpcReady,
          onResetDaqNodeAggregate: resetDaqNodeAggregate,
          onAddNode: addDaqNode,
          onRemoveNode: removeDaqNode,
          onSetNodeId: setDaqNodeId,
          onSetNodeOp: setDaqNodeOp,
          onSetNodeInput: setDaqNodeInput,
          onSetNodeParam: setDaqNodeParam,
          onAddOutput: addDaqOutput,
          onRemoveOutput: removeDaqOutput,
          onSetOutputId: setDaqOutputId,
          onSetOutputNode: setDaqOutputNode,
          daqPublishableNodeOptions,
          daqResettableNodeIds,
          onFocusNodeCard: focusDaqNodeCard,
          onReloadStore: reloadDaqWorkspaceStore,
          onSaveStore: saveDaqWorkspaceStore,
          workspaceStoreBusyAction,
          onApplyWorkspace: applyDaqWorkspace,
        }}
        deviceCommand={{
          opened: commandOpen,
          onClose: () => setCommandOpen(false),
          title: commandDevice ? (
            <>
              Command{" "}
              <DeviceNameInline
                deviceId={commandDevice}
                device={
                  devices.find((device) => device.device_id === commandDevice) ?? null
                }
              />
            </>
          ) : (
            "Command"
          ),
          capabilities: capabilitiesForActive,
          commandAction,
          onActionChange: handleActionChange,
          commandLabel,
          onLabelChange: handleLabelChange,
          showAdvancedParams,
          onShowAdvancedParamsChange: setShowAdvancedParams,
          activeParams,
          commandParamValues,
          onParamValueChange: (name, value) =>
            setCommandParamValues((prev) => ({
              ...prev,
              [name]: value,
            })),
          commandParams,
          onCommandParamsChange: setCommandParams,
          commandResponse,
          colorScheme: computedColorScheme,
          isPinned,
          pinDisabled: !commandAction || !commandDevice,
          onTogglePin: handlePinClick,
          deckDisabled: !commandAction || !commandDevice,
          onAddToDeck: addToDeckFromCommandModal,
          onExecute: executeCommand,
        }}
      />

      <AppModalsLayer
        hdf={hdfController}
        influx={influxController}
        renderMeasurementFieldInput={renderMeasurementFieldInput}
        processesController={processesController}
        processCommandController={processCommandController}
        processCommandDeckDisabled={!processCommandAction || !processCommandProcessId}
        onAddProcessCommandToDeck={addToDeckFromProcessCommandModal}
        onProcessAction={handleProcessAction}
        settingsOpen={settingsOpen}
        setSettingsOpen={setSettingsOpen}
        settingsFileInputRef={settingsFileInputRef}
        onImportUiProfile={importUiProfile}
        onExportUiProfile={exportUiProfile}
        onLoadDefaultUiProfile={loadDefaultUiProfile}
        defaultUiProfileAvailable={defaultProfileAvailable}
        defaultUiProfileLoading={defaultProfileLoading}
        onReloadSettings={loadGatewayRuntimeSettings}
        settingsLoading={settingsLoading}
        settingsError={settingsError}
        gatewaySettings={gatewaySettings}
        resolvedApiBase={resolvedApiBase}
        resolvedWsBase={resolvedWsBase}
        telemetryStreamStatus={telemetryStreamStatus}
        devices={devices}
        streamCatalog={streamCatalog}
        capabilitiesByDevice={capabilitiesByDevice}
        streamWorkspaces={streamWorkspaces}
        latestSignalsByDevice={latestByDevice}
        interlocksController={interlocksController}
        watchdogsController={watchdogsController}
        stateMachinesController={stateMachinesController}
        sequencerController={sequencerController}
        sequencerPrimaryIcon={sequencerPrimaryIcon}
        sequencerProcessId={sequencerProcess?.process_id ?? null}
        colorScheme={computedColorScheme}
        commandHistoryController={commandHistoryController}
        commandHistoryScrollRef={commandHistoryScrollRef}
        copyJsonToClipboard={copyJsonToClipboard}
        logsOpen={logsOpen}
        setLogsOpen={setLogsOpen}
        logsWsConnected={logsWsConnected}
        filteredLogRows={filteredLogRows}
        logRows={logRows}
        logAutoScroll={logAutoScroll}
        setLogAutoScroll={setLogAutoScroll}
        logLoading={logLoading}
        loadLogTail={loadLogTail}
        logSeenRef={logSeenRef}
        setLogRows={setLogRows}
        setExpandedLogByKey={setExpandedLogByKey}
        logSeverityFilter={logSeverityFilter}
        setLogSeverityFilter={setLogSeverityFilter}
        logSourceFilter={logSourceFilter}
        setLogSourceFilter={setLogSourceFilter}
        logDeviceFilter={logDeviceFilter}
        setLogDeviceFilter={setLogDeviceFilter}
        logProcessFilter={logProcessFilter}
        setLogProcessFilter={setLogProcessFilter}
        logTextFilter={logTextFilter}
        setLogTextFilter={setLogTextFilter}
        logScrollRef={logScrollRef}
        expandedLogByKey={expandedLogByKey}
        copyTextToClipboard={copyTextToClipboard}
        />
      </AppShell>
    </DndContext>
  );
}


