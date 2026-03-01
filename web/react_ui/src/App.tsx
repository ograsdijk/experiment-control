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
  Switch,
  SegmentedControl,
  useComputedColorScheme,
  useMantineColorScheme,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconCheck,
  IconCpu,
  IconFileText,
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
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from "react";
import {
  callDevice,
  buildWsUrl,
  fetchLogTail,
  fetchDevices,
  fetchGatewaySettings,
  fetchStreams,
  callProcess,
  deleteStreamWorkspace,
  fetchStreamWorkspace,
  fetchStreamWorkspaceList,
  fetchStreamWorkspaceStoreStatus,
  putStreamWorkspace,
  reloadStreamWorkspaceStore,
  resetStreamWorkspace,
  saveStreamWorkspaceStore,
  validateStreamWorkspace,
  type GatewaySettingsInfo,
} from "./api";
import { DeviceCard } from "./components/DeviceCard";
import { DeviceNameInline } from "./components/DeviceNameInline";
import { CommandHistoryModal } from "./components/CommandHistoryModal";
import { DaqWorkspacesModal } from "./components/DaqWorkspacesModal";
import { DeviceCommandModal } from "./components/DeviceCommandModal";
import { HdfMeasurementNoteModal } from "./components/HdfMeasurementNoteModal";
import { HdfWriterModal } from "./components/HdfWriterModal";
import { InterlocksModal } from "./components/InterlocksModal";
import { LogsModal } from "./components/LogsModal";
import { PlotPanel, computeTelemetryAutoYRange } from "./components/PlotPanel";
import { ProcessCommandModal } from "./components/ProcessCommandModal";
import { ProcessesModal } from "./components/ProcessesModal";
import { SequencerModal } from "./components/SequencerModal";
import { SettingsModal } from "./components/SettingsModal";
import { StreamBin2dOptionsModal } from "./components/StreamBin2dOptionsModal";
import { StreamBinStatsOptionsModal } from "./components/StreamBinStatsOptionsModal";
import { StreamParamsOptionsModal } from "./components/StreamParamsOptionsModal";
import { StreamParamsPanel } from "./components/StreamParamsPanel";
import { StreamTraceOptionsModal } from "./components/StreamTraceOptionsModal";
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
import { YAxisModal } from "./components/YAxisModal";
import { clampCommandHistoryLimit } from "./features/commands/utils";
import { useCommandHistoryController } from "./features/commands/useCommandHistoryController";
import { sameStringArray, sameStringRecord } from "./features/common/compare";
import {
  normalizeBooleanMap,
  normalizeStringList,
} from "./features/common/normalize";
import {
  collectGridEntries,
  computeHorizontalReorderMode,
  computeInsertIndexFromGrid,
  computeVerticalReorderMode,
} from "./features/layout/reorder";
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
  UiProfileFile,
  UiProfileState,
} from "./features/profile/types";
import {
  clampNavWidth,
  normalizePinnedCommands,
  normalizeUiProfile,
} from "./features/profile/utils";
import { normalizePlotState } from "./features/profile/plot_state";
import { useInterlocksController } from "./features/interlocks/useInterlocksController";
import { useHdfController } from "./features/hdf/useHdfController";
import { useDeviceCapabilitiesController } from "./features/devices/useDeviceCapabilitiesController";
import { useDeviceGridController } from "./features/devices/useDeviceGridController";
import { useDeviceLifecycleController } from "./features/devices/useDeviceLifecycleController";
import { useDeviceCommandController } from "./features/devices/useDeviceCommandController";
import { buildParamDefaults } from "./features/devices/command_schema";
import { useProcessCommandController } from "./features/processes/useProcessCommandController";
import { useProcessLifecycleController } from "./features/processes/useProcessLifecycleController";
import { useProcessesController } from "./features/processes/useProcessesController";
import type {
  DropPayload,
  PanelDragPayload,
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
  ReorderMode,
  StreamAnalysisSettings,
  StreamAnalysisWorkspaceConfig,
  StreamAnalysisWorkspaceSubscription,
  StreamBin2dSnapshot,
  StreamBinStatsSettings,
  StreamBinStatsSnapshot,
  StreamDagNodeConfig,
  StreamDagOpId,
  StreamDagOutputConfig,
  StreamFrameSample,
  StreamTarget,
  StreamTraceAverageMode,
  StreamTraceDecimator,
  StreamTraceSourceMode,
  StreamParamsOutputValue,
  StreamWorkspaceStoreStatus,
  StreamWorkspaceSummary,
  TraceDragPayload,
  YDisplayMode,
  YOffsetMode,
  YScaleMode,
} from "./features/stream/types";
import {
  cloneDagNodes,
  cloneDagOutputs,
  coerceDagParamValue,
  defaultInputsForOp,
  defaultParamsForOp,
  isPublishableNodeKind,
  nodeKindFromOp,
  normalizeDagNode,
  normalizeDagOutput,
  STREAM_DAG_INPUT_KINDS,
  STREAM_DAG_OP_OPTIONS,
  STREAM_DAG_OPS,
} from "./features/stream/dag";
import {
  normalizeHistAggValue,
  normalizeHist2dValue,
  normalizeFitParamsMapValue,
  normalizeStreamAnalysisOutputMessage,
  normalizeStreamFrameMessage,
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
  workspaceFromLegacyPanel,
} from "./features/stream/panel_helpers";
import {
  dagOutputKindColor,
  DEFAULT_BIN2D_OUTPUT_ID,
  DEFAULT_BIN2D_REDUCER,
  DEFAULT_BIN_COUNT,
  DEFAULT_BIN_OUTPUT_ID,
  DEFAULT_BIN_X_MAX,
  DEFAULT_BIN_X_MIN,
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
  defaultStreamWorkspaceName,
  nextWorkspaceCounter,
  normalizeStreamAnalysisSettings,
  normalizeStreamBinStatsSettings,
  normalizeStreamWorkspaceRecord,
  normalizeUncertaintyMode,
  normalizeWorkspaceStoreStatus,
  normalizeWorkspaceSummaries,
  streamWorkspaceSort,
  workspaceBin2dAxisLabel,
  workspaceOutputKind,
  workspaceOutputOptionsByKind,
  workspaceStreamFromGraphNodes,
  workspaceXAxisLabel,
} from "./features/stream/workspace";
import { useSequencerController } from "./features/sequencer/useSequencerController";
import { RingBuffer } from "./utils/ringBuffer";
import { colorWithAlpha, traceColorAt } from "./utils/traceColors";
import {
  CapabilityMember,
  DeviceStatus,
  LogEntry,
  LogMessage,
  PinnedCommand,
  ProcessStatus,
  StreamAnalysisMessage,
  StreamCatalogEntry,
  StreamFrameMessage,
  TelemetryMessage,
  TelemetrySignal,
  TraceKey,
} from "./types";

const DEFAULT_WINDOW_S = 60;
const DEFAULT_BUFFER_POINTS = 500;
const DEFAULT_NAV_WIDTH = 360;
const NAV_MIN_WIDTH = 260;
const NAV_MAX_WIDTH = 900;
const MAX_LOG_ROWS = 2000;
const DEFAULT_COMMAND_HISTORY_LIMIT = 200;
const MIN_COMMAND_HISTORY_LIMIT = 20;
const MAX_COMMAND_HISTORY_LIMIT = 2000;
const COMMAND_HISTORY_LIMIT_BOUNDS = {
  fallback: DEFAULT_COMMAND_HISTORY_LIMIT,
  min: MIN_COMMAND_HISTORY_LIMIT,
  max: MAX_COMMAND_HISTORY_LIMIT,
} as const;
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

export function App() {
  const [navWidth, setNavWidth] = useState(() => {
    try {
      const raw = localStorage.getItem("ecui.navWidth");
      const parsed = raw ? Number(raw) : NaN;
      if (Number.isFinite(parsed)) {
        return clampNavWidth(parsed, { min: NAV_MIN_WIDTH, max: NAV_MAX_WIDTH });
      }
    } catch {
      // ignore storage errors
    }
    return clampNavWidth(DEFAULT_NAV_WIDTH, {
      min: NAV_MIN_WIDTH,
      max: NAV_MAX_WIDTH,
    });
  });
  const resizeRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const [isResizing, setIsResizing] = useState(false);
  const initialPlotState = useMemo(() => {
    try {
      const raw = localStorage.getItem("ecui.plotState");
      if (!raw) {
        return normalizePlotState(null, { defaultWindowS: DEFAULT_WINDOW_S });
      }
      return normalizePlotState(JSON.parse(raw), {
        defaultWindowS: DEFAULT_WINDOW_S,
      });
    } catch {
      return normalizePlotState(null, { defaultWindowS: DEFAULT_WINDOW_S });
    }
  }, []);
  const initialStreamWorkspaceState = useMemo(
    () => ({
      workspaces: {} as Record<string, StreamAnalysisWorkspaceConfig>,
      nextId: 1,
    }),
    []
  );
  const [devices, setDevices] = useState<DeviceStatus[]>([]);
  const [latestByDevice, setLatestByDevice] = useState<LatestSignals>({});
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const computedColorScheme = useComputedColorScheme("light");
  const panelIdRef = useRef(initialPlotState.nextPanelId);
  const [panels, setPanels] = useState<PlotPanelState[]>(
    initialPlotState.panels
  );
  const [activePanelId, setActivePanelId] = useState(
    initialPlotState.activePanelId
  );
  const streamWorkspaceIdRef = useRef(initialStreamWorkspaceState.nextId);
  const [streamWorkspaces, setStreamWorkspaces] = useState<
    Record<string, StreamAnalysisWorkspaceConfig>
  >(initialStreamWorkspaceState.workspaces);
  const [streamWorkspaceRevisions, setStreamWorkspaceRevisions] = useState<
    Record<string, number>
  >({});
  const [workspaceStoreStatus, setWorkspaceStoreStatus] =
    useState<StreamWorkspaceStoreStatus>(() =>
      normalizeWorkspaceStoreStatus(null)
    );
  const [workspaceStoreBusyAction, setWorkspaceStoreBusyAction] = useState<
    "save" | "reload" | null
  >(null);
  const [daqOpen, setDaqOpen] = useState(false);
  const [daqWorkspaceId, setDaqWorkspaceId] = useState<string | null>(
    Object.keys(initialStreamWorkspaceState.workspaces)[0] ?? null
  );
  const [daqDraftName, setDaqDraftName] = useState("");
  const [daqDraftNodes, setDaqDraftNodes] = useState<StreamDagNodeConfig[]>([]);
  const [daqDraftOutputs, setDaqDraftOutputs] = useState<StreamDagOutputConfig[]>([]);
  const [daqDraftEnabled, setDaqDraftEnabled] = useState(true);
  const [daqResetNodeBusyId, setDaqResetNodeBusyId] = useState<string | null>(null);
  const [daqFocusedNodeId, setDaqFocusedNodeId] = useState<string | null>(null);
  const [plotTick, setPlotTick] = useState(0);
  const [yAxisModalPanelId, setYAxisModalPanelId] = useState<string | null>(null);
  const [streamTraceOptionsPanelId, setStreamTraceOptionsPanelId] = useState<
    string | null
  >(null);
  const [streamBinStatsOptionsPanelId, setStreamBinStatsOptionsPanelId] =
    useState<string | null>(null);
  const [streamParamsOptionsPanelId, setStreamParamsOptionsPanelId] =
    useState<string | null>(null);
  const [streamBin2dOptionsPanelId, setStreamBin2dOptionsPanelId] =
    useState<string | null>(null);
  const [yAxisDraftMin, setYAxisDraftMin] = useState<string | number>("");
  const [yAxisDraftMax, setYAxisDraftMax] = useState<string | number>("");
  const [yAxisAutoRange, setYAxisAutoRange] = useState<{
    min: number;
    max: number;
  } | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [telemetryActive, setTelemetryActive] = useState(false);
  const [streamWsConnected, setStreamWsConnected] = useState(false);
  const [streamAnalysisWsConnected, setStreamAnalysisWsConnected] =
    useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [logsWsConnected, setLogsWsConnected] = useState(false);
  const [commandUnreadError, setCommandUnreadError] = useState(false);
  const [logsUnreadError, setLogsUnreadError] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [gatewaySettings, setGatewaySettings] =
    useState<GatewaySettingsInfo | null>(null);
  const [logRows, setLogRows] = useState<LogEntry[]>([]);
  const [logSeverityFilter, setLogSeverityFilter] = useState("all");
  const [logSourceFilter, setLogSourceFilter] = useState("all");
  const [logDeviceFilter, setLogDeviceFilter] = useState("all");
  const [logProcessFilter, setLogProcessFilter] = useState("all");
  const [logTextFilter, setLogTextFilter] = useState("");
  const [logAutoScroll, setLogAutoScroll] = useState(true);
  const [logLoading, setLogLoading] = useState(false);
  const [expandedLogByKey, setExpandedLogByKey] = useState<
    Record<string, boolean>
  >({});
  const [streamCatalog, setStreamCatalog] = useState<StreamCatalogEntry[]>([]);
  const [dragOverPanelTarget, setDragOverPanelTarget] = useState<{
    panelId: string;
    mode: ReorderMode;
  } | null>(null);
  const [dragPanelId, setDragPanelId] = useState<string | null>(null);
  const [editingPanelId, setEditingPanelId] = useState<string | null>(null);
  const [panelTitleDraft, setPanelTitleDraft] = useState("");
  const [deviceOrder, setDeviceOrder] = useState<string[]>(() => {
    try {
      const raw = localStorage.getItem("ecui.deviceOrder");
      if (!raw) {
        return [];
      }
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        return [];
      }
      return parsed
        .map((value) => (typeof value === "string" ? value : ""))
        .filter((value) => value.length > 0);
    } catch {
      return [];
    }
  });
  const [telemetryCollapsedByDevice, setTelemetryCollapsedByDevice] = useState<
    Record<string, boolean>
  >(() => {
    try {
      const raw = localStorage.getItem("ecui.telemetryCollapsedByDevice");
      if (!raw) {
        return {};
      }
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") {
        return {};
      }
      const next: Record<string, boolean> = {};
      for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
        if (typeof key === "string" && typeof value === "boolean") {
          next[key] = value;
        }
      }
      return next;
    } catch {
      return {};
    }
  });
  const [panelInsertIndex, setPanelInsertIndex] = useState<number | null>(null);
  const [pinnedParamDrafts, setPinnedParamDrafts] = useState<PinnedParamDrafts>(
    {}
  );
  const [pinnedBusyByKey, setPinnedBusyByKey] = useState<Record<string, boolean>>(
    {}
  );
  const logSeenRef = useRef<Set<string>>(new Set());
  const logScrollRef = useRef<HTMLDivElement | null>(null);
  const commandHistoryScrollRef = useRef<HTMLDivElement | null>(null);
  const commandHistoryBaselineReadyRef = useRef(false);
  const commandHistoryLastIdRef = useRef<string | null>(null);
  const logRowsBaselineReadyRef = useRef(false);
  const logRowsLastKeyRef = useRef<string | null>(null);
  const settingsFileInputRef = useRef<HTMLInputElement | null>(null);
  const [pinnedCommands, setPinnedCommands] = useState<PinnedCommandMap>(() => {
    try {
      const raw = localStorage.getItem("ecui.pinnedCommands");
      if (!raw) {
        return {};
      }
      const parsed = JSON.parse(raw);
      return normalizePinnedCommands(parsed);
    } catch {
      return {};
    }
  });
  const buffersRef = useMemo(
    () => new Map<string, Map<string, RingBuffer>>(),
    []
  );
  const streamFramesRef = useMemo(
    () => new Map<string, StreamFrameSample[]>(),
    []
  );
  const streamTraceOverlayRef = useMemo(
    () => new Map<string, Map<string, { seq: number; values: number[] }>>(),
    []
  );
  const streamBinStatsOverlayRef = useMemo(
    () => new Map<string, Map<string, { seq: number; values: number[] }>>(),
    []
  );
  const streamParamsLatestRef = useMemo(
    () => new Map<string, Record<string, StreamParamsOutputValue>>(),
    []
  );
  const streamBinStatsRef = useMemo(
    () => new Map<string, StreamBinStatsSnapshot>(),
    []
  );
  const streamBin2dRef = useMemo(
    () => new Map<string, StreamBin2dSnapshot>(),
    []
  );
  const streamWorkspacesRef = useRef<Record<string, StreamAnalysisWorkspaceConfig>>(
    initialStreamWorkspaceState.workspaces
  );
  const daqNodeCardRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const daqNodeFocusTimeoutRef = useRef<number | null>(null);
  const streamWorkspaceRevisionsRef = useRef<Record<string, number>>({});
  const panelsRef = useRef<PlotPanelState[]>(initialPlotState.panels);
  const streamAnalysisReadyRef = useRef(false);

  useEffect(() => {
    return () => {
      if (daqNodeFocusTimeoutRef.current !== null) {
        window.clearTimeout(daqNodeFocusTimeoutRef.current);
        daqNodeFocusTimeoutRef.current = null;
      }
    };
  }, []);

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
  } = useProcessesController({
    callProcessFn: callProcess,
  });

  const {
    commandHistoryOpen,
    setCommandHistoryOpen,
    commandHistoryRows,
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
    sendDeviceCommand,
    sendProcessCommand,
  } = useCommandHistoryController({
    callDeviceFn: callDevice,
    callProcessFn: callProcess,
  });

  const {
    capabilitiesByDevice,
    setCapabilitiesByDevice,
    invalidateDeviceCapabilities,
  } = useDeviceCapabilitiesController(devices);

  const orderedDevices = useMemo(() => {
    const rank = new Map(deviceOrder.map((deviceId, idx) => [deviceId, idx]));
    return [...devices].sort((a, b) => {
      const aRank = rank.get(a.device_id);
      const bRank = rank.get(b.device_id);
      if (aRank != null && bRank != null && aRank !== bRank) {
        return aRank - bRank;
      }
      if (aRank != null) {
        return -1;
      }
      if (bRank != null) {
        return 1;
      }
      return a.device_id.localeCompare(b.device_id);
    });
  }, [devices, deviceOrder]);
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
  const yAxisModalPanel = useMemo(
    () => panels.find((panel) => panel.id === yAxisModalPanelId) ?? null,
    [panels, yAxisModalPanelId]
  );
  const streamTraceOptionsPanel = useMemo(() => {
    const panel = panels.find((entry) => entry.id === streamTraceOptionsPanelId) ?? null;
    if (!panel || !isStreamTracePanel(panel)) {
      return null;
    }
    return panel;
  }, [panels, streamTraceOptionsPanelId]);
  const streamTraceOptionsWorkspace = useMemo(() => {
    if (!streamTraceOptionsPanel || streamTraceOptionsPanel.sourceMode !== "dag") {
      return null;
    }
    return streamWorkspaces[streamTraceOptionsPanel.workspaceId] ?? null;
  }, [streamTraceOptionsPanel, streamWorkspaces]);
  const streamTraceOptionsTraceOutputOptions = useMemo(() => {
    return workspaceOutputOptionsByKind(streamTraceOptionsWorkspace, "trace");
  }, [streamTraceOptionsWorkspace]);
  const streamTraceOptionsOverlayOutputOptions = useMemo(() => {
    const selectedPrimary = String(streamTraceOptionsPanel?.outputId ?? "").trim();
    return streamTraceOptionsTraceOutputOptions.filter(
      (option) => option.value !== selectedPrimary
    );
  }, [streamTraceOptionsTraceOutputOptions, streamTraceOptionsPanel?.outputId]);
  const streamBinStatsOptionsPanel = useMemo(() => {
    const panel = panels.find((entry) => entry.id === streamBinStatsOptionsPanelId) ?? null;
    if (!panel || !isStreamBinStatsPanel(panel)) {
      return null;
    }
    return panel;
  }, [panels, streamBinStatsOptionsPanelId]);
  const streamBinStatsOptionsWorkspace = useMemo(() => {
    if (!streamBinStatsOptionsPanel) {
      return null;
    }
    return streamWorkspaces[streamBinStatsOptionsPanel.workspaceId] ?? null;
  }, [streamBinStatsOptionsPanel, streamWorkspaces]);
  const streamBinStatsOptionsOutputOptions = useMemo(() => {
    return workspaceOutputOptionsByKind(streamBinStatsOptionsWorkspace, "hist_agg");
  }, [streamBinStatsOptionsWorkspace]);
  const streamBinStatsOptionsTraceOverlayOptions = useMemo(() => {
    return workspaceOutputOptionsByKind(streamBinStatsOptionsWorkspace, "trace");
  }, [streamBinStatsOptionsWorkspace]);
  const streamBinStatsOptionsXLabel = useMemo(() => {
    return workspaceXAxisLabel(
      streamBinStatsOptionsWorkspace,
      streamBinStatsOptionsPanel?.outputId ?? null
    );
  }, [streamBinStatsOptionsWorkspace, streamBinStatsOptionsPanel?.outputId]);
  const streamParamsOptionsPanel = useMemo(() => {
    const panel = panels.find((entry) => entry.id === streamParamsOptionsPanelId) ?? null;
    if (!panel || !isStreamParamsPanel(panel)) {
      return null;
    }
    return panel;
  }, [panels, streamParamsOptionsPanelId]);
  const streamParamsOptionsWorkspace = useMemo(() => {
    if (!streamParamsOptionsPanel) {
      return null;
    }
    return streamWorkspaces[streamParamsOptionsPanel.workspaceId] ?? null;
  }, [streamParamsOptionsPanel, streamWorkspaces]);
  const streamParamsOutputOptions = useMemo(() => {
    const scalar = workspaceOutputOptionsByKind(streamParamsOptionsWorkspace, "scalar").map(
      (item) => ({
        value: item.value,
        label: `[scalar] ${item.label}`,
      })
    );
    const paramsMap = workspaceOutputOptionsByKind(
      streamParamsOptionsWorkspace,
      "params_map"
    ).map((item) => ({
      value: item.value,
      label: `[fit params] ${item.label}`,
    }));
    return [...scalar, ...paramsMap];
  }, [streamParamsOptionsWorkspace]);
  const streamBin2dOptionsPanel = useMemo(() => {
    const panel = panels.find((entry) => entry.id === streamBin2dOptionsPanelId) ?? null;
    if (!panel || !isStreamBin2dPanel(panel)) {
      return null;
    }
    return panel;
  }, [panels, streamBin2dOptionsPanelId]);
  const streamBin2dOptionsWorkspace = useMemo(() => {
    if (!streamBin2dOptionsPanel) {
      return null;
    }
    return streamWorkspaces[streamBin2dOptionsPanel.workspaceId] ?? null;
  }, [streamBin2dOptionsPanel, streamWorkspaces]);
  const streamBin2dOptionsOutputOptions = useMemo(() => {
    return workspaceOutputOptionsByKind(streamBin2dOptionsWorkspace, "hist2d");
  }, [streamBin2dOptionsWorkspace]);
  const streamBin2dOptionsXLabel = useMemo(() => {
    return workspaceBin2dAxisLabel(
      streamBin2dOptionsWorkspace,
      streamBin2dOptionsPanel?.outputId ?? null,
      "x"
    );
  }, [streamBin2dOptionsWorkspace, streamBin2dOptionsPanel?.outputId]);
  const streamBin2dOptionsYLabel = useMemo(() => {
    return workspaceBin2dAxisLabel(
      streamBin2dOptionsWorkspace,
      streamBin2dOptionsPanel?.outputId ?? null,
      "y"
    );
  }, [streamBin2dOptionsWorkspace, streamBin2dOptionsPanel?.outputId]);
  const hdfWriterProcess = useMemo(
    () => processes.find(isHdfWriterProcess) ?? null,
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
  const activeRawStreamSubscriptions = useMemo<RawStreamSubscription[]>(() => {
    const out = new Map<string, RawStreamSubscription>();
    for (const panel of panels) {
      if (!isStreamTracePanel(panel) || panel.sourceMode !== "raw" || panel.stream === null) {
        continue;
      }
      const traceDecimator = normalizeTraceDecimator(panel.traceDecimator);
      const traceMaxPoints = normalizeTraceMaxPoints(panel.traceMaxPoints);
      const traceMaxFps = normalizeTraceMaxFps(panel.traceMaxFps);
      const rollingWindow = normalizeTraceRollingWindow(panel.rollingWindow);
      const averageMode = normalizeTraceAverageMode(panel.averageMode);
      const key = [
        panel.stream.deviceId,
        panel.stream.stream,
        String(panel.channelIndex),
        traceDecimator,
        String(traceMaxPoints),
        traceMaxFps.toFixed(3),
        String(rollingWindow),
        averageMode,
      ].join("|");
      out.set(key, {
        deviceId: panel.stream.deviceId,
        stream: panel.stream.stream,
        channelIndex: panel.channelIndex,
        traceDecimator,
        traceMaxPoints,
        traceMaxFps,
        rollingWindow,
        averageMode,
      });
    }
    return [...out.values()].sort((a, b) => {
      if (a.deviceId !== b.deviceId) {
        return a.deviceId.localeCompare(b.deviceId);
      }
      if (a.stream !== b.stream) {
        return a.stream.localeCompare(b.stream);
      }
      if (a.channelIndex !== b.channelIndex) {
        return a.channelIndex - b.channelIndex;
      }
      if (a.traceDecimator !== b.traceDecimator) {
        return a.traceDecimator.localeCompare(b.traceDecimator);
      }
      if (a.traceMaxPoints !== b.traceMaxPoints) {
        return a.traceMaxPoints - b.traceMaxPoints;
      }
      if (a.traceMaxFps !== b.traceMaxFps) {
        return a.traceMaxFps - b.traceMaxFps;
      }
      if (a.rollingWindow !== b.rollingWindow) {
        return a.rollingWindow - b.rollingWindow;
      }
      return a.averageMode.localeCompare(b.averageMode);
    });
  }, [panels]);
  const activeStreamAnalysisWorkspaceSubscriptions = useMemo<
    StreamAnalysisWorkspaceSubscription[]
  >(() => {
    const outputKindsByWorkspace = new Map<
      string,
      Set<"scalar" | "hist_agg" | "hist2d" | "params_map">
    >();
    const traceConfigsByWorkspace = new Map<
      string,
      Map<
        string,
        {
          traceDecimator: StreamTraceDecimator;
          traceMaxPoints: number;
          traceMaxFps: number;
          traceRollingWindow: number;
          traceAverageMode: StreamTraceAverageMode;
        }
      >
    >();
    for (const panel of panels) {
      const workspaceId = String(panel.workspaceId ?? "").trim();
      if (!workspaceId) {
        continue;
      }
      if (isStreamScalarPanel(panel)) {
        const kinds = outputKindsByWorkspace.get(workspaceId) ?? new Set();
        kinds.add("scalar");
        outputKindsByWorkspace.set(workspaceId, kinds);
        continue;
      }
      if (isStreamParamsPanel(panel)) {
        const kinds = outputKindsByWorkspace.get(workspaceId) ?? new Set();
        const workspace = streamWorkspaces[workspaceId] ?? null;
        for (const outputId of panel.outputIds ?? []) {
          const kind = workspaceOutputKind(workspace, outputId);
          if (kind === "scalar" || kind === "params_map") {
            kinds.add(kind);
          }
        }
        outputKindsByWorkspace.set(workspaceId, kinds);
        continue;
      }
      if (isStreamBinStatsPanel(panel)) {
        const kinds = outputKindsByWorkspace.get(workspaceId) ?? new Set();
        kinds.add("hist_agg");
        outputKindsByWorkspace.set(workspaceId, kinds);
        if ((panel.overlayOutputIds ?? []).length > 0) {
          const configs = traceConfigsByWorkspace.get(workspaceId) ?? new Map();
          const traceDecimator = DEFAULT_TRACE_DECIMATOR;
          const traceMaxPoints = DEFAULT_TRACE_MAX_POINTS;
          const traceMaxFps = DEFAULT_TRACE_MAX_FPS;
          const traceRollingWindow = 1;
          const traceAverageMode = DEFAULT_TRACE_AVERAGE_MODE;
          const key = `${traceDecimator}|${traceMaxPoints}|${traceMaxFps.toFixed(3)}|${traceRollingWindow}|${traceAverageMode}`;
          configs.set(key, {
            traceDecimator,
            traceMaxPoints,
            traceMaxFps,
            traceRollingWindow,
            traceAverageMode,
          });
          traceConfigsByWorkspace.set(workspaceId, configs);
        }
        continue;
      }
      if (isStreamBin2dPanel(panel)) {
        const kinds = outputKindsByWorkspace.get(workspaceId) ?? new Set();
        kinds.add("hist2d");
        outputKindsByWorkspace.set(workspaceId, kinds);
        continue;
      }
      if (isStreamTracePanel(panel) && panel.sourceMode === "dag") {
        const configs = traceConfigsByWorkspace.get(workspaceId) ?? new Map();
        const traceDecimator = normalizeTraceDecimator(panel.traceDecimator);
        const traceMaxPoints = normalizeTraceMaxPoints(panel.traceMaxPoints);
        const traceMaxFps = normalizeTraceMaxFps(panel.traceMaxFps);
        const traceRollingWindow = normalizeTraceRollingWindow(panel.rollingWindow);
        const traceAverageMode = normalizeTraceAverageMode(panel.averageMode);
        const key = `${traceDecimator}|${traceMaxPoints}|${traceMaxFps.toFixed(3)}|${traceRollingWindow}|${traceAverageMode}`;
        configs.set(key, {
          traceDecimator,
          traceMaxPoints,
          traceMaxFps,
          traceRollingWindow,
          traceAverageMode,
        });
        traceConfigsByWorkspace.set(workspaceId, configs);
      }
    }
    const workspaceIds = new Set<string>([
      ...outputKindsByWorkspace.keys(),
      ...traceConfigsByWorkspace.keys(),
    ]);
    const out: StreamAnalysisWorkspaceSubscription[] = [];
    for (const workspaceId of [...workspaceIds].sort()) {
      const outputKinds = outputKindsByWorkspace.get(workspaceId);
      if (outputKinds && outputKinds.size > 0) {
        const kinds = [...outputKinds].sort() as Array<
          "scalar" | "hist_agg" | "hist2d" | "params_map"
        >;
        out.push({
          workspaceId,
          kinds,
        });
      }
      const traceConfigs = traceConfigsByWorkspace.get(workspaceId);
      if (traceConfigs && traceConfigs.size > 0) {
        const sortedConfigs = [...traceConfigs.values()].sort((a, b) => {
          if (a.traceDecimator !== b.traceDecimator) {
            return a.traceDecimator.localeCompare(b.traceDecimator);
          }
          if (a.traceMaxPoints !== b.traceMaxPoints) {
            return a.traceMaxPoints - b.traceMaxPoints;
          }
          if (a.traceMaxFps !== b.traceMaxFps) {
            return a.traceMaxFps - b.traceMaxFps;
          }
          if (a.traceRollingWindow !== b.traceRollingWindow) {
            return a.traceRollingWindow - b.traceRollingWindow;
          }
          return a.traceAverageMode.localeCompare(b.traceAverageMode);
        });
        for (const cfg of sortedConfigs) {
          out.push({
            workspaceId,
            kinds: ["trace"],
            traceDecimator: cfg.traceDecimator,
            traceMaxPoints: cfg.traceMaxPoints,
            traceMaxFps: cfg.traceMaxFps,
            traceRollingWindow: cfg.traceRollingWindow,
            traceAverageMode: cfg.traceAverageMode,
          });
        }
      }
    }
    return out;
  }, [panels, streamWorkspaces]);
  const filteredLogRows = useMemo(() => {
    const needle = logTextFilter.trim().toLowerCase();
    return logRows.filter((entry) => {
      const severity = String(entry.severity ?? "").toLowerCase();
      if (logSeverityFilter !== "all" && severity !== logSeverityFilter) {
        return false;
      }
      const sourceKind = String(entry.source_kind ?? "").toLowerCase();
      if (logSourceFilter !== "all" && sourceKind !== logSourceFilter) {
        return false;
      }
      const deviceId = String(entry.device_id ?? "");
      if (logDeviceFilter !== "all" && deviceId !== logDeviceFilter) {
        return false;
      }
      const processId = String(entry.process_id ?? "");
      if (logProcessFilter !== "all" && processId !== logProcessFilter) {
        return false;
      }
      if (!needle) {
        return true;
      }
      const haystack = `${entry.topic ?? ""} ${entry.message ?? ""} ${
        entry.payload_json ?? ""
      }`.toLowerCase();
      return haystack.includes(needle);
    });
  }, [
    logRows,
    logSeverityFilter,
    logSourceFilter,
    logDeviceFilter,
    logProcessFilter,
    logTextFilter,
  ]);
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

  const refreshDevices = async () => {
    const next = await fetchDevices();
    setDevices(next);
    return next;
  };

  const refreshStreams = async () => {
    return fetchStreams();
  };

  const loadGatewayRuntimeSettings = async () => {
    setSettingsLoading(true);
    setSettingsError(null);
    try {
      const next = await fetchGatewaySettings();
      if (next === null) {
        setSettingsError("Could not fetch gateway settings.");
        return null;
      }
      setGatewaySettings(next);
      return next;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setSettingsError(message);
      return null;
    } finally {
      setSettingsLoading(false);
    }
  };

  const exportUiProfile = () => {
    try {
      const profile: UiProfileFile = {
        kind: "experiment-control-ui-profile",
        version: 1,
        exported_at: new Date().toISOString(),
        layout: {
          nav_width: navWidth,
          device_order: [...deviceOrder],
          telemetry_collapsed_by_device: { ...telemetryCollapsedByDevice },
        },
        plots: {
          plot_state: {
            panels: [...panels],
            activePanelId,
          },
        },
        commands: {
          pinned_commands: { ...pinnedCommands },
        },
        analysis: {
          stream_workspaces: { ...streamWorkspaces },
        },
      };
      const text = JSON.stringify(profile, null, 2);
      const now = new Date();
      const stamp = `${now.getFullYear()}_${String(
        now.getMonth() + 1
      ).padStart(2, "0")}_${String(now.getDate()).padStart(2, "0")}-${String(
        now.getHours()
      ).padStart(2, "0")}_${String(now.getMinutes()).padStart(
        2,
        "0"
      )}_${String(now.getSeconds()).padStart(2, "0")}`;
      const filename = `ec_ui_profile_${stamp}.json`;
      const blob = new Blob([text], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      notifications.show({
        color: "teal",
        title: "UI profile exported",
        message: filename,
      });
    } catch (error) {
      notifications.show({
        color: "red",
        title: "Export failed",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  };

  const importUiProfile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) {
      return;
    }
    try {
      const rawText = await file.text();
      const raw = JSON.parse(rawText);
      const profile = normalizeUiProfile(raw, {
        defaultNavWidth: DEFAULT_NAV_WIDTH,
        navMin: NAV_MIN_WIDTH,
        navMax: NAV_MAX_WIDTH,
        normalizePlotState,
        normalizeStreamWorkspaceRecord,
      });
      if (!profile) {
        throw new Error("Invalid UI profile format.");
      }
      panelIdRef.current = profile.plotState.nextPanelId;
      setNavWidth(profile.navWidth);
      setPanels(profile.plotState.panels);
      setActivePanelId(profile.plotState.activePanelId);
      setDeviceOrder(profile.deviceOrder);
      setTelemetryCollapsedByDevice(profile.telemetryCollapsedByDevice);
      setPinnedCommands(profile.pinnedCommands);
      {
        const migratedFromPanels: Record<string, StreamAnalysisWorkspaceConfig> = {};
        for (const panel of profile.plotState.panels) {
          if (!isStreamScalarPanel(panel) && !isStreamBinStatsPanel(panel)) {
            continue;
          }
          const workspaceId = String(panel.workspaceId ?? "").trim();
          if (!workspaceId || migratedFromPanels[workspaceId]) {
            continue;
          }
          migratedFromPanels[workspaceId] = workspaceFromLegacyPanel(panel);
        }
        const importedWorkspaces =
          Object.keys(profile.streamWorkspaces).length > 0
            ? profile.streamWorkspaces
            : Object.keys(migratedFromPanels).length > 0
            ? migratedFromPanels
            : streamWorkspacesRef.current;
        setStreamWorkspaces(importedWorkspaces);
        streamWorkspacesRef.current = importedWorkspaces;
        setStreamWorkspaceRevisions({});
        streamWorkspaceRevisionsRef.current = {};
        streamWorkspaceIdRef.current = nextWorkspaceCounter(importedWorkspaces);
        const firstWorkspaceId = Object.keys(importedWorkspaces).sort()[0] ?? null;
        setDaqWorkspaceId(firstWorkspaceId);
        if (streamAnalysisReadyRef.current) {
          for (const workspaceId of Object.keys(importedWorkspaces)) {
            await syncStreamAnalysisWorkspace(workspaceId, "ui-profile-import");
          }
          await loadStreamAnalysisWorkspaces("ui-profile-import", {
            notifyOnError: false,
          });
        }
      }
      notifications.show({
        color: "teal",
        title: "UI profile imported",
        message: file.name,
      });
    } catch (error) {
      notifications.show({
        color: "red",
        title: "Import failed",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  };

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
    setPinnedParamDrafts((prev) => {
      const next: PinnedParamDrafts = { ...prev };
      const validKeys = new Set<string>();
      let changed = false;
      for (const [deviceId, entries] of Object.entries(pinnedCommands)) {
        const capabilities = capabilitiesByDevice[deviceId] ?? [];
        for (const entry of entries) {
          const key = pinnedCommandKey(deviceId, entry.action);
          validKeys.add(key);
          const member = capabilities.find(
            (capability) => capability.name === entry.action
          );
          const params = member?.params ?? [];
          if (params.length === 0) {
            const current = next[key] ?? {};
            if (Object.keys(current).length > 0) {
              next[key] = {};
              changed = true;
            } else if (!(key in next)) {
              next[key] = {};
              changed = true;
            }
            continue;
          }
          const defaults = buildParamDefaults(member);
          const current = next[key] ?? {};
          const merged: Record<string, string> = {};
          for (const param of params) {
            const paramName = param.name;
            if (typeof current[paramName] === "string") {
              merged[paramName] = current[paramName];
            } else {
              merged[paramName] = defaults[paramName] ?? "";
            }
          }
          if (!sameStringRecord(current, merged)) {
            next[key] = merged;
            changed = true;
          } else if (!(key in next)) {
            next[key] = merged;
            changed = true;
          }
        }
      }
      for (const key of Object.keys(next)) {
        if (!validKeys.has(key)) {
          delete next[key];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
    setPinnedBusyByKey((prev) => {
      const next = { ...prev };
      const valid = new Set<string>();
      for (const [deviceId, entries] of Object.entries(pinnedCommands)) {
        for (const entry of entries) {
          valid.add(pinnedCommandKey(deviceId, entry.action));
        }
      }
      let changed = false;
      for (const key of Object.keys(next)) {
        if (!valid.has(key)) {
          delete next[key];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [pinnedCommands, capabilitiesByDevice]);

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
        "ecui.plotState",
        JSON.stringify({ panels, activePanelId })
      );
    } catch {
      // ignore storage errors
    }
  }, [panels, activePanelId]);

  useEffect(() => {
    panelsRef.current = panels;
  }, [panels]);

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

  useEffect(() => {
    setPanels((prev) => {
      let changed = false;
      const next = prev.map((panel) => {
        if (isStreamTracePanel(panel) && panel.sourceMode === "dag") {
          const workspace = streamWorkspaces[panel.workspaceId] ?? null;
          const validTraceOutputIds = new Set(
            workspace?.publishOutputs
              .filter((entry) => workspaceOutputKind(workspace, entry.outputId) === "trace")
              .map((entry) => entry.outputId) ?? []
          );
          const outputId =
            panel.outputId &&
            workspace &&
            workspaceOutputKind(workspace, panel.outputId) === "trace"
              ? panel.outputId
              : defaultOutputForKind(workspace, "trace");
          const overlayOutputIds = (panel.overlayOutputIds ?? []).filter(
            (id) => id !== outputId && validTraceOutputIds.has(id)
          );
          const currentStreamKey = panel.stream
            ? streamTargetKey(panel.stream.deviceId, panel.stream.stream)
            : "";
          const workspaceStreamKey = workspace?.stream
            ? streamTargetKey(workspace.stream.deviceId, workspace.stream.stream)
            : "";
          const streamChanged = currentStreamKey !== workspaceStreamKey;
          const channelChanged = panel.channelIndex !== (workspace?.channelIndex ?? panel.channelIndex);
          const outputChanged = panel.outputId !== outputId;
          const overlayChanged = !sameStringArray(panel.overlayOutputIds ?? [], overlayOutputIds);
          if (!streamChanged && !channelChanged && !outputChanged && !overlayChanged) {
            return panel;
          }
          changed = true;
          return {
            ...panel,
            outputId,
            overlayOutputIds,
            stream: workspace?.stream ?? panel.stream,
            channelIndex: workspace?.channelIndex ?? panel.channelIndex,
          };
        }
        if (isStreamScalarPanel(panel)) {
          if (panel.outputId) {
            return panel;
          }
          const workspace = streamWorkspaces[panel.workspaceId] ?? null;
          const outputId = defaultOutputForKind(workspace, "scalar");
          if (!outputId) {
            return panel;
          }
          changed = true;
          return { ...panel, outputId };
        }
        if (isStreamParamsPanel(panel)) {
          const workspace = streamWorkspaces[panel.workspaceId] ?? null;
          const validOutputIds = new Set(
            workspaceOutputOptionsByKind(workspace, "scalar").map((item) => item.value)
          );
          for (const item of workspaceOutputOptionsByKind(workspace, "params_map")) {
            validOutputIds.add(item.value);
          }
          const outputIds = (panel.outputIds ?? []).filter((id) => validOutputIds.has(id));
          if (sameStringArray(panel.outputIds ?? [], outputIds)) {
            return panel;
          }
          changed = true;
          return { ...panel, outputIds };
        }
        if (isStreamBinStatsPanel(panel)) {
          const workspace = streamWorkspaces[panel.workspaceId] ?? null;
          const outputId =
            panel.outputId &&
            workspace &&
            workspaceOutputKind(workspace, panel.outputId) === "hist_agg"
              ? panel.outputId
              : defaultOutputForKind(workspace, "hist_agg");
          const validTraceOutputIds = new Set(
            workspaceOutputOptionsByKind(workspace, "trace").map((item) => item.value)
          );
          const overlayOutputIds = (panel.overlayOutputIds ?? []).filter((id) =>
            validTraceOutputIds.has(id)
          );
          const outputChanged = panel.outputId !== outputId;
          const overlayChanged = !sameStringArray(panel.overlayOutputIds ?? [], overlayOutputIds);
          if (!outputChanged && !overlayChanged) {
            return panel;
          }
          changed = true;
          return { ...panel, outputId, overlayOutputIds };
        }
        if (isStreamBin2dPanel(panel)) {
          if (panel.outputId) {
            return panel;
          }
          const workspace = streamWorkspaces[panel.workspaceId] ?? null;
          const outputId = defaultOutputForKind(workspace, "hist2d");
          if (!outputId) {
            return panel;
          }
          changed = true;
          return { ...panel, outputId };
        }
        return panel;
      });
      return changed ? next : prev;
    });
  }, [streamWorkspaces]);

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

  useEffect(() => {
    if (!isResizing) {
      return;
    }
    const handleMove = (event: PointerEvent) => {
      if (!resizeRef.current) {
        return;
      }
      const delta = event.clientX - resizeRef.current.startX;
      const proposed = resizeRef.current.startWidth + delta;
      const max = Math.min(NAV_MAX_WIDTH, window.innerWidth - 320);
      const safeMax = Math.max(NAV_MIN_WIDTH, max);
      const nextWidth = Math.max(NAV_MIN_WIDTH, Math.min(safeMax, proposed));
      setNavWidth(nextWidth);
    };
    const handleUp = () => {
      resizeRef.current = null;
      setIsResizing(false);
    };
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    return () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [isResizing]);

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
        streamBin2dRef.delete(panel.id);
      } else if (isStreamParamsPanel(panel)) {
        buffersRef.delete(panel.id);
        streamFramesRef.delete(panel.id);
        streamTraceOverlayRef.delete(panel.id);
        streamBinStatsOverlayRef.delete(panel.id);
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
        streamParamsLatestRef.delete(panel.id);
        streamBinStatsRef.delete(panel.id);
      } else {
        buffersRef.delete(panel.id);
        streamFramesRef.delete(panel.id);
        streamTraceOverlayRef.delete(panel.id);
        streamBinStatsOverlayRef.delete(panel.id);
        streamParamsLatestRef.delete(panel.id);
        streamBinStatsRef.delete(panel.id);
        streamBin2dRef.delete(panel.id);
      }
    }
  }, [
    panels,
    buffersRef,
    streamFramesRef,
    streamTraceOverlayRef,
    streamBinStatsOverlayRef,
    streamParamsLatestRef,
    streamBinStatsRef,
    streamBin2dRef,
  ]);

  useEffect(() => {
    const ws = new WebSocket(buildWsUrl("/ws/telemetry"));
    ws.onopen = () => {
      setWsConnected(true);
      setTelemetryActive(false);
    };
    ws.onclose = () => {
      setWsConnected(false);
      setTelemetryActive(false);
    };
    ws.onerror = () => {
      // Keep the current state; close handler is the authoritative disconnect signal.
    };
    ws.onmessage = (event) => {
      try {
        setTelemetryActive(true);
        setWsConnected(true);
        const msg = JSON.parse(event.data) as TelemetryMessage;
        if (!msg?.payload?.device_id) {
          return;
        }
        const deviceId = msg.payload.device_id;
        const bundleTs = msg.payload.ts?.t_wall;
        const booleanSignalKeys = new Set<string>();
        setLatestByDevice((prev) => {
          const next = { ...prev };
          const deviceSignals = { ...(next[deviceId] ?? {}) };
          let updated = false;
          for (const [name, signal] of Object.entries(
            msg.payload.signals ?? {}
          )) {
            deviceSignals[name] = signal;
            const traceKey = `${deviceId}:${name}`;
            let plotValue: number | null = null;
            if (typeof signal.value === "number" && Number.isFinite(signal.value)) {
              plotValue = signal.value;
            } else if (typeof signal.value === "boolean") {
              plotValue = signal.value ? 1 : 0;
              booleanSignalKeys.add(traceKey);
            }
            if (plotValue !== null) {
              for (const panelBuffers of buffersRef.values()) {
                const buffer = panelBuffers.get(traceKey);
                if (buffer) {
                  buffer.push(normalizeTime(signal, bundleTs), plotValue);
                  updated = true;
                }
              }
            }
          }
          next[deviceId] = deviceSignals;
          if (updated) {
            setPlotTick((tick) => tick + 1);
          }
          return next;
        });
        if (booleanSignalKeys.size > 0) {
          setPanels((prev) => {
            let changed = false;
            const next = prev.map((panel) => {
              if (!isTelemetryPanel(panel)) {
                return panel;
              }
              let tracesChanged = false;
              const nextTraces = panel.traces.map((trace) => {
                const key = `${trace.deviceId}:${trace.signal}`;
                if (!booleanSignalKeys.has(key) || trace.valueKind === "boolean") {
                  return trace;
                }
                tracesChanged = true;
                changed = true;
                return { ...trace, valueKind: "boolean" as const };
              });
              return tracesChanged ? { ...panel, traces: nextTraces } : panel;
            });
            return changed ? next : prev;
          });
        }
      } catch {
        return;
      }
    };
    return () => {
      ws.close();
    };
  }, [buffersRef]);

  useEffect(() => {
    if (activeRawStreamSubscriptions.length <= 0) {
      setStreamWsConnected(true);
      return;
    }
    let disposed = false;
    const sockets = new Map<string, WebSocket>();
    const openIds = new Set<string>();

    const updateConnected = () => {
      if (disposed) {
        return;
      }
      setStreamWsConnected(openIds.size > 0);
    };

    const onMessage = (subscription: RawStreamSubscription) => (event: MessageEvent<string>) => {
      try {
        const msg = JSON.parse(event.data) as StreamFrameMessage;
        const frame = normalizeStreamFrameMessage(msg);
        if (frame === null) {
          return;
        }
        if (frame.deviceId !== subscription.deviceId || frame.stream !== subscription.stream) {
          return;
        }
        let updated = false;
        for (const panel of panelsRef.current) {
          if (
            !isStreamTracePanel(panel) ||
            panel.sourceMode !== "raw" ||
            panel.stream === null
          ) {
            continue;
          }
          if (
            panel.stream.deviceId !== subscription.deviceId ||
            panel.stream.stream !== subscription.stream
          ) {
            continue;
          }
          if (Math.max(0, Math.trunc(panel.channelIndex)) !== subscription.channelIndex) {
            continue;
          }
          if (normalizeTraceDecimator(panel.traceDecimator) !== subscription.traceDecimator) {
            continue;
          }
          if (normalizeTraceMaxPoints(panel.traceMaxPoints) !== subscription.traceMaxPoints) {
            continue;
          }
          if (normalizeTraceMaxFps(panel.traceMaxFps) !== subscription.traceMaxFps) {
            continue;
          }
          if (normalizeTraceRollingWindow(panel.rollingWindow) !== subscription.rollingWindow) {
            continue;
          }
          if (normalizeTraceAverageMode(panel.averageMode) !== subscription.averageMode) {
            continue;
          }
          const currentFrames = streamFramesRef.get(panel.id) ?? [];
          if (
            currentFrames.length > 0 &&
            currentFrames[currentFrames.length - 1].seq === frame.seq
          ) {
            continue;
          }
          const appended = [
            ...currentFrames,
            {
              seq: frame.seq,
              shape: frame.shape,
              values: frame.values,
            },
          ];
          const keep = Math.max(MAX_STREAM_FRAME_BUFFER, panel.overlayCount * 4);
          const nextFrames =
            appended.length > keep ? appended.slice(appended.length - keep) : appended;
          streamFramesRef.set(panel.id, nextFrames);
          updated = true;
        }
        if (updated) {
          setPlotTick((tick) => tick + 1);
        }
      } catch {
        return;
      }
    };

    for (const subscription of activeRawStreamSubscriptions) {
      const params = new URLSearchParams();
      params.set("device_id", subscription.deviceId);
      params.set("stream", subscription.stream);
      params.set("channel_index", String(subscription.channelIndex));
      params.set("trace_decimator", subscription.traceDecimator);
      params.set("trace_max_points", String(subscription.traceMaxPoints));
      params.set("trace_max_fps", String(subscription.traceMaxFps));
      params.set("rolling_window", String(subscription.rollingWindow));
      params.set("trace_average_mode", subscription.averageMode);
      const query = params.toString();
      const socketKey = `${subscription.deviceId}|${subscription.stream}|${subscription.channelIndex}|${subscription.traceDecimator}|${subscription.traceMaxPoints}|${subscription.traceMaxFps.toFixed(3)}|${subscription.rollingWindow}|${subscription.averageMode}`;
      const ws = new WebSocket(buildWsUrl(`/ws/raw_stream?${query}`));
      ws.onopen = () => {
        openIds.add(socketKey);
        updateConnected();
      };
      ws.onclose = () => {
        openIds.delete(socketKey);
        updateConnected();
      };
      ws.onerror = () => {
        openIds.delete(socketKey);
        updateConnected();
      };
      ws.onmessage = onMessage(subscription);
      sockets.set(socketKey, ws);
    }

    updateConnected();
    return () => {
      disposed = true;
      openIds.clear();
      setStreamWsConnected(false);
      for (const ws of sockets.values()) {
        ws.close();
      }
      sockets.clear();
    };
  }, [activeRawStreamSubscriptions, streamFramesRef]);

  useEffect(() => {
    if (activeStreamAnalysisWorkspaceSubscriptions.length <= 0) {
      setStreamAnalysisWsConnected(streamAnalysisRpcReady);
      return;
    }
    let disposed = false;
    const sockets = new Map<string, WebSocket>();
    const openIds = new Set<string>();

    const updateConnected = () => {
      if (disposed) {
        return;
      }
      setStreamAnalysisWsConnected(openIds.size > 0);
    };

    const onMessage = (
      subscription: StreamAnalysisWorkspaceSubscription
    ) => (event: MessageEvent<string>) => {
      try {
        const msg = JSON.parse(event.data) as StreamAnalysisMessage;
        let updated = false;
        const output = normalizeStreamAnalysisOutputMessage(msg);
        if (output !== null && output.kind === "scalar") {
          const scalar = Number(output.value);
          if (Number.isFinite(scalar)) {
            for (const panel of panelsRef.current) {
              if (!isStreamScalarPanel(panel)) {
                if (isStreamParamsPanel(panel)) {
                  if (panel.workspaceId !== output.workspaceId) {
                    continue;
                  }
                  if (!(panel.outputIds ?? []).includes(output.outputId)) {
                    continue;
                  }
                  const latest = streamParamsLatestRef.get(panel.id) ?? {};
                  latest[output.outputId] = scalar;
                  streamParamsLatestRef.set(panel.id, latest);
                  updated = true;
                }
                continue;
              }
              if (panel.workspaceId !== output.workspaceId) {
                continue;
              }
              if ((panel.outputId ?? "") !== output.outputId) {
                continue;
              }
              const panelBuffers = ensurePanelBuffers(panel.id);
              const key = traceKeyId(streamScalarTrace(panel));
              let buffer = panelBuffers.get(key);
              if (!buffer) {
                buffer = new RingBuffer(panelCapacity(panel.timeWindowS));
                panelBuffers.set(key, buffer);
              }
              buffer.push(output.tWallS, scalar);
              updated = true;
            }
          }
        }
        if (output !== null && output.kind === "params_map") {
          const paramsMap = normalizeFitParamsMapValue(output.value);
          if (paramsMap) {
            for (const panel of panelsRef.current) {
              if (!isStreamParamsPanel(panel)) {
                continue;
              }
              if (panel.workspaceId !== output.workspaceId) {
                continue;
              }
              if (!(panel.outputIds ?? []).includes(output.outputId)) {
                continue;
              }
              const latest = streamParamsLatestRef.get(panel.id) ?? {};
              latest[output.outputId] = paramsMap;
              streamParamsLatestRef.set(panel.id, latest);
              updated = true;
            }
          }
        }
        if (output !== null && output.kind === "hist_agg") {
          const series = normalizeHistAggValue(output.value);
          if (series) {
            for (const panel of panelsRef.current) {
              if (!isStreamBinStatsPanel(panel)) {
                continue;
              }
              if (panel.workspaceId !== output.workspaceId) {
                continue;
              }
              if ((panel.outputId ?? "") !== output.outputId) {
                continue;
              }
              streamBinStatsRef.set(panel.id, series);
              updated = true;
            }
          }
        }
        if (output !== null && output.kind === "hist2d") {
          const snapshot = normalizeHist2dValue(output.value);
          if (snapshot) {
            for (const panel of panelsRef.current) {
              if (!isStreamBin2dPanel(panel)) {
                continue;
              }
              if (panel.workspaceId !== output.workspaceId) {
                continue;
              }
              if ((panel.outputId ?? "") !== output.outputId) {
                continue;
              }
              streamBin2dRef.set(panel.id, snapshot);
              updated = true;
            }
          }
        }
        if (output !== null && output.kind === "trace") {
          const values = normalizeTraceValues(output.value);
          if (values !== null) {
            for (const panel of panelsRef.current) {
              if (
                isStreamBinStatsPanel(panel) &&
                panel.workspaceId === output.workspaceId
              ) {
                const overlayIds = new Set(
                  (panel.overlayOutputIds ?? []).map((id) => String(id ?? "").trim())
                );
                if (overlayIds.has(output.outputId)) {
                  const perPanel = streamBinStatsOverlayRef.get(panel.id) ?? new Map();
                  const seq =
                    output.seq ??
                    (perPanel.get(output.outputId)?.seq ?? 0) + 1;
                  perPanel.set(output.outputId, { seq, values });
                  streamBinStatsOverlayRef.set(panel.id, perPanel);
                  updated = true;
                }
              }
              if (
                !isStreamTracePanel(panel) ||
                panel.sourceMode !== "dag" ||
                panel.workspaceId !== output.workspaceId
              ) {
                continue;
              }
              if (
                subscription.traceDecimator !== undefined &&
                (normalizeTraceDecimator(panel.traceDecimator) !==
                  subscription.traceDecimator ||
                  normalizeTraceMaxPoints(panel.traceMaxPoints) !==
                    subscription.traceMaxPoints ||
                  normalizeTraceMaxFps(panel.traceMaxFps) !==
                    subscription.traceMaxFps ||
                  normalizeTraceRollingWindow(panel.rollingWindow) !==
                    subscription.traceRollingWindow ||
                  normalizeTraceAverageMode(panel.averageMode) !==
                    subscription.traceAverageMode)
              ) {
                continue;
              }
              const primaryOutputId = String(panel.outputId ?? "").trim();
              const overlayOutputIds = new Set(
                (panel.overlayOutputIds ?? []).map((id) => String(id ?? "").trim())
              );
              const isPrimary = primaryOutputId.length > 0 && primaryOutputId === output.outputId;
              const isOverlay = overlayOutputIds.has(output.outputId);
              if (!isPrimary && !isOverlay) {
                continue;
              }
              if (isOverlay) {
                const perPanel = streamTraceOverlayRef.get(panel.id) ?? new Map();
                const seq =
                  output.seq ??
                  (perPanel.get(output.outputId)?.seq ?? 0) + 1;
                perPanel.set(output.outputId, { seq, values });
                streamTraceOverlayRef.set(panel.id, perPanel);
                updated = true;
                continue;
              }
              const currentFrames = streamFramesRef.get(panel.id) ?? [];
              const seq =
                output.seq ??
                (currentFrames.length > 0
                  ? currentFrames[currentFrames.length - 1].seq + 1
                  : 0);
              if (
                currentFrames.length > 0 &&
                currentFrames[currentFrames.length - 1].seq === seq
              ) {
                continue;
              }
              const appended = [
                ...currentFrames,
                {
                  seq,
                  shape: [values.length],
                  values,
                },
              ];
              const keep = Math.max(MAX_STREAM_FRAME_BUFFER, panel.overlayCount * 4);
              const nextFrames =
                appended.length > keep
                  ? appended.slice(appended.length - keep)
                  : appended;
              streamFramesRef.set(panel.id, nextFrames);
              updated = true;
            }
          }
        }
        if (updated) {
          setPlotTick((tick) => tick + 1);
        }
      } catch {
        return;
      }
    };

    for (const subscription of activeStreamAnalysisWorkspaceSubscriptions) {
      const workspaceId = subscription.workspaceId;
      const params = new URLSearchParams();
      if (subscription.kinds.length > 0) {
        params.set("kinds", subscription.kinds.join(","));
      }
      if (
        subscription.kinds.includes("trace") &&
        subscription.traceDecimator !== undefined &&
        subscription.traceMaxPoints !== undefined &&
        subscription.traceMaxFps !== undefined &&
        subscription.traceRollingWindow !== undefined &&
        subscription.traceAverageMode !== undefined
      ) {
        params.set("trace_decimator", subscription.traceDecimator);
        params.set("trace_max_points", String(subscription.traceMaxPoints));
        params.set("trace_max_fps", String(subscription.traceMaxFps));
        params.set("rolling_window", String(subscription.traceRollingWindow));
        params.set("trace_average_mode", subscription.traceAverageMode);
      }
      const query = params.toString();
      const socketKey = `${workspaceId}|${query}`;
      const ws = new WebSocket(
        buildWsUrl(
          `/ws/stream/${encodeURIComponent(workspaceId)}${query ? `?${query}` : ""}`
        )
      );
      ws.onopen = () => {
        openIds.add(socketKey);
        updateConnected();
      };
      ws.onclose = () => {
        openIds.delete(socketKey);
        updateConnected();
      };
      ws.onerror = () => {
        openIds.delete(socketKey);
        updateConnected();
      };
      ws.onmessage = onMessage(subscription);
      sockets.set(socketKey, ws);
    }

    updateConnected();

    return () => {
      disposed = true;
      openIds.clear();
      setStreamAnalysisWsConnected(false);
      for (const ws of sockets.values()) {
        ws.close();
      }
      sockets.clear();
    };
  }, [
    activeStreamAnalysisWorkspaceSubscriptions,
    buffersRef,
    streamAnalysisRpcReady,
    streamTraceOverlayRef,
    streamBinStatsOverlayRef,
    streamParamsLatestRef,
    streamBinStatsRef,
    streamBin2dRef,
  ]);

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
        const next = await fetchGatewaySettings();
        if (cancelled || next === null) {
          return;
        }
        setGatewaySettings(next);
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

  useEffect(() => {
    const ws = new WebSocket(buildWsUrl("/ws/logs"));
    ws.onopen = () => setLogsWsConnected(true);
    ws.onclose = () => setLogsWsConnected(false);
    ws.onerror = () => setLogsWsConnected(false);
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as LogMessage;
        if (msg.topic !== "manager.log") {
          return;
        }
        const entry = normalizeLogEntry(msg.payload);
        if (entry === null) {
          return;
        }
        appendLogEntries([entry]);
      } catch {
        return;
      }
    };
    return () => {
      ws.close();
    };
  }, []);

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
    if (!commandHistoryOpen || !commandHistoryAutoScroll) {
      return;
    }
    const host = commandHistoryScrollRef.current;
    if (!host) {
      return;
    }
    host.scrollTop = host.scrollHeight;
  }, [filteredCommandHistoryRows, commandHistoryOpen, commandHistoryAutoScroll]);

  const panelCapacity = (timeWindow: number) =>
    Math.max(DEFAULT_BUFFER_POINTS, Math.floor(timeWindow * 10));

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
  } = useHdfController({
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

  const {
    processCommandOpen,
    setProcessCommandOpen,
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
  } = useProcessCommandController({
    capabilitiesByProcess,
    sendProcessCommand,
    refreshProcesses,
    refreshHdfWriterStatus,
    hdfWriterProcessId,
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
  } = useInterlocksController({
    processes,
    capabilitiesByProcess,
    refreshProcesses,
    ensureProcessCapabilitiesLoaded,
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
    sequencerEditorRef,
    sequencerFileInputRef,
    onSequencerYamlTextChange,
    refreshSequencerStatus,
    fetchSequencerLoadedYaml,
    openSequencerModal,
    runSequencerAction,
    jumpToSequencerDiagnostic,
    handleSequencerFileInput,
    validateSequencerYaml,
    loadSequencerYaml,
  } = useSequencerController({
    sequencerProcess,
    callProcessFn: callProcess,
    sendProcessCommand,
    refreshProcesses,
  });

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

  const {
    dragDeviceId,
    dragOverDeviceTarget,
    deviceInsertIndex,
    handleDeviceTelemetryToggle,
    handleDeviceDragStart,
    handleDeviceDragEnd,
    handleDeviceDragOver,
    handleDeviceDragLeave,
    handleDeviceDrop,
    handleDeviceGridDragOver,
    handleDeviceGridDrop,
    handleDeviceGridDragLeave,
  } = useDeviceGridController({
    orderedDevices,
    setDeviceOrder,
    setTelemetryCollapsedByDevice,
  });

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

  const ensurePanelBuffers = (panelId: string) => {
    let panelBuffers = buffersRef.get(panelId);
    if (!panelBuffers) {
      panelBuffers = new Map<string, RingBuffer>();
      buffersRef.set(panelId, panelBuffers);
    }
    return panelBuffers;
  };

  const refreshWorkspaceStoreStatus = async (
    source: string,
    options?: { notifyOnError?: boolean }
  ) => {
    if (!streamAnalysisReadyRef.current) {
      setWorkspaceStoreStatus(normalizeWorkspaceStoreStatus(null));
      return;
    }
    const resp = await fetchStreamWorkspaceStoreStatus();
    if (!resp.ok) {
      if (options?.notifyOnError) {
        notifications.show({
          color: "red",
          title: "Workspace store status failed",
          message: `${source}: ${
            resp.error?.message ?? resp.error?.code ?? "workspace_store.status failed"
          }`,
        });
      }
      return;
    }
    setWorkspaceStoreStatus(normalizeWorkspaceStoreStatus(resp.result));
  };

  const loadStreamAnalysisWorkspaces = async (
    source: string,
    options?: { notifyOnError?: boolean }
  ) => {
    if (!streamAnalysisReadyRef.current) {
      return false;
    }
    const listResp = await fetchStreamWorkspaceList();
    if (!listResp.ok) {
      if (options?.notifyOnError) {
        notifications.show({
          color: "red",
          title: "Workspace load failed",
          message: `${source}: ${
            listResp.error?.message ?? listResp.error?.code ?? "workspace.list failed"
          }`,
        });
      }
      return false;
    }
    const listRaw = Array.isArray(listResp.result?.workspaces)
      ? listResp.result.workspaces
      : [];
    const summaries = normalizeWorkspaceSummaries(listRaw);
    const summaryById = new Map<string, Record<string, unknown>>();
    for (const item of listRaw) {
      if (!item || typeof item !== "object") {
        continue;
      }
      const obj = item as Record<string, unknown>;
      const workspaceId = String(obj.workspace_id ?? "").trim();
      if (!workspaceId) {
        continue;
      }
      summaryById.set(workspaceId, obj);
    }
    const rawRecord: Record<string, unknown> = {};
    await Promise.all(
      summaries.map(async (summary) => {
        const getResp = await fetchStreamWorkspace(summary.workspaceId);
        if (getResp.ok && getResp.result && typeof getResp.result === "object") {
          const raw = (getResp.result as { raw?: unknown }).raw;
          if (raw && typeof raw === "object") {
            rawRecord[summary.workspaceId] = raw as Record<string, unknown>;
            return;
          }
        }
        const fallback = summaryById.get(summary.workspaceId);
        if (!fallback) {
          return;
        }
        const graph =
          fallback.graph && typeof fallback.graph === "object"
            ? (fallback.graph as Record<string, unknown>)
            : {};
        const publish =
          fallback.publish && typeof fallback.publish === "object"
            ? (fallback.publish as Record<string, unknown>)
            : {};
        rawRecord[summary.workspaceId] = {
          workspace_id: summary.workspaceId,
          name:
            typeof fallback.name === "string" && fallback.name.trim().length > 0
              ? fallback.name.trim()
              : undefined,
          enabled: fallback.enabled !== false,
          graph,
          publish,
        };
      })
    );
    const normalized = normalizeStreamWorkspaceRecord(rawRecord);
    const revisions: Record<string, number> = {};
    for (const summary of summaries) {
      if (normalized[summary.workspaceId]) {
        revisions[summary.workspaceId] = summary.revision;
      }
    }
    setStreamWorkspaces(normalized);
    streamWorkspacesRef.current = normalized;
    setStreamWorkspaceRevisions(revisions);
    streamWorkspaceRevisionsRef.current = revisions;
    streamWorkspaceIdRef.current = nextWorkspaceCounter(normalized);
    await refreshWorkspaceStoreStatus(source, { notifyOnError: false });
    return true;
  };

  const deleteStreamAnalysisWorkspace = async (workspaceId: string, source: string) => {
    if (!streamAnalysisReadyRef.current) {
      return;
    }
    const expectedRevision =
      streamWorkspaceRevisionsRef.current[workspaceId] ?? null;
    const resp = await deleteStreamWorkspace(workspaceId, expectedRevision);
    if (resp.ok) {
      setStreamWorkspaceRevisions((prev) => {
        if (!(workspaceId in prev)) {
          return prev;
        }
        const next = { ...prev };
        delete next[workspaceId];
        streamWorkspaceRevisionsRef.current = next;
        return next;
      });
      await refreshWorkspaceStoreStatus(source, { notifyOnError: false });
      return;
    }
    if (String(resp.error?.code ?? "").toLowerCase() === "revision_conflict") {
      notifications.show({
        color: "yellow",
        title: "Workspace changed elsewhere",
        message: `${source}: Reloaded latest workspace state.`,
      });
      await loadStreamAnalysisWorkspaces(source, { notifyOnError: false });
      return;
    }
    if (
      String(resp.error?.code ?? "").toLowerCase() !== "unknown_workspace"
    ) {
      notifications.show({
        color: "red",
        title: "stream_analysis sync failed",
        message: `${source}: ${resp.error?.message ?? "workspace.delete failed"}`,
      });
    }
  };

  const buildStreamAnalysisWorkspacePayload = (
    workspace: StreamAnalysisWorkspaceConfig
  ): Record<string, unknown> | null => {
    const graphNodes = Array.isArray(workspace.graphNodes) ? workspace.graphNodes : [];
    if (graphNodes.length <= 0) {
      return null;
    }
    const nodes: Array<Record<string, unknown>> = graphNodes.map((node) => {
      const spec = STREAM_DAG_OPS[node.op];
      const params: Record<string, unknown> = {};
      for (const field of spec.params) {
        const raw = node.params[field.name];
        const coerced = coerceDagParamValue(raw, field.kind);
        if (
          field.optional &&
          (coerced === "" || coerced === null || coerced === undefined)
        ) {
          continue;
        }
        params[field.name] = coerced;
      }
      const inputs: Record<string, unknown> = {};
      const allInputPorts = [...spec.inputs, ...(spec.optionalInputs ?? [])];
      for (const port of allInputPorts) {
        const sourceNodeId = String(node.inputs[port] ?? "").trim();
        if (sourceNodeId) {
          inputs[port] = sourceNodeId;
        }
      }
      const out: Record<string, unknown> = {
        id: node.id,
        op: node.op,
        params,
      };
      if (allInputPorts.length > 0) {
        out.inputs = inputs;
      }
      return out;
    });

    const outputs = (Array.isArray(workspace.publishOutputs) ? workspace.publishOutputs : [])
      .map((output) => ({
        output_id: String(output.outputId ?? "").trim(),
        node_id: String(output.nodeId ?? "").trim(),
      }))
      .filter((output) => output.output_id && output.node_id);

    return {
      workspace_id: workspace.workspaceId,
      name: workspace.name,
      enabled: workspace.enabled !== false,
      graph: { nodes },
      publish: { outputs },
    };
  };

  const syncStreamAnalysisWorkspace = async (workspaceId: string, source: string) => {
    if (!streamAnalysisReadyRef.current) {
      return;
    }
    const workspaceConfig = streamWorkspacesRef.current[workspaceId];
    if (!workspaceConfig) {
      await deleteStreamAnalysisWorkspace(workspaceId, source);
      return;
    }
    const workspace = buildStreamAnalysisWorkspacePayload(workspaceConfig);
    if (!workspace) {
      await deleteStreamAnalysisWorkspace(workspaceConfig.workspaceId, source);
      return;
    }
    const expectedRevision = Object.prototype.hasOwnProperty.call(
      streamWorkspaceRevisionsRef.current,
      workspaceConfig.workspaceId
    )
      ? streamWorkspaceRevisionsRef.current[workspaceConfig.workspaceId]
      : 0;
    const resp = await putStreamWorkspace(
      workspaceConfig.workspaceId,
      workspace,
      expectedRevision
    );
    if (
      !resp.ok &&
      String(resp.error?.code ?? "").toLowerCase() === "revision_conflict"
    ) {
      notifications.show({
        color: "yellow",
        title: "Workspace changed elsewhere",
        message: `${source}: Reloaded latest workspace state.`,
      });
      await loadStreamAnalysisWorkspaces(source, { notifyOnError: false });
      return;
    }
    if (!resp.ok) {
      notifications.show({
        color: "red",
        title: "stream_analysis sync failed",
        message: `${source}: ${resp.error?.message ?? "workspace.put failed"}`,
      });
      return;
    }
    const resultObj =
      resp.result && typeof resp.result === "object"
        ? (resp.result as Record<string, unknown>)
        : {};
    const raw =
      resultObj.raw && typeof resultObj.raw === "object"
        ? ({ [workspaceConfig.workspaceId]: resultObj.raw } as Record<
            string,
            unknown
          >)
        : ({ [workspaceConfig.workspaceId]: workspace } as Record<
            string,
            unknown
          >);
    const normalized = normalizeStreamWorkspaceRecord(raw);
    const nextWorkspace = normalized[workspaceConfig.workspaceId];
    if (nextWorkspace) {
      setStreamWorkspaces((prev) => ({
        ...prev,
        [workspaceConfig.workspaceId]: nextWorkspace,
      }));
      streamWorkspacesRef.current = {
        ...streamWorkspacesRef.current,
        [workspaceConfig.workspaceId]: nextWorkspace,
      };
    }
    const summaryRaw =
      resultObj.workspace && typeof resultObj.workspace === "object"
        ? (resultObj.workspace as Record<string, unknown>)
        : null;
    if (summaryRaw) {
      const summaries = normalizeWorkspaceSummaries([summaryRaw]);
      const summary = summaries[0];
      if (summary) {
        setStreamWorkspaceRevisions((prev) => ({
          ...prev,
          [workspaceConfig.workspaceId]: summary.revision,
        }));
        streamWorkspaceRevisionsRef.current = {
          ...streamWorkspaceRevisionsRef.current,
          [workspaceConfig.workspaceId]: summary.revision,
        };
      }
    }
    await refreshWorkspaceStoreStatus(source, { notifyOnError: false });
  };

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

  const createPanel = (kind: PanelKind) => {
    panelIdRef.current += 1;
    const id = `panel-${panelIdRef.current}`;
    const workspaceIds = Object.keys(streamWorkspacesRef.current).sort();
    let defaultWorkspaceId = workspaceIds[0] ?? null;
    if (!defaultWorkspaceId) {
      const nextId = Math.max(1, Math.trunc(streamWorkspaceIdRef.current));
      const workspaceId = `workspace-${nextId}`;
      streamWorkspaceIdRef.current = nextId + 1;
      const workspace = defaultStreamAnalysisWorkspaceConfig(workspaceId);
      setStreamWorkspaces((prev) => ({ ...prev, [workspaceId]: workspace }));
      streamWorkspacesRef.current = {
        ...streamWorkspacesRef.current,
        [workspaceId]: workspace,
      };
      if (!daqWorkspaceId) {
        setDaqWorkspaceId(workspaceId);
      }
      defaultWorkspaceId = workspaceId;
    }
    const workspaceConfig =
      (defaultWorkspaceId && streamWorkspacesRef.current[defaultWorkspaceId]) ?? null;
    let panel: PlotPanelState;
    if (kind === "stream_raw" || kind === "stream_waterfall") {
      const traceOutputId = defaultOutputForKind(workspaceConfig, "trace");
      panel = {
        id,
        title:
          kind === "stream_waterfall"
            ? `Waterfall ${panelIdRef.current}`
            : `Trace ${panelIdRef.current}`,
        kind,
        sourceMode: "raw",
        stream: null,
        overlayCount:
          kind === "stream_waterfall"
            ? DEFAULT_WATERFALL_ROWS
            : DEFAULT_STREAM_OVERLAY_COUNT,
        channelIndex: 0,
        workspaceId: defaultWorkspaceId ?? id,
        outputId: traceOutputId,
        overlayOutputIds: [],
        traceDecimator: DEFAULT_TRACE_DECIMATOR,
        traceMaxPoints: DEFAULT_TRACE_MAX_POINTS,
        traceMaxFps: DEFAULT_TRACE_MAX_FPS,
        rollingWindow: DEFAULT_TRACE_ROLLING_WINDOW,
        averageMode: DEFAULT_TRACE_AVERAGE_MODE,
        yScaleMode: "auto",
        yMin: null,
        yMax: null,
      };
      streamFramesRef.set(id, []);
      streamTraceOverlayRef.set(id, new Map());
    } else if (kind === "stream_scalar") {
      const integralOutputId = defaultOutputForKind(workspaceConfig, "scalar");
      panel = {
        id,
        title: `Scalar ${panelIdRef.current}`,
        kind: "stream_scalar",
        workspaceId: defaultWorkspaceId ?? id,
        outputId: integralOutputId,
        stream: workspaceConfig?.stream ?? null,
        channelIndex: workspaceConfig?.channelIndex ?? 0,
        analysis: workspaceConfig?.analysis ?? defaultStreamAnalysisSettings(),
        timeWindowS: DEFAULT_WINDOW_S,
        yScaleMode: "auto",
        yMin: null,
        yMax: null,
      };
      buffersRef.set(id, new Map());
    } else if (kind === "stream_params") {
      const paramsOutputIds = workspaceOutputOptionsByKind(
        workspaceConfig,
        "params_map"
      ).map((item) => item.value);
      const firstScalarOutputId = defaultOutputForKind(workspaceConfig, "scalar");
      panel = {
        id,
        title: `Params ${panelIdRef.current}`,
        kind: "stream_params",
        workspaceId: defaultWorkspaceId ?? id,
        outputIds:
          paramsOutputIds.length > 0
            ? paramsOutputIds
            : firstScalarOutputId
            ? [firstScalarOutputId]
            : [],
      };
      streamParamsLatestRef.set(id, {});
    } else if (kind === "stream_bin_stats") {
      const binOutputId = defaultOutputForKind(workspaceConfig, "hist_agg");
      panel = {
        id,
        title: `Bin stats ${panelIdRef.current}`,
        kind: "stream_bin_stats",
        workspaceId: defaultWorkspaceId ?? id,
        outputId: binOutputId,
        overlayOutputIds: [],
        stream: workspaceConfig?.stream ?? null,
        channelIndex: workspaceConfig?.channelIndex ?? 0,
        analysis: workspaceConfig?.analysis ?? defaultStreamAnalysisSettings(),
        binStats: workspaceConfig?.binStats ?? defaultStreamBinStatsSettings(),
        uncertaintyMode: "sem",
        uncertaintyScale: DEFAULT_UNCERTAINTY_SCALE,
        yScaleMode: "auto",
        yMin: null,
        yMax: null,
      };
      streamBinStatsRef.delete(id);
    } else if (kind === "stream_bin2d") {
      const bin2dOutputId = defaultOutputForKind(workspaceConfig, "hist2d");
      panel = {
        id,
        title: `Bin2D ${panelIdRef.current}`,
        kind: "stream_bin2d",
        workspaceId: defaultWorkspaceId ?? id,
        outputId: bin2dOutputId,
        reducer: DEFAULT_BIN2D_REDUCER,
        yScaleMode: "auto",
        yMin: null,
        yMax: null,
      };
      streamBin2dRef.delete(id);
    } else {
      panel = {
        id,
        title: `Panel ${panelIdRef.current}`,
        kind: "telemetry",
        traces: [],
        timeWindowS: DEFAULT_WINDOW_S,
        yScaleMode: "auto",
        yMin: null,
        yMax: null,
        yDisplayMode: "absolute",
        yOffsetMode: "auto",
        yOffsetValue: null,
      };
      buffersRef.set(id, new Map());
    }
    setPanels((prev) => [...prev, panel]);
    setActivePanelId(id);
  };

  const removePanel = (panelId: string) => {
    if (panels.length <= 1) {
      return;
    }
    const nextActive = panels.find((panel) => panel.id !== panelId);
    buffersRef.delete(panelId);
    streamFramesRef.delete(panelId);
    streamTraceOverlayRef.delete(panelId);
    streamBinStatsOverlayRef.delete(panelId);
    streamParamsLatestRef.delete(panelId);
    streamBinStatsRef.delete(panelId);
    streamBin2dRef.delete(panelId);
    if (editingPanelId === panelId) {
      setEditingPanelId(null);
      setPanelTitleDraft("");
    }
    if (streamTraceOptionsPanelId === panelId) {
      setStreamTraceOptionsPanelId(null);
    }
    if (streamBinStatsOptionsPanelId === panelId) {
      setStreamBinStatsOptionsPanelId(null);
    }
    if (streamBin2dOptionsPanelId === panelId) {
      setStreamBin2dOptionsPanelId(null);
    }
    if (streamParamsOptionsPanelId === panelId) {
      setStreamParamsOptionsPanelId(null);
    }
    setPanels((prev) => prev.filter((panel) => panel.id !== panelId));
    if (activePanelId === panelId && nextActive) {
      setActivePanelId(nextActive.id);
    }
    setPlotTick((tick) => tick + 1);
  };

  const movePanel = (
    sourcePanelId: string,
    targetPanelId: string,
    mode: ReorderMode
  ) => {
    if (!sourcePanelId || !targetPanelId || sourcePanelId === targetPanelId) {
      return;
    }
    setPanels((prev) => {
      const sourceIndex = prev.findIndex((panel) => panel.id === sourcePanelId);
      const targetIndex = prev.findIndex((panel) => panel.id === targetPanelId);
      if (sourceIndex < 0 || targetIndex < 0) {
        return prev;
      }
      const next = [...prev];
      if (mode === "swap") {
        const temp = next[sourceIndex];
        next[sourceIndex] = next[targetIndex];
        next[targetIndex] = temp;
        return next;
      }
      const [moved] = next.splice(sourceIndex, 1);
      const nextTargetIndex = next.findIndex((panel) => panel.id === targetPanelId);
      if (nextTargetIndex < 0) {
        return prev;
      }
      const insertIndex = mode === "before" ? nextTargetIndex : nextTargetIndex + 1;
      next.splice(insertIndex, 0, moved);
      return next;
    });
  };

  const addTraceToPanel = (
    panelId: string,
    deviceId: string,
    signal: string
  ) => {
    const panel = panels.find((p) => p.id === panelId);
    if (!panel || !isTelemetryPanel(panel)) {
      return;
    }
    if (panel.traces.some((t) => t.deviceId === deviceId && t.signal === signal)) {
      return;
    }
    const units =
      latestByDevice[deviceId]?.[signal]?.units ??
      null;
    const latestValue = latestByDevice[deviceId]?.[signal]?.value;
    const valueKind =
      typeof latestValue === "boolean"
        ? "boolean"
        : typeof latestValue === "number"
        ? "number"
        : undefined;
    const trace = { deviceId, signal, units, valueKind };
    setPanels((prev) =>
      prev.map((p) =>
        p.id === panelId ? { ...p, traces: [...p.traces, trace] } : p
      )
    );
    const panelBuffers = ensurePanelBuffers(panelId);
    const capacity = panelCapacity(panel.timeWindowS);
    const key = traceKeyId(trace);
    if (!panelBuffers.has(key)) {
      panelBuffers.set(key, new RingBuffer(capacity));
    }
    setPlotTick((tick) => tick + 1);
  };

  const removeTraceFromPanel = (panelId: string, trace: TraceKey) => {
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isTelemetryPanel(panel)
          ? {
              ...panel,
              traces: panel.traces.filter(
                (item) =>
                  !(item.deviceId === trace.deviceId && item.signal === trace.signal)
              ),
            }
          : panel
      )
    );
    const panelBuffers = buffersRef.get(panelId);
    panelBuffers?.delete(traceKeyId(trace));
    setPlotTick((tick) => tick + 1);
  };

  const clearPanelBuffers = (panelId: string) => {
    const panelBuffers = buffersRef.get(panelId);
    if (!panelBuffers) {
      return;
    }
    for (const buffer of panelBuffers.values()) {
      buffer.clear();
    }
    setPlotTick((tick) => tick + 1);
  };

  const clearStreamPanelFrames = (panelId: string) => {
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const clearStreamBinStatsPanel = async (panelId: string) => {
    const panel = panels.find((entry) => entry.id === panelId);
    if (!panel || !isStreamBinStatsPanel(panel)) {
      return;
    }
    const workspace = streamWorkspacesRef.current[panel.workspaceId] ?? null;
    const outputId = String(panel.outputId ?? "").trim();
    const output = workspace?.publishOutputs.find((entry) => entry.outputId === outputId);
    const nodeId = output?.nodeId ?? null;
    const node = nodeId
      ? workspace?.graphNodes.find((entry) => entry.id === nodeId) ?? null
      : null;
    if (
      streamAnalysisReadyRef.current &&
      workspace &&
      node &&
      node.op === "aggregate.bin_stats"
    ) {
      const resp = await resetStreamWorkspace(workspace.workspaceId, node.id);
      if (!resp.ok) {
        notifications.show({
          color: "red",
          title: "Clear binned data failed",
          message: resp.error?.message ?? resp.error?.code ?? "workspace.reset failed",
        });
      } else {
        clearWorkspaceBinPanels(workspace.workspaceId, node.id);
        return;
      }
    }
    streamBinStatsRef.delete(panelId);
    streamBinStatsOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const clearStreamBin2dPanel = async (panelId: string) => {
    const panel = panels.find((entry) => entry.id === panelId);
    if (!panel || !isStreamBin2dPanel(panel)) {
      return;
    }
    const workspace = streamWorkspacesRef.current[panel.workspaceId] ?? null;
    const outputId = String(panel.outputId ?? "").trim();
    const output = workspace?.publishOutputs.find((entry) => entry.outputId === outputId);
    const nodeId = output?.nodeId ?? null;
    const node = nodeId
      ? workspace?.graphNodes.find((entry) => entry.id === nodeId) ?? null
      : null;
    if (
      streamAnalysisReadyRef.current &&
      workspace &&
      node &&
      node.op === "aggregate.bin2d_stats"
    ) {
      const resp = await resetStreamWorkspace(workspace.workspaceId, node.id);
      if (!resp.ok) {
        notifications.show({
          color: "red",
          title: "Clear binned data failed",
          message: resp.error?.message ?? resp.error?.code ?? "workspace.reset failed",
        });
      } else {
        clearWorkspaceBinPanels(workspace.workspaceId, node.id);
        return;
      }
    }
    streamBin2dRef.delete(panelId);
    setPlotTick((tick) => tick + 1);
  };

  const setPanelTimeWindow = (panelId: string, value: number) => {
    const panel = panels.find((p) => p.id === panelId);
    if (
      !panel ||
      (!isTelemetryPanel(panel) && !isStreamScalarPanel(panel))
    ) {
      return;
    }
    const nextWindow = Number.isFinite(value) ? Math.max(5, value) : panel.timeWindowS;
    setPanels((prev) =>
      prev.map((p) =>
        p.id === panelId &&
        (isTelemetryPanel(p) || isStreamScalarPanel(p))
          ? { ...p, timeWindowS: nextWindow }
          : p
      )
    );
    const capacity = panelCapacity(nextWindow);
    const panelBuffers = ensurePanelBuffers(panelId);
    const traceKeys = isTelemetryPanel(panel)
      ? new Set(panel.traces.map(traceKeyId))
      : new Set([traceKeyId(streamScalarTrace(panel))]);
    for (const [key, buffer] of panelBuffers.entries()) {
      if (!traceKeys.has(key)) {
        panelBuffers.delete(key);
      } else {
        buffer.resize(capacity);
      }
    }
    if (isTelemetryPanel(panel)) {
      for (const trace of panel.traces) {
        const key = traceKeyId(trace);
        if (!panelBuffers.has(key)) {
          panelBuffers.set(key, new RingBuffer(capacity));
        }
      }
    } else {
      const key = traceKeyId(streamScalarTrace(panel));
      if (!panelBuffers.has(key)) {
        panelBuffers.set(key, new RingBuffer(capacity));
      }
    }
    setPlotTick((tick) => tick + 1);
  };

  const setPanelYScaleMode = (panelId: string, mode: YScaleMode) => {
    setPanels((prev) =>
      prev.map((panel) => {
        if (panel.id !== panelId) {
          return panel;
        }
        if (mode === "auto") {
          return { ...panel, yScaleMode: "auto", yMin: null, yMax: null };
        }
        const nextMin = panel.yMin ?? 0;
        const nextMax = panel.yMax ?? (nextMin + 1);
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

  const resolveTelemetryPanelOffset = (
    panel: PlotTelemetryPanelState
  ): number | null => {
    if (panel.yDisplayMode !== "delta") {
      return null;
    }
    if (
      panel.yOffsetMode === "freeze" &&
      typeof panel.yOffsetValue === "number" &&
      Number.isFinite(panel.yOffsetValue)
    ) {
      return Math.round(panel.yOffsetValue);
    }
    const numericTraces = panel.traces.filter(
      (trace) => trace.valueKind !== "boolean"
    );
    if (numericTraces.length === 0) {
      return null;
    }
    const panelBuffers = buffersRef.get(panel.id) ?? new Map<string, RingBuffer>();
    const range = normalizeAutoRange(
      computeTelemetryAutoYRange(numericTraces, panelBuffers, panel.timeWindowS)
    );
    if (!range) {
      return null;
    }
    return Math.round((range.min + range.max) / 2);
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

  const setTelemetryYOffsetMode = (
    panelId: string,
    mode: YOffsetMode,
    value: number | null = null
  ) => {
    const panel = panels.find((entry) => entry.id === panelId);
    const resolvedFreezeValue =
      mode === "freeze"
        ? typeof value === "number" && Number.isFinite(value)
          ? value
          : panel && isTelemetryPanel(panel)
          ? resolveTelemetryPanelOffset(panel)
          : null
        : null;
    setPanels((prev) =>
      prev.map((panel) => {
        if (panel.id !== panelId || !isTelemetryPanel(panel)) {
          return panel;
        }
        if (
          mode === "freeze" &&
          typeof resolvedFreezeValue === "number" &&
          Number.isFinite(resolvedFreezeValue)
        ) {
          return {
            ...panel,
            yOffsetMode: "freeze",
            yOffsetValue: Math.round(resolvedFreezeValue),
          };
        }
        return { ...panel, yOffsetMode: "auto", yOffsetValue: null };
      })
    );
  };

  const resolvePanelAutoYRange = (
    panel: PlotPanelState | null
  ): { min: number; max: number } | null => {
    if (!panel) {
      return null;
    }
    if (isTelemetryPanel(panel)) {
      const panelBuffers = buffersRef.get(panel.id) ?? new Map<string, RingBuffer>();
      const range = normalizeAutoRange(
        computeTelemetryAutoYRange(panel.traces, panelBuffers, panel.timeWindowS)
      );
      if (!range) {
        return null;
      }
      if (panel.yDisplayMode !== "delta") {
        return range;
      }
      const offset = resolveTelemetryPanelOffset(panel);
      if (offset === null) {
        return range;
      }
      return {
        min: range.min - offset,
        max: range.max - offset,
      };
    }
    if (isStreamScalarPanel(panel)) {
      const panelBuffers = buffersRef.get(panel.id) ?? new Map<string, RingBuffer>();
      return normalizeAutoRange(
        computeTelemetryAutoYRange(
          [streamScalarTrace(panel)],
          panelBuffers,
          panel.timeWindowS
        )
      );
    }
    if (isStreamBinStatsPanel(panel)) {
      const snapshot = streamBinStatsRef.get(panel.id) ?? null;
      return normalizeAutoRange(
        computeStreamBinStatsAutoYRange(
          snapshot?.series ?? null,
          panel.uncertaintyMode,
          panel.uncertaintyScale,
          streamBinStatsOverlaySeries(panel)
        )
      );
    }
    if (isStreamBin2dPanel(panel)) {
      const snapshot = streamBin2dRef.get(panel.id) ?? null;
      return normalizeAutoRange(
        computeStreamBin2dAutoZRange(snapshot?.series ?? null, panel.reducer)
      );
    }
    const frames = streamFramesRef.get(panel.id) ?? [];
    if (isStreamWaterfallPanel(panel)) {
      return normalizeAutoRange(
        computeStreamWaterfallAutoZRange(
          frames,
          panel.overlayCount,
          panel.sourceMode === "raw" ? panel.channelIndex : 0
        )
      );
    }
    return normalizeAutoRange(
      computeStreamRawAutoYRange(
        frames,
        panel.overlayCount,
        panel.sourceMode === "raw" ? panel.channelIndex : 0,
        panel.sourceMode === "dag" ? streamTraceOverlaySeries(panel) : []
      )
    );
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

  const openYAxisModal = (
    panelId: string,
    options?: { prefillFromAuto?: boolean }
  ) => {
    const panel = panels.find((entry) => entry.id === panelId) ?? null;
    if (!panel) {
      return;
    }
    const autoRange = resolvePanelAutoYRange(panel);
    setYAxisModalPanelId(panelId);
    setYAxisAutoRange(autoRange);
    if (options?.prefillFromAuto) {
      if (autoRange) {
        setYAxisDraftMin(autoRange.min);
        setYAxisDraftMax(autoRange.max);
      } else {
        setYAxisDraftMin(0);
        setYAxisDraftMax(1);
      }
      return;
    }
    if (
      panel.yScaleMode === "manual" &&
      typeof panel.yMin === "number" &&
      typeof panel.yMax === "number" &&
      Number.isFinite(panel.yMin) &&
      Number.isFinite(panel.yMax) &&
      panel.yMin < panel.yMax
    ) {
      setYAxisDraftMin(panel.yMin);
      setYAxisDraftMax(panel.yMax);
      return;
    }
    setYAxisDraftMin(autoRange ? autoRange.min : "");
    setYAxisDraftMax(autoRange ? autoRange.max : "");
  };

  const closeYAxisModal = () => {
    setYAxisModalPanelId(null);
    setYAxisDraftMin("");
    setYAxisDraftMax("");
    setYAxisAutoRange(null);
  };

  const applyYAxisModal = () => {
    const panelId = yAxisModalPanelId;
    if (!panelId) {
      return;
    }
    const min = parseNumberInput(yAxisDraftMin);
    const max = parseNumberInput(yAxisDraftMax);
    if (min === null || max === null) {
      notifications.show({
        color: "red",
        title: "Invalid y range",
        message: "Manual y-axis limits require numeric min and max values.",
      });
      return;
    }
    if (min >= max) {
      notifications.show({
        color: "red",
        title: "Invalid y range",
        message: "Y-axis min must be less than y-axis max.",
      });
      return;
    }
    setPanelManualYRange(panelId, min, max);
    closeYAxisModal();
  };

  const setStreamPanelTarget = (
    panelId: string,
    target: StreamTarget | null
  ) => {
    const targetChannelCount = inferChannelCountFromShape(target?.shape);
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId &&
        isStreamTracePanel(panel) &&
        panel.sourceMode === "raw"
          ? {
              ...panel,
              stream: target,
              channelIndex:
                targetChannelCount <= 1
                  ? 0
                  : Math.max(0, Math.min(panel.channelIndex, targetChannelCount - 1)),
            }
          : panel
      )
    );
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const streamTraceOverlaySeries = (
    panel: PlotStreamPanelState | PlotStreamWaterfallPanelState
  ) => {
    const overlayMap = streamTraceOverlayRef.get(panel.id);
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
  };

  const streamBinStatsOverlaySeries = (
    panel: PlotStreamBinStatsPanelState
  ) => {
    const overlayMap = streamBinStatsOverlayRef.get(panel.id);
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
  };

  const setStreamPanelTargetFromKey = (
    panelId: string,
    targetKey: string | null
  ) => {
    if (!targetKey) {
      setStreamPanelTarget(panelId, null);
      return;
    }
    const splitAt = targetKey.indexOf("|");
    if (splitAt <= 0 || splitAt >= targetKey.length - 1) {
      setStreamPanelTarget(panelId, null);
      return;
    }
    const deviceId = targetKey.slice(0, splitAt);
    const stream = targetKey.slice(splitAt + 1);
    const meta = streamCatalogByKey.get(targetKey);
    setStreamPanelTarget(panelId, {
      deviceId,
      stream,
      units: typeof meta?.units === "string" ? meta.units : undefined,
      shape: normalizeShape(meta?.shape),
    });
  };

  const setStreamPanelOverlayCount = (panelId: string, value: number) => {
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamTracePanel(panel)
          ? {
              ...panel,
              overlayCount: Number.isFinite(value)
                ? Math.max(
                    1,
                    Math.min(
                      isStreamWaterfallPanel(panel) ? 600 : 80,
                      Math.trunc(value)
                    )
                  )
                : isStreamWaterfallPanel(panel)
                ? DEFAULT_WATERFALL_ROWS
                : DEFAULT_STREAM_OVERLAY_COUNT,
            }
          : panel
      )
    );
  };

  const setStreamPanelChannelIndex = (panelId: string, value: number) => {
    const nextChannel = Number.isFinite(value)
      ? Math.max(0, Math.trunc(value))
      : 0;
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId &&
        isStreamTracePanel(panel) &&
        panel.sourceMode === "raw"
          ? { ...panel, channelIndex: nextChannel }
          : panel
      )
    );
  };

  const setStreamPanelTraceDecimator = (
    panelId: string,
    decimator: StreamTraceDecimator
  ) => {
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamTracePanel(panel)
          ? { ...panel, traceDecimator: normalizeTraceDecimator(decimator) }
          : panel
      )
    );
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamPanelTraceMaxPoints = (panelId: string, value: number) => {
    const nextPoints = normalizeTraceMaxPoints(value);
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamTracePanel(panel)
          ? { ...panel, traceMaxPoints: nextPoints }
          : panel
      )
    );
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamPanelTraceMaxFps = (panelId: string, value: number) => {
    const nextFps = normalizeTraceMaxFps(value);
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamTracePanel(panel)
          ? { ...panel, traceMaxFps: nextFps }
          : panel
      )
    );
  };

  const setStreamPanelRollingWindow = (panelId: string, value: number) => {
    const nextWindow = normalizeTraceRollingWindow(value);
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamTracePanel(panel)
          ? { ...panel, rollingWindow: nextWindow }
          : panel
      )
    );
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamPanelAverageMode = (
    panelId: string,
    mode: StreamTraceAverageMode
  ) => {
    const nextMode = normalizeTraceAverageMode(mode);
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamTracePanel(panel)
          ? { ...panel, averageMode: nextMode }
          : panel
      )
    );
    streamFramesRef.set(panelId, []);
    setPlotTick((tick) => tick + 1);
  };

  const setStreamTracePanelSourceMode = (
    panelId: string,
    sourceMode: StreamTraceSourceMode
  ) => {
    setPanels((prev) =>
      prev.map((panel) => {
        if (panel.id !== panelId || !isStreamTracePanel(panel)) {
          return panel;
        }
        if (panel.sourceMode === sourceMode) {
          return panel;
        }
        if (sourceMode === "raw") {
          return {
            ...panel,
            sourceMode: "raw",
            overlayOutputIds: [],
          };
        }
        const workspaceId =
          panel.workspaceId && streamWorkspacesRef.current[panel.workspaceId]
            ? panel.workspaceId
            : Object.keys(streamWorkspacesRef.current).sort()[0] ?? panel.workspaceId;
        const workspace = workspaceId
          ? streamWorkspacesRef.current[workspaceId] ?? null
          : null;
        const outputId =
          panel.outputId &&
          workspace &&
          workspaceOutputKind(workspace, panel.outputId) === "trace"
            ? panel.outputId
            : defaultOutputForKind(workspace, "trace");
        return {
          ...panel,
          sourceMode: "dag",
          workspaceId,
          outputId,
          overlayOutputIds: [],
          stream: workspace?.stream ?? panel.stream,
          channelIndex: workspace?.channelIndex ?? panel.channelIndex,
        };
      })
    );
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamTracePanelWorkspace = (
    panelId: string,
    workspaceId: string | null
  ) => {
    const nextWorkspaceId = String(workspaceId ?? "").trim();
    const workspace = streamWorkspacesRef.current[nextWorkspaceId] ?? null;
    if (!nextWorkspaceId || !workspace) {
      return;
    }
    setPanels((prev) =>
      prev.map((panel) => {
        if (
          panel.id !== panelId ||
          !isStreamTracePanel(panel) ||
          panel.sourceMode !== "dag"
        ) {
          return panel;
        }
        const outputId = defaultOutputForKind(workspace, "trace");
        return {
          ...panel,
          workspaceId: nextWorkspaceId,
          outputId,
          overlayOutputIds: [],
          stream: workspace.stream,
          channelIndex: workspace.channelIndex,
        };
      })
    );
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamTracePanelOutput = (panelId: string, outputId: string | null) => {
    const nextOutputId = String(outputId ?? "").trim() || null;
    setPanels((prev) =>
      prev.map((panel) => {
        if (
          panel.id !== panelId ||
          !isStreamTracePanel(panel) ||
          panel.sourceMode !== "dag"
        ) {
          return panel;
        }
        return {
          ...panel,
          outputId: nextOutputId,
          overlayOutputIds: (panel.overlayOutputIds ?? []).filter(
            (id) => id !== nextOutputId
          ),
        };
      })
    );
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamTracePanelOverlayOutputs = (
    panelId: string,
    outputIds: string[]
  ) => {
    const nextSet = new Set(
      outputIds
        .map((value) => String(value ?? "").trim())
        .filter((value) => value.length > 0)
    );
    setPanels((prev) =>
      prev.map((panel) => {
        if (
          panel.id !== panelId ||
          !isStreamTracePanel(panel) ||
          panel.sourceMode !== "dag"
        ) {
          return panel;
        }
        const primary = String(panel.outputId ?? "").trim();
        if (primary) {
          nextSet.delete(primary);
        }
        return {
          ...panel,
          overlayOutputIds: [...nextSet],
        };
      })
    );
    streamTraceOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamAnalysisPanelWorkspace = (
    panelId: string,
    workspaceId: string | null
  ) => {
    const nextWorkspaceId = String(workspaceId ?? "").trim();
    const nextWorkspace = streamWorkspacesRef.current[nextWorkspaceId];
    if (!nextWorkspaceId || !nextWorkspace) {
      return;
    }
    const panel = panels.find((entry) => entry.id === panelId);
    if (
      !panel ||
      (!isStreamScalarPanel(panel) &&
        !isStreamParamsPanel(panel) &&
        !isStreamBinStatsPanel(panel) &&
        !isStreamBin2dPanel(panel))
    ) {
      return;
    }
    if (panel.workspaceId === nextWorkspaceId) {
      return;
    }
    const nextOutputId = isStreamScalarPanel(panel)
      ? defaultOutputForKind(nextWorkspace, "scalar")
      : isStreamParamsPanel(panel)
      ? null
      : isStreamBinStatsPanel(panel)
      ? defaultOutputForKind(nextWorkspace, "hist_agg")
      : defaultOutputForKind(nextWorkspace, "hist2d");
    const updated = isStreamScalarPanel(panel)
      ? ({
          ...panel,
          workspaceId: nextWorkspaceId,
          outputId: nextOutputId,
          stream: nextWorkspace.stream,
          channelIndex: nextWorkspace.channelIndex,
          analysis: nextWorkspace.analysis,
        } as PlotStreamScalarPanelState)
      : isStreamParamsPanel(panel)
      ? ({
          ...panel,
          workspaceId: nextWorkspaceId,
          outputIds: (() => {
            const paramsOutputs = workspaceOutputOptionsByKind(
              nextWorkspace,
              "params_map"
            ).map((item) => item.value);
            if (paramsOutputs.length > 0) {
              return paramsOutputs;
            }
            const firstScalar = defaultOutputForKind(nextWorkspace, "scalar");
            return firstScalar ? [firstScalar] : [];
          })(),
        } as PlotStreamParamsPanelState)
      : isStreamBinStatsPanel(panel)
      ? ({
          ...panel,
          workspaceId: nextWorkspaceId,
          outputId: nextOutputId,
          overlayOutputIds: [],
          stream: nextWorkspace.stream,
          channelIndex: nextWorkspace.channelIndex,
          analysis: nextWorkspace.analysis,
          binStats: nextWorkspace.binStats,
        } as PlotStreamBinStatsPanelState)
      : ({
          ...panel,
          workspaceId: nextWorkspaceId,
          outputId: nextOutputId,
        } as PlotStreamBin2dPanelState);
    setPanels((prev) =>
      prev.map((entry) => (entry.id === panelId ? updated : entry))
    );
    if (isStreamScalarPanel(panel)) {
      clearPanelBuffers(panelId);
    } else if (isStreamParamsPanel(panel)) {
      streamParamsLatestRef.set(panelId, {});
      setPlotTick((tick) => tick + 1);
    } else if (isStreamBinStatsPanel(panel)) {
      streamBinStatsRef.delete(panelId);
      streamBinStatsOverlayRef.set(panelId, new Map());
      setPlotTick((tick) => tick + 1);
    } else {
      streamBin2dRef.delete(panelId);
      setPlotTick((tick) => tick + 1);
    }
  };

  const setStreamAnalysisPanelOutput = (panelId: string, outputId: string | null) => {
    const nextOutputId = String(outputId ?? "").trim() || null;
    const panel = panels.find((entry) => entry.id === panelId);
    if (
      !panel ||
      (!isStreamScalarPanel(panel) &&
        !isStreamBinStatsPanel(panel) &&
        !isStreamBin2dPanel(panel))
    ) {
      return;
    }
    setPanels((prev) =>
      prev.map((entry) => {
        if (entry.id !== panelId) {
          return entry;
        }
        if (isStreamScalarPanel(entry)) {
          return { ...entry, outputId: nextOutputId };
        }
        if (isStreamBinStatsPanel(entry)) {
          return { ...entry, outputId: nextOutputId };
        }
        return { ...entry, outputId: nextOutputId };
      })
    );
    if (isStreamScalarPanel(panel)) {
      clearPanelBuffers(panelId);
    } else if (isStreamBinStatsPanel(panel)) {
      streamBinStatsRef.delete(panelId);
      streamBinStatsOverlayRef.set(panelId, new Map());
      setPlotTick((tick) => tick + 1);
    } else {
      streamBin2dRef.delete(panelId);
      setPlotTick((tick) => tick + 1);
    }
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

  const setStreamParamsPanelOutputs = (panelId: string, outputIds: string[]) => {
    const next = outputIds
      .map((value) => String(value ?? "").trim())
      .filter((value) => value.length > 0);
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamParamsPanel(panel)
          ? { ...panel, outputIds: next }
          : panel
      )
    );
    streamParamsLatestRef.set(panelId, {});
    setPlotTick((tick) => tick + 1);
  };

  const setStreamBinStatsOverlayOutputs = (panelId: string, outputIds: string[]) => {
    const next = outputIds
      .map((value) => String(value ?? "").trim())
      .filter((value) => value.length > 0);
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamBinStatsPanel(panel)
          ? { ...panel, overlayOutputIds: next }
          : panel
      )
    );
    streamBinStatsOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
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

  const clearWorkspaceBinPanels = (workspaceId: string, nodeId?: string | null) => {
    const workspace = streamWorkspacesRef.current[workspaceId] ?? null;
    const allowedOutputIds =
      workspace && nodeId
        ? new Set(
            workspace.publishOutputs
              .filter((output) => output.nodeId === nodeId)
              .map((output) => output.outputId)
          )
        : null;
    for (const panel of panelsRef.current) {
      if (!isStreamBinStatsPanel(panel) && !isStreamBin2dPanel(panel)) {
        continue;
      }
      if (panel.workspaceId !== workspaceId) {
        continue;
      }
      if (allowedOutputIds && !allowedOutputIds.has(panel.outputId ?? "")) {
        continue;
      }
      if (isStreamBinStatsPanel(panel)) {
        streamBinStatsRef.delete(panel.id);
      } else {
        streamBin2dRef.delete(panel.id);
      }
    }
    setPlotTick((tick) => tick + 1);
  };

  const resetDaqNodeAggregate = async (nodeId: string) => {
    const workspaceId = String(daqWorkspaceId ?? "").trim();
    const normalizedNodeId = String(nodeId ?? "").trim();
    if (!workspaceId || !normalizedNodeId || daqResetNodeBusyId !== null) {
      return;
    }
    if (!streamAnalysisReadyRef.current) {
      notifications.show({
        color: "yellow",
        title: "Stream analysis unavailable",
        message: "Start the stream_analysis process first.",
      });
      return;
    }
    setDaqResetNodeBusyId(normalizedNodeId);
    try {
      const resp = await resetStreamWorkspace(workspaceId, normalizedNodeId);
      if (!resp.ok) {
        notifications.show({
          color: "red",
          title: "Node reset failed",
          message: resp.error?.message ?? resp.error?.code ?? "workspace.reset failed",
        });
        return;
      }
      clearWorkspaceBinPanels(workspaceId, normalizedNodeId);
      notifications.show({
        color: "teal",
        title: "Node aggregate cleared",
        message: `${workspaceId}.${normalizedNodeId}`,
      });
    } finally {
      setDaqResetNodeBusyId(null);
    }
  };

  const focusDaqNodeCard = (nodeId: string) => {
    const normalizedNodeId = String(nodeId ?? "").trim();
    if (!normalizedNodeId) {
      return;
    }
    const card = daqNodeCardRefs.current.get(normalizedNodeId);
    if (card) {
      card.scrollIntoView({
        behavior: "smooth",
        block: "center",
        inline: "nearest",
      });
    }
    setDaqFocusedNodeId(normalizedNodeId);
    if (daqNodeFocusTimeoutRef.current !== null) {
      window.clearTimeout(daqNodeFocusTimeoutRef.current);
      daqNodeFocusTimeoutRef.current = null;
    }
    daqNodeFocusTimeoutRef.current = window.setTimeout(() => {
      setDaqFocusedNodeId((current) =>
        current === normalizedNodeId ? null : current
      );
      daqNodeFocusTimeoutRef.current = null;
    }, 1600);
  };

  const loadDaqWorkspaceDraft = (workspaceId: string | null) => {
    const id = String(workspaceId ?? "").trim();
    if (!id) {
      return;
    }
    const workspace = streamWorkspacesRef.current[id];
    if (!workspace) {
      return;
    }
    setDaqWorkspaceId(id);
    setDaqDraftName(workspace.name);
    setDaqDraftNodes(cloneDagNodes(workspace.graphNodes));
    setDaqDraftOutputs(cloneDagOutputs(workspace.publishOutputs));
    setDaqDraftEnabled(workspace.enabled !== false);
    setDaqFocusedNodeId(null);
    daqNodeCardRefs.current.clear();
  };

  const createStreamWorkspace = () => {
    const nextId = Math.max(1, Math.trunc(streamWorkspaceIdRef.current));
    const workspaceId = `workspace-${nextId}`;
    streamWorkspaceIdRef.current = nextId + 1;
    const workspace = defaultStreamAnalysisWorkspaceConfig(workspaceId);
    workspace.name = `Workspace ${nextId}`;
    setStreamWorkspaces((prev) => ({ ...prev, [workspaceId]: workspace }));
    streamWorkspacesRef.current = {
      ...streamWorkspacesRef.current,
      [workspaceId]: workspace,
    };
    loadDaqWorkspaceDraft(workspaceId);
  };

  const openDaqModal = async (workspaceId?: string | null) => {
    if (!streamAnalysisReadyRef.current) {
      notifications.show({
        color: "yellow",
        title: "stream_analysis not running",
        message: "Start the stream_analysis process first.",
      });
      return;
    }
    if (streamAnalysisReadyRef.current) {
      await loadStreamAnalysisWorkspaces("daq-modal-open", { notifyOnError: false });
    }
    const preferred = String(workspaceId ?? "").trim();
    const knownIds = Object.keys(streamWorkspacesRef.current).sort();
    if (knownIds.length === 0) {
      createStreamWorkspace();
      setDaqOpen(true);
      return;
    }
    const nextId =
      (preferred && streamWorkspacesRef.current[preferred] ? preferred : null) ??
      daqWorkspaceId ??
      knownIds[0];
    loadDaqWorkspaceDraft(nextId);
    setDaqOpen(true);
  };

  const closeDaqModal = () => {
    setDaqOpen(false);
    setDaqFocusedNodeId(null);
    if (daqNodeFocusTimeoutRef.current !== null) {
      window.clearTimeout(daqNodeFocusTimeoutRef.current);
      daqNodeFocusTimeoutRef.current = null;
    }
  };

  const setDaqNodeId = (index: number, value: string) => {
    const nextId = value.trim();
    if (!nextId) {
      return;
    }
    const current = daqDraftNodes[index];
    if (!current) {
      return;
    }
    if (current.id === nextId) {
      return;
    }
    if (daqDraftNodes.some((node, idx) => idx !== index && node.id === nextId)) {
      notifications.show({
        color: "red",
        title: "Duplicate node ID",
        message: `Node id '${nextId}' is already in use.`,
      });
      return;
    }
    const oldId = current.id;
    setDaqDraftNodes((prev) =>
      prev.map((node, idx) => {
        if (idx === index) {
          return { ...node, id: nextId };
        }
        let changed = false;
        const nextInputs: Record<string, string> = {};
        for (const [port, source] of Object.entries(node.inputs ?? {})) {
          if (source === oldId) {
            nextInputs[port] = nextId;
            changed = true;
          } else {
            nextInputs[port] = source;
          }
        }
        return changed ? { ...node, inputs: nextInputs } : node;
      })
    );
    setDaqDraftOutputs((prev) =>
      prev.map((output) =>
        output.nodeId === oldId ? { ...output, nodeId: nextId } : output
      )
    );
  };

  const setDaqNodeOp = (index: number, opRaw: string | null) => {
    if (!opRaw || !Object.prototype.hasOwnProperty.call(STREAM_DAG_OPS, opRaw)) {
      return;
    }
    const op = opRaw as StreamDagOpId;
    setDaqDraftNodes((prev) =>
      prev.map((node, idx) =>
        idx === index
          ? {
              ...node,
              op,
              params: defaultParamsForOp(op),
              inputs: defaultInputsForOp(op),
            }
          : node
      )
    );
  };

  const setDaqNodeInput = (index: number, port: string, sourceNodeId: string | null) => {
    setDaqDraftNodes((prev) =>
      prev.map((node, idx) =>
        idx === index
          ? {
              ...node,
              inputs: {
                ...node.inputs,
                [port]: String(sourceNodeId ?? "").trim(),
              },
            }
          : node
      )
    );
  };

  const setDaqNodeParam = (index: number, paramName: string, value: string) => {
    setDaqDraftNodes((prev) =>
      prev.map((node, idx) =>
        idx === index
          ? {
              ...node,
              params: {
                ...node.params,
                [paramName]: value,
              },
            }
          : node
      )
    );
  };

  const addDaqNode = () => {
    const existingIds = new Set(daqDraftNodes.map((node) => node.id));
    let counter = daqDraftNodes.length + 1;
    let nodeId = `node_${counter}`;
    while (existingIds.has(nodeId)) {
      counter += 1;
      nodeId = `node_${counter}`;
    }
    const op: StreamDagOpId = "trace.integrate";
    setDaqDraftNodes((prev) => [
      ...prev,
      {
        id: nodeId,
        op,
        params: defaultParamsForOp(op),
        inputs: defaultInputsForOp(op),
      },
    ]);
  };

  const removeDaqNode = (index: number) => {
    const removed = daqDraftNodes[index];
    if (!removed) {
      return;
    }
    const removedId = removed.id;
    setDaqDraftNodes((prev) => prev.filter((_, idx) => idx !== index));
    setDaqDraftOutputs((prev) => prev.filter((output) => output.nodeId !== removedId));
  };

  const setDaqOutputId = (index: number, outputId: string) => {
    setDaqDraftOutputs((prev) =>
      prev.map((output, idx) =>
        idx === index ? { ...output, outputId: outputId.trim() } : output
      )
    );
  };

  const setDaqOutputNode = (index: number, nodeId: string | null) => {
    setDaqDraftOutputs((prev) =>
      prev.map((output, idx) =>
        idx === index ? { ...output, nodeId: String(nodeId ?? "").trim() } : output
      )
    );
  };

  const addDaqOutput = () => {
    const publishableNodeIds = daqDraftNodes
      .filter((node) => isPublishableNodeKind(nodeKindFromOp(node.op)))
      .map((node) => node.id);
    if (publishableNodeIds.length <= 0) {
        notifications.show({
          color: "yellow",
          title: "No publishable nodes",
          message: "Add a scalar, trace, hist_agg, hist2d, or params_map node first.",
        });
        return;
      }
    const usedOutputIds = new Set(daqDraftOutputs.map((output) => output.outputId));
    let counter = daqDraftOutputs.length + 1;
    let outputId = `out_${counter}`;
    while (usedOutputIds.has(outputId)) {
      counter += 1;
      outputId = `out_${counter}`;
    }
    setDaqDraftOutputs((prev) => [
      ...prev,
      {
        outputId,
        nodeId: publishableNodeIds[0],
      },
    ]);
  };

  const removeDaqOutput = (index: number) => {
    setDaqDraftOutputs((prev) => prev.filter((_, idx) => idx !== index));
  };

  const applyDaqWorkspace = async () => {
    const workspaceId = String(daqWorkspaceId ?? "").trim();
    if (!workspaceId) {
      return;
    }
    const current = streamWorkspacesRef.current[workspaceId];
    if (!current) {
      return;
    }

    const name = daqDraftName.trim() || defaultStreamWorkspaceName(workspaceId);
    const cleanedNodes = daqDraftNodes
      .map((node) => normalizeDagNode(node))
      .filter((node): node is StreamDagNodeConfig => node !== null);
    if (cleanedNodes.length <= 0) {
      notifications.show({
        color: "red",
        title: "Invalid graph",
        message: "At least one node is required.",
      });
      return;
    }
    const nodeIds = cleanedNodes.map((node) => node.id);
    const uniqueNodeIds = new Set(nodeIds);
    if (uniqueNodeIds.size !== nodeIds.length) {
      notifications.show({
        color: "red",
        title: "Invalid graph",
        message: "Node IDs must be unique and non-empty.",
      });
      return;
    }
    const sourceStreamCount = cleanedNodes.filter(
      (node) => node.op === "source.stream"
    ).length;
    if (sourceStreamCount !== 1) {
      notifications.show({
        color: "red",
        title: "Invalid graph",
        message: "Graph must include exactly one source.stream node.",
      });
      return;
    }

    const cleanedOutputs = daqDraftOutputs
      .map((output) => normalizeDagOutput(output))
      .filter((output): output is StreamDagOutputConfig => output !== null)
      .filter((output) => uniqueNodeIds.has(output.nodeId));
    const outputIds = cleanedOutputs.map((output) => output.outputId);
    const uniqueOutputIds = new Set(outputIds);
    if (uniqueOutputIds.size !== outputIds.length) {
      notifications.show({
        color: "red",
        title: "Invalid outputs",
        message: "Output IDs must be unique and non-empty.",
      });
      return;
    }

    const derivedSource = workspaceStreamFromGraphNodes(cleanedNodes, streamCatalogByKey);
    const updated: StreamAnalysisWorkspaceConfig = {
      ...current,
      workspaceId,
      name,
      stream: derivedSource.stream,
      channelIndex: derivedSource.channelIndex,
      graphNodes: cloneDagNodes(cleanedNodes),
      publishOutputs: cloneDagOutputs(cleanedOutputs),
      enabled: daqDraftEnabled !== false,
    };
    const validatePayload = buildStreamAnalysisWorkspacePayload(updated);
    if (streamAnalysisReadyRef.current && validatePayload) {
      const validation = await validateStreamWorkspace(workspaceId, validatePayload);
      if (!validation.ok) {
        notifications.show({
          color: "red",
          title: "Invalid DAG workspace",
          message:
            validation.error?.message ??
            validation.error?.code ??
            "workspace validation failed",
        });
        return;
      }
    }
    setStreamWorkspaces((prev) => ({ ...prev, [workspaceId]: updated }));
    streamWorkspacesRef.current = {
      ...streamWorkspacesRef.current,
      [workspaceId]: updated,
    };
    const scalarOutputIds = new Set(
      workspaceOutputOptionsByKind(updated, "scalar").map((item) => item.value)
    );
    const paramsMapOutputIds = new Set(
      workspaceOutputOptionsByKind(updated, "params_map").map((item) => item.value)
    );
    const traceOutputIds = new Set(
      workspaceOutputOptionsByKind(updated, "trace").map((item) => item.value)
    );
    const histOutputIds = new Set(
      workspaceOutputOptionsByKind(updated, "hist_agg").map((item) => item.value)
    );
    const hist2dOutputIds = new Set(
      workspaceOutputOptionsByKind(updated, "hist2d").map((item) => item.value)
    );
    setPanels((prev) =>
      prev.map((panel) => {
        if (
          !isStreamTracePanel(panel) &&
          !isStreamScalarPanel(panel) &&
          !isStreamParamsPanel(panel) &&
          !isStreamBinStatsPanel(panel) &&
          !isStreamBin2dPanel(panel)
        ) {
          return panel;
        }
        if (panel.workspaceId !== workspaceId) {
          return panel;
        }
        if (isStreamTracePanel(panel)) {
          if (panel.sourceMode !== "dag") {
            return panel;
          }
          const nextOutputId =
            panel.outputId && traceOutputIds.has(panel.outputId)
              ? panel.outputId
              : defaultOutputForKind(updated, "trace");
          streamFramesRef.set(panel.id, []);
          streamTraceOverlayRef.set(panel.id, new Map());
          const overlayOutputIds = (panel.overlayOutputIds ?? []).filter(
            (id) => id !== nextOutputId && traceOutputIds.has(id)
          );
          return {
            ...panel,
            outputId: nextOutputId,
            overlayOutputIds,
            stream: updated.stream,
            channelIndex: updated.channelIndex,
          };
        }
        if (isStreamScalarPanel(panel)) {
          const nextOutputId =
            panel.outputId && scalarOutputIds.has(panel.outputId)
              ? panel.outputId
              : defaultOutputForKind(updated, "scalar");
          clearPanelBuffers(panel.id);
          return {
            ...panel,
            outputId: nextOutputId,
            stream: updated.stream,
            channelIndex: updated.channelIndex,
            analysis: updated.analysis,
          };
        }
        if (isStreamParamsPanel(panel)) {
          const nextOutputIds = (panel.outputIds ?? []).filter((id) =>
            scalarOutputIds.has(id) || paramsMapOutputIds.has(id)
          );
          streamParamsLatestRef.set(panel.id, {});
          return {
            ...panel,
            outputIds: nextOutputIds,
          };
        }
        if (isStreamBin2dPanel(panel)) {
          const nextOutputId =
            panel.outputId && hist2dOutputIds.has(panel.outputId)
              ? panel.outputId
              : defaultOutputForKind(updated, "hist2d");
          streamBin2dRef.delete(panel.id);
          return {
            ...panel,
            outputId: nextOutputId,
          };
        }
        const nextOutputId =
          panel.outputId && histOutputIds.has(panel.outputId)
            ? panel.outputId
            : defaultOutputForKind(updated, "hist_agg");
        streamBinStatsRef.delete(panel.id);
        streamBinStatsOverlayRef.set(panel.id, new Map());
        const nextOverlayOutputIds = (panel.overlayOutputIds ?? []).filter(
          (id) => traceOutputIds.has(id)
        );
        return {
          ...panel,
          outputId: nextOutputId,
          overlayOutputIds: nextOverlayOutputIds,
          stream: updated.stream,
          channelIndex: updated.channelIndex,
          analysis: updated.analysis,
          binStats: updated.binStats,
        };
      })
    );
    setPlotTick((tick) => tick + 1);
    void syncStreamAnalysisWorkspace(workspaceId, "stream-workspace-apply");
  };

  const saveDaqWorkspaceStore = async () => {
    if (!streamAnalysisReadyRef.current || workspaceStoreBusyAction !== null) {
      return;
    }
    setWorkspaceStoreBusyAction("save");
    try {
      const resp = await saveStreamWorkspaceStore();
      if (!resp.ok) {
        notifications.show({
          color: "red",
          title: "Workspace save failed",
          message:
            resp.error?.message ?? resp.error?.code ?? "workspace_store.save failed",
        });
        await refreshWorkspaceStoreStatus("workspace-save", {
          notifyOnError: false,
        });
        return;
      }
      const resultObj =
        resp.result && typeof resp.result === "object"
          ? (resp.result as Record<string, unknown>)
          : {};
      const statusRaw =
        resultObj.status && typeof resultObj.status === "object"
          ? resultObj.status
          : null;
      if (statusRaw) {
        setWorkspaceStoreStatus(normalizeWorkspaceStoreStatus(statusRaw));
      } else {
        await refreshWorkspaceStoreStatus("workspace-save", {
          notifyOnError: false,
        });
      }
      notifications.show({
        color: "teal",
        title: "Workspace file saved",
        message:
          (statusRaw && typeof (statusRaw as { path?: unknown }).path === "string"
            ? String((statusRaw as { path?: unknown }).path)
            : workspaceStoreStatus.path) ?? "workspace store updated",
      });
    } finally {
      setWorkspaceStoreBusyAction(null);
    }
  };

  const reloadDaqWorkspaceStore = async () => {
    if (!streamAnalysisReadyRef.current || workspaceStoreBusyAction !== null) {
      return;
    }
    setWorkspaceStoreBusyAction("reload");
    try {
      const resp = await reloadStreamWorkspaceStore();
      if (!resp.ok) {
        notifications.show({
          color: "red",
          title: "Workspace reload failed",
          message:
            resp.error?.message ?? resp.error?.code ?? "workspace_store.reload failed",
        });
        await refreshWorkspaceStoreStatus("workspace-reload", {
          notifyOnError: false,
        });
        return;
      }
      const resultObj =
        resp.result && typeof resp.result === "object"
          ? (resp.result as Record<string, unknown>)
          : {};
      const statusRaw =
        resultObj.status && typeof resultObj.status === "object"
          ? resultObj.status
          : null;
      if (statusRaw) {
        setWorkspaceStoreStatus(normalizeWorkspaceStoreStatus(statusRaw));
      } else {
        await refreshWorkspaceStoreStatus("workspace-reload", {
          notifyOnError: false,
        });
      }
      await loadStreamAnalysisWorkspaces("workspace-reload", { notifyOnError: false });
      notifications.show({
        color: "teal",
        title: "Workspace file reloaded",
        message: "Runtime DAG workspaces refreshed from disk.",
      });
    } finally {
      setWorkspaceStoreBusyAction(null);
    }
  };

  const startPanelTitleEdit = (panel: PlotPanelState) => {
    setEditingPanelId(panel.id);
    setPanelTitleDraft(panel.title);
  };

  const cancelPanelTitleEdit = () => {
    setEditingPanelId(null);
    setPanelTitleDraft("");
  };

  const commitPanelTitleEdit = () => {
    if (!editingPanelId) {
      return;
    }
    const panelId = editingPanelId;
    const trimmed = panelTitleDraft.trim();
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId
          ? { ...panel, title: trimmed.length > 0 ? trimmed : panel.id }
          : panel
      )
    );
    setEditingPanelId(null);
    setPanelTitleDraft("");
  };

  const onPlotSignal = (deviceId: string, signal: string) => {
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
  };

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

  const handleSignalDragStart = (
    deviceId: string,
    signal: string,
    event: DragEvent<HTMLDivElement>
  ) => {
    const payload: TraceDragPayload = { kind: "trace", deviceId, signal };
    event.dataTransfer.setData("application/json", JSON.stringify(payload));
    event.dataTransfer.effectAllowed = "copy";
  };

  const handleTraceDragStart = (
    panelId: string,
    trace: TraceKey,
    event: DragEvent<HTMLSpanElement>
  ) => {
    const payload: TraceDragPayload = {
      kind: "trace",
      deviceId: trace.deviceId,
      signal: trace.signal,
      originPanelId: panelId,
    };
    event.dataTransfer.setData("application/json", JSON.stringify(payload));
    event.dataTransfer.effectAllowed = "move";
  };

  const handlePanelDragStart = (
    panelId: string,
    event: DragEvent<HTMLElement>
  ) => {
    const payload: PanelDragPayload = {
      kind: "panel",
      panelId,
    };
    setDragPanelId(panelId);
    setDragOverPanelTarget(null);
    event.dataTransfer.setData("application/json", JSON.stringify(payload));
    event.dataTransfer.effectAllowed = "move";
  };

  const insertPanelByIndex = (sourcePanelId: string, insertIndex: number) => {
    if (!sourcePanelId || !Number.isFinite(insertIndex)) {
      return;
    }
    setPanels((prev) => {
      const sourceIndex = prev.findIndex((panel) => panel.id === sourcePanelId);
      if (sourceIndex < 0) {
        return prev;
      }
      const next = [...prev];
      const [moved] = next.splice(sourceIndex, 1);
      const clamped = Math.max(0, Math.min(Math.trunc(insertIndex), next.length));
      next.splice(clamped, 0, moved);
      return next;
    });
  };

  const handlePanelGridDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!dragPanelId) {
      return;
    }
    event.preventDefault();
    if (
      event.target instanceof Element &&
      event.target.closest("[data-panel-card-id]")
    ) {
      return;
    }
    const container = event.currentTarget;
    const entries = collectGridEntries(
      container,
      "data-panel-card-id",
      dragPanelId
    );
    const index = computeInsertIndexFromGrid(entries, event.clientX, event.clientY);
    setDragOverPanelTarget(null);
    setPanelInsertIndex((prev) => (prev === index ? prev : index));
  };

  const handlePanelGridDrop = (event: DragEvent<HTMLDivElement>) => {
    if (!dragPanelId) {
      return;
    }
    event.preventDefault();
    const raw =
      event.dataTransfer.getData("application/json") ||
      event.dataTransfer.getData("text/plain");
    if (!raw) {
      setDragPanelId(null);
      setDragOverPanelTarget(null);
      setPanelInsertIndex(null);
      return;
    }
    try {
      const payload = JSON.parse(raw) as DropPayload;
      if (payload.kind !== "panel" || typeof payload.panelId !== "string") {
        return;
      }
      const sourcePanelId = payload.panelId;
      const container = event.currentTarget;
      const entries = collectGridEntries(
        container,
        "data-panel-card-id",
        sourcePanelId
      );
      const index =
        panelInsertIndex ??
        computeInsertIndexFromGrid(entries, event.clientX, event.clientY);
      insertPanelByIndex(sourcePanelId, index);
    } finally {
      setDragPanelId(null);
      setDragOverPanelTarget(null);
      setPanelInsertIndex(null);
    }
  };

  const handlePanelGridDragLeave = (event: DragEvent<HTMLDivElement>) => {
    if (!dragPanelId) {
      return;
    }
    const nextTarget = event.relatedTarget as Node | null;
    if (nextTarget && event.currentTarget.contains(nextTarget)) {
      return;
    }
    setPanelInsertIndex(null);
    setDragOverPanelTarget(null);
  };

  const handleDropOnPanel = (
    panelId: string,
    event: DragEvent<HTMLDivElement>
  ) => {
    const panelDropMode = computeHorizontalReorderMode(event);
    const raw =
      event.dataTransfer.getData("application/json") ||
      event.dataTransfer.getData("text/plain");
    if (!raw) {
      return;
    }
    try {
      const payload = JSON.parse(raw) as DropPayload;
      if (payload.kind === "panel" && typeof payload.panelId === "string") {
        if (panelDropMode !== "swap") {
          return;
        }
        event.preventDefault();
        event.stopPropagation();
        movePanel(payload.panelId, panelId, panelDropMode);
        setDragOverPanelTarget(null);
        setDragPanelId(null);
        setPanelInsertIndex(null);
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      if (!payload.deviceId || !payload.signal) {
        return;
      }
      const targetPanel = panelsRef.current.find((panel) => panel.id === panelId);
      if (!targetPanel || !isTelemetryPanel(targetPanel)) {
        return;
      }
      if (payload.originPanelId && payload.originPanelId !== panelId) {
        removeTraceFromPanel(payload.originPanelId, {
          deviceId: payload.deviceId,
          signal: payload.signal,
        });
      }
      addTraceToPanel(panelId, payload.deviceId, payload.signal);
    } catch {
      return;
    }
  };

  const handleNavResizeStart = (
    event: React.PointerEvent<HTMLDivElement>
  ) => {
    event.preventDefault();
    resizeRef.current = { startX: event.clientX, startWidth: navWidth };
    setIsResizing(true);
  };

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
    <AppShell
      className="app-shell"
      header={{ height: 72 }}
      padding="lg"
    >
      <AppShell.Header className="app-header">
        <Group h="100%" px="lg" justify="space-between">
          <Group gap="sm">
            <div className="pulse" />
            <Text className="brand" size="lg">
              {instanceLabel}
            </Text>
            {hdfWriterProcess && (
              <Button
                size="xs"
                variant="light"
                color={hdfWriterChipColor}
                loading={hdfWriterLoading}
                onClick={async () => {
                  await openHdfWriterCommands();
                }}
                title={hdfWriterStatus?.error ?? "Open HDF writer commands"}
              >
                HDF {hdfWriterState} | {hdfWriterFileLabel}
              </Button>
            )}
            {hdfShowNoteChiplet && (
              <Button
                size="xs"
                variant="light"
                color="orange"
                leftSection={<IconFileText size={14} />}
                loading={hdfMeasurementSchemaLoading}
                disabled={hdfCommandsBlocked || hdfMeasurementNoteBusy}
                onClick={async () => {
                  await openHdfMeasurementNoteModal();
                }}
                title={
                  hdfMeasurementSchemaDisplayError ??
                  "Add a measurement note to the active HDF file"
                }
              >
                Note ({hdfWriterStatus?.measurementNotesRows ?? 0})
              </Button>
            )}
            {sequencerProcess && (
              <Button.Group>
                <Button
                  size="xs"
                  variant="light"
                  color={sequencerChipColor}
                  loading={sequencerStatusLoading}
                  onClick={async () => {
                    await openSequencerModal();
                  }}
                  title={sequencerStatus?.error ?? sequencerChipTooltip}
                  style={sequencerChipProgressStyle}
                >
                  Sequencer {sequencerRuntimeState}
                  {sequencerChipSuffix}
                </Button>
                <Button
                  size="xs"
                  variant="light"
                  color={sequencerPrimaryAction === "start" ? "teal" : "yellow"}
                  leftSection={sequencerPrimaryIcon}
                  disabled={sequencerPrimaryDisabled}
                  loading={sequencerActionBusy}
                  onClick={async () => {
                    await runSequencerAction(sequencerPrimaryAction);
                  }}
                  title={
                    sequencerPrimaryAction === "start" && !sequencerLoaded
                      ? "Load a sequence before starting"
                      : undefined
                  }
                >
                  {sequencerPrimaryLabel}
                </Button>
              </Button.Group>
            )}
          </Group>
          <Group gap="xs">
            <Button
              size="xs"
              variant="light"
              color="gray"
              leftSection={<IconCpu size={14} />}
              onClick={async () => {
                setProcessOpen(true);
                await refreshProcesses();
              }}
            >
              Processes
            </Button>
            <Button
              size="xs"
              variant="light"
              color={interlockButtonSummary.color}
              leftSection={<IconShieldCheck size={14} />}
              onClick={() => setInterlocksOpen(true)}
              title={interlockButtonSummary.tooltip}
            >
              {interlockButtonSummary.label}
            </Button>
            {showDaqUi ? (
              <Button
                size="xs"
                variant="light"
                color="cyan"
                leftSection={<IconPencil size={14} />}
                onClick={() => {
                  void openDaqModal();
                }}
                title="Open shared stream-analysis workspaces"
              >
                DAG ({Object.keys(streamWorkspaces).length})
              </Button>
            ) : null}
            <Button
              size="xs"
              variant="light"
              color={commandUnreadError ? "red" : "gray"}
              leftSection={<IconTerminal2 size={14} />}
              onClick={() => {
                setCommandUnreadError(false);
                setCommandHistoryOpen(true);
              }}
              title="Latest command requests and replies"
            >
              Commands ({commandHistoryRows.length})
            </Button>
            <Button
              size="xs"
              variant="light"
              color={logsUnreadError ? "red" : "gray"}
              leftSection={<IconFileText size={14} />}
              onClick={() => {
                setLogsUnreadError(false);
                setLogsOpen(true);
              }}
            >
              Logs
            </Button>
            <Button
              size="xs"
              variant="light"
              color="gray"
              leftSection={<IconSettings size={14} />}
              onClick={() => setSettingsOpen(true)}
            >
              Settings
            </Button>
            <Button
              size="xs"
              variant="light"
              leftSection={<IconRefresh size={14} />}
              onClick={async () => {
                const [, nextProcesses] = await Promise.all([
                  refreshDevices(),
                  refreshProcesses(),
                ]);
                const hdfProcess = nextProcesses.find(isHdfWriterProcess);
                if (hdfProcess) {
                  await refreshHdfWriterStatus(hdfProcess.process_id);
                }
                const seqProcess = nextProcesses.find(isSequencerProcess);
                if (seqProcess) {
                  await refreshSequencerStatus(seqProcess.process_id);
                }
              }}
            >
              Refresh status
            </Button>
            <SegmentedControl
              size="xs"
              value={colorScheme}
              onChange={(value) =>
                setColorScheme(value as "light" | "dark" | "auto")
              }
              data={[
                { label: "Light", value: "light" },
                { label: "Dark", value: "dark" },
                { label: "Auto", value: "auto" },
              ]}
            />
            <Badge variant="light" color={telemetryBadgeColor}>
              {telemetryBadgeLabel}
            </Badge>
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Main>
        <div className="app-layout">
          <section className="device-panel" style={{ width: navWidth }}>
            <Group mb="sm" justify="space-between">
              <Group gap={8}>
                <Text fw={600}>Devices</Text>
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
              </Group>
              <Group gap={6}>
                <IconPlug size={16} />
                <Text size="xs" c="dimmed">
                  {devices.length}
                </Text>
              </Group>
            </Group>
            <ScrollArea h="calc(100vh - 180px)" type="never">
              <div
                className="device-grid"
                onDragOver={handleDeviceGridDragOver}
                onDrop={handleDeviceGridDrop}
                onDragLeave={handleDeviceGridDragLeave}
              >
                {orderedDevices.map((device, idx) => (
                  <DeviceCard
                    key={device.device_id}
                    device={device}
                    signals={latestByDevice[device.device_id]}
                    busy={Boolean(deviceBusyById[device.device_id])}
                    onConnect={() => handleDeviceConnect(device.device_id)}
                    onDisconnect={() => handleDeviceDisconnect(device.device_id)}
                    onRestart={() => handleDeviceRestart(device.device_id)}
                    onPlot={(signal) => onPlotSignal(device.device_id, signal)}
                    onCommand={() => openCommand(device.device_id)}
                    onDragSignal={(signal, event) =>
                      handleSignalDragStart(device.device_id, signal, event)
                    }
                    onDeviceDragStart={(event) =>
                      handleDeviceDragStart(device.device_id, event)
                    }
                    onDeviceDragEnd={handleDeviceDragEnd}
                    onDeviceDragOver={(event) =>
                      handleDeviceDragOver(device.device_id, event)
                    }
                    onDeviceDragLeave={() => handleDeviceDragLeave(device.device_id)}
                    onDeviceDrop={(event) => handleDeviceDrop(device.device_id, event)}
                    telemetryCollapsed={Boolean(
                      telemetryCollapsedByDevice[device.device_id]
                    )}
                    onTelemetryToggle={() =>
                      handleDeviceTelemetryToggle(device.device_id)
                    }
                    dragMode={(() => {
                      if (dragOverDeviceTarget?.deviceId === device.device_id) {
                        return dragOverDeviceTarget.mode;
                      }
                      if (!dragDeviceId || deviceInsertIndex == null) {
                        return null;
                      }
                      const withoutDragged = orderedDevices
                        .map((entry) => entry.device_id)
                        .filter((entryId) => entryId !== dragDeviceId);
                      const idxWithoutDragged = withoutDragged.indexOf(device.device_id);
                      if (idxWithoutDragged < 0) {
                        return null;
                      }
                      if (deviceInsertIndex === idxWithoutDragged) {
                        return "before";
                      }
                      if (
                        deviceInsertIndex === withoutDragged.length &&
                        idxWithoutDragged === withoutDragged.length - 1
                      ) {
                        return "after";
                      }
                      return null;
                    })()}
                    isDragging={dragDeviceId === device.device_id}
                    pinnedCommands={pinnedCommands[device.device_id] ?? []}
                    onPinnedCommand={(action) => openCommand(device.device_id, action)}
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
                    index={idx}
                  />
                ))}
              </div>
            </ScrollArea>
          </section>
          <div
            className="layout-resizer"
            onPointerDown={handleNavResizeStart}
            role="separator"
            aria-orientation="vertical"
          />
          <section className="plot-panel-area">
            <Stack gap="lg">
              <Group justify="space-between">
                <Text fw={600}>Plot workspace</Text>
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
              <div
                className="plot-grid"
                onDragOver={handlePanelGridDragOver}
                onDrop={handlePanelGridDrop}
                onDragLeave={handlePanelGridDragLeave}
              >
                {panels.map((panel) => {
                  const isActive = panel.id === activePanelId;
                  const panelBuffers = buffersRef.get(panel.id) ?? new Map();
                  const streamWorkspace =
                    isStreamScalarPanel(panel) ||
                    isStreamParamsPanel(panel) ||
                    isStreamBinStatsPanel(panel) ||
                    isStreamBin2dPanel(panel) ||
                    (isStreamTracePanel(panel) && panel.sourceMode === "dag")
                      ? streamWorkspaces[panel.workspaceId] ?? null
                      : null;
                  const integralOutputOptions = isStreamScalarPanel(panel)
                    ? workspaceOutputOptionsByKind(streamWorkspace, "scalar")
                    : [];
                  const binStatsXLabel = isStreamBinStatsPanel(panel)
                    ? workspaceXAxisLabel(streamWorkspace, panel.outputId)
                    : DEFAULT_STREAM_CONTEXT_FIELD;
                  const binStatsSnapshot = isStreamBinStatsPanel(panel)
                    ? streamBinStatsRef.get(panel.id) ?? null
                    : null;
                  const bin2dSnapshot = isStreamBin2dPanel(panel)
                    ? streamBin2dRef.get(panel.id) ?? null
                    : null;
                  const bin2dXLabel = isStreamBin2dPanel(panel)
                    ? workspaceBin2dAxisLabel(streamWorkspace, panel.outputId, "x")
                    : DEFAULT_STREAM_CONTEXT_FIELD;
                  const bin2dYLabel = isStreamBin2dPanel(panel)
                    ? workspaceBin2dAxisLabel(streamWorkspace, panel.outputId, "y")
                    : "context_y";
                  const panelDragMode = (() => {
                    if (dragOverPanelTarget?.panelId === panel.id) {
                      return dragOverPanelTarget.mode;
                    }
                    if (!dragPanelId || panelInsertIndex == null) {
                      return null;
                    }
                    const withoutDragged = panels
                      .map((entry) => entry.id)
                      .filter((entryId) => entryId !== dragPanelId);
                    const idxWithoutDragged = withoutDragged.indexOf(panel.id);
                    if (idxWithoutDragged < 0) {
                      return null;
                    }
                    if (panelInsertIndex === idxWithoutDragged) {
                      return "before";
                    }
                    if (
                      panelInsertIndex === withoutDragged.length &&
                      idxWithoutDragged === withoutDragged.length - 1
                    ) {
                      return "after";
                    }
                    return null;
                  })();
                  const panelBg =
                    panelDragMode !== null
                      ? computedColorScheme === "dark"
                        ? "rgba(45, 185, 177, 0.18)"
                        : "#f2fbfa"
                      : "var(--card)";
                  const telemetryNumericTraceCount = isTelemetryPanel(panel)
                    ? panel.traces.filter((trace) => trace.valueKind !== "boolean")
                        .length
                    : 0;
                  const telemetryOffset = isTelemetryPanel(panel)
                    ? resolveTelemetryPanelOffset(panel)
                    : null;
                  const telemetryOffsetCompact =
                    typeof telemetryOffset === "number" &&
                    Number.isFinite(telemetryOffset)
                      ? formatOffsetCompact(telemetryOffset)
                      : "n/a";
                  const telemetryOffsetFull =
                    typeof telemetryOffset === "number" &&
                    Number.isFinite(telemetryOffset)
                      ? formatOffsetFull(telemetryOffset)
                      : null;
                  const telemetryOffsetUnit = (() => {
                    if (!isTelemetryPanel(panel)) {
                      return "";
                    }
                    const units = panel.traces
                      .filter((trace) => trace.valueKind !== "boolean")
                      .map((trace) => (typeof trace.units === "string" ? trace.units.trim() : ""))
                      .filter((unit) => unit.length > 0);
                    if (units.length === 0) {
                      return "";
                    }
                    const unique = new Set(units);
                    return unique.size === 1 ? units[0] : "";
                  })();
                  const telemetryOffsetLabel = telemetryOffsetUnit
                    ? `${telemetryOffsetCompact} ${telemetryOffsetUnit}`
                    : telemetryOffsetCompact;
                  const telemetryOffsetFullLabel =
                    telemetryOffsetFull !== null
                      ? telemetryOffsetUnit
                        ? `${telemetryOffsetFull} ${telemetryOffsetUnit}`
                        : telemetryOffsetFull
                      : null;
                  return (
                    <Card
                      key={panel.id}
                      data-panel-card-id={panel.id}
                      className="plot-workspace-card"
                      radius="lg"
                      p="md"
                      style={{
                        border:
                          panelDragMode === "swap"
                            ? "2px dashed #0e9f9a"
                            : isActive
                            ? "2px solid #0e9f9a"
                            : "1px solid var(--card-border)",
                        borderLeft:
                          panelDragMode === "before"
                            ? "3px solid #0e9f9a"
                            : undefined,
                        borderRight:
                          panelDragMode === "after"
                            ? "3px solid #0e9f9a"
                            : undefined,
                        background: panelBg,
                        position: "relative",
                      }}
                      onDragOver={(event) => {
                        if (!dragPanelId || dragPanelId === panel.id) {
                          // Allow telemetry/trace drops on panel cards.
                          event.preventDefault();
                          return;
                        }
                        const mode = computeHorizontalReorderMode(event);
                        if (mode !== "swap") {
                          if (dragOverPanelTarget?.panelId === panel.id) {
                            setDragOverPanelTarget(null);
                          }
                          return;
                        }
                        event.preventDefault();
                        event.stopPropagation();
                        setPanelInsertIndex(null);
                        setDragOverPanelTarget((prev) =>
                          prev && prev.panelId === panel.id && prev.mode === "swap"
                            ? prev
                            : { panelId: panel.id, mode: "swap" }
                        );
                      }}
                      onDragLeave={() => {
                        if (dragOverPanelTarget?.panelId === panel.id) {
                          setDragOverPanelTarget(null);
                        }
                      }}
                      onDrop={(event) => handleDropOnPanel(panel.id, event)}
                    >
                      <div
                        className="panel-drag-handle panel-drag-handle-top"
                        draggable
                        onDragStart={(event) => handlePanelDragStart(panel.id, event)}
                        onDragEnd={() => {
                          setDragPanelId(null);
                          setDragOverPanelTarget(null);
                          setPanelInsertIndex(null);
                        }}
                        title="Drag from border to reorder panels"
                      />
                      <div
                        className="panel-drag-handle panel-drag-handle-right"
                        draggable
                        onDragStart={(event) => handlePanelDragStart(panel.id, event)}
                        onDragEnd={() => {
                          setDragPanelId(null);
                          setDragOverPanelTarget(null);
                          setPanelInsertIndex(null);
                        }}
                        title="Drag from border to reorder panels"
                      />
                      <div
                        className="panel-drag-handle panel-drag-handle-bottom"
                        draggable
                        onDragStart={(event) => handlePanelDragStart(panel.id, event)}
                        onDragEnd={() => {
                          setDragPanelId(null);
                          setDragOverPanelTarget(null);
                          setPanelInsertIndex(null);
                        }}
                        title="Drag from border to reorder panels"
                      />
                      <div
                        className="panel-drag-handle panel-drag-handle-left"
                        draggable
                        onDragStart={(event) => handlePanelDragStart(panel.id, event)}
                        onDragEnd={() => {
                          setDragPanelId(null);
                          setDragOverPanelTarget(null);
                          setPanelInsertIndex(null);
                        }}
                        title="Drag from border to reorder panels"
                      />
                      <Group justify="space-between" align="center">
                        <Group gap="sm" align="center">
                          {editingPanelId === panel.id ? (
                            <Group gap={6} align="center">
                              <TextInput
                                size="xs"
                                w={180}
                                value={panelTitleDraft}
                                onChange={(event) =>
                                  setPanelTitleDraft(event.currentTarget.value)
                                }
                                onKeyDown={(event) => {
                                  if (event.key === "Enter") {
                                    event.preventDefault();
                                    commitPanelTitleEdit();
                                    return;
                                  }
                                  if (event.key === "Escape") {
                                    event.preventDefault();
                                    cancelPanelTitleEdit();
                                  }
                                }}
                                autoFocus
                                placeholder={panel.id}
                              />
                              <ActionIcon
                                size="sm"
                                variant="light"
                                color="teal"
                                onClick={commitPanelTitleEdit}
                              >
                                <IconCheck size={14} />
                              </ActionIcon>
                              <ActionIcon
                                size="sm"
                                variant="light"
                                color="gray"
                                onClick={cancelPanelTitleEdit}
                              >
                                <IconX size={14} />
                              </ActionIcon>
                            </Group>
                          ) : (
                            <Group gap={6} align="center">
                              <Text fw={600}>{panel.title}</Text>
                              <ActionIcon
                                size="sm"
                                variant="subtle"
                                color="gray"
                                onClick={() => startPanelTitleEdit(panel)}
                              >
                                <IconPencil size={14} />
                              </ActionIcon>
                            </Group>
                          )}
                          {dragPanelId === panel.id && (
                            <Badge variant="light" color="blue">
                              Dragging
                            </Badge>
                          )}
                          {isActive ? (
                            <Badge variant="light" color="teal">
                              Active
                            </Badge>
                          ) : (
                            <Button
                              size="xs"
                              variant="light"
                              leftSection={<IconStar size={14} />}
                              onClick={() => setActivePanelId(panel.id)}
                            >
                              Set active
                            </Button>
                          )}
                          <Badge
                            variant="light"
                            color={
                              isTelemetryPanel(panel)
                                ? "teal"
                                : isStreamRawPanel(panel)
                                ? "orange"
                                : isStreamWaterfallPanel(panel)
                                ? "cyan"
                                : isStreamParamsPanel(panel)
                                ? "lime"
                                : isStreamBinStatsPanel(panel)
                                ? "blue"
                                : isStreamBin2dPanel(panel)
                                ? "violet"
                                : "green"
                            }
                          >
                            {isTelemetryPanel(panel)
                              ? "Telemetry"
                              : isStreamRawPanel(panel)
                              ? "Stream trace"
                              : isStreamWaterfallPanel(panel)
                              ? "Stream waterfall"
                              : isStreamParamsPanel(panel)
                              ? "Stream params"
                              : isStreamBinStatsPanel(panel)
                              ? "Stream bin stats"
                              : isStreamBin2dPanel(panel)
                              ? "Stream 2D bins"
                              : "Stream scalar"}
                          </Badge>
                          {!isStreamParamsPanel(panel) ? (
                            <Group gap={6} align="center">
                              <Text size="xs" c="dimmed">
                                {isStreamWaterfallPanel(panel) || isStreamBin2dPanel(panel)
                                  ? "Z"
                                  : "Y"}
                              </Text>
                              <SegmentedControl
                                size="xs"
                                value={panel.yScaleMode}
                                onChange={(value) => {
                                  const nextMode = value as YScaleMode;
                                  if (nextMode === "auto") {
                                    setPanelYScaleMode(panel.id, "auto");
                                    return;
                                  }
                                  openYAxisModal(panel.id, { prefillFromAuto: true });
                                }}
                                data={[
                                  { value: "auto", label: "Auto" },
                                  {
                                    value: "manual",
                                    label: (
                                      <span
                                        onMouseDown={() => {
                                          if (panel.yScaleMode === "manual") {
                                            openYAxisModal(panel.id);
                                          }
                                        }}
                                      >
                                        Manual
                                      </span>
                                    ),
                                  },
                                ]}
                              />
                            </Group>
                          ) : null}
                          {isTelemetryPanel(panel) ? (
                            <Group gap={6} align="center" wrap="wrap">
                              <Text size="xs" c="dimmed">
                                Window (s)
                              </Text>
                              <NumberInput
                                size="xs"
                                w={110}
                                min={5}
                                max={600}
                                value={panel.timeWindowS}
                                onChange={(value) =>
                                  setPanelTimeWindow(panel.id, Number(value))
                                }
                              />
                              <Text size="xs" c="dimmed">
                                Display
                              </Text>
                              <SegmentedControl
                                size="xs"
                                value={panel.yDisplayMode}
                                data={[
                                  { value: "absolute", label: "Abs" },
                                  { value: "delta", label: "Delta" },
                                ]}
                                onChange={(value) => {
                                  const nextMode = value as YDisplayMode;
                                  if (
                                    nextMode === "delta" &&
                                    telemetryNumericTraceCount === 0
                                  ) {
                                    notifications.show({
                                      color: "yellow",
                                      title: "No numeric traces",
                                      message:
                                        "Delta display requires at least one numeric telemetry trace.",
                                    });
                                    return;
                                  }
                                  setTelemetryYDisplayMode(panel.id, nextMode);
                                }}
                              />
                              {panel.yDisplayMode === "delta" && (
                                <>
                                  <Text size="xs" c="dimmed">
                                    Offset
                                  </Text>
                                  <SegmentedControl
                                    size="xs"
                                    value={panel.yOffsetMode}
                                    data={[
                                      { value: "auto", label: "Auto" },
                                      { value: "freeze", label: "Freeze" },
                                    ]}
                                    onChange={(value) => {
                                      const nextMode = value as YOffsetMode;
                                      if (nextMode === "auto") {
                                        setTelemetryYOffsetMode(panel.id, "auto");
                                        return;
                                      }
                                      if (
                                        typeof telemetryOffset !== "number" ||
                                        !Number.isFinite(telemetryOffset)
                                      ) {
                                        notifications.show({
                                          color: "yellow",
                                          title: "Offset unavailable",
                                          message:
                                            "No numeric telemetry samples available to freeze offset yet.",
                                        });
                                        return;
                                      }
                                      setTelemetryYOffsetMode(
                                        panel.id,
                                        "freeze",
                                        telemetryOffset
                                      );
                                    }}
                                  />
                                  <Badge
                                    variant="light"
                                    color={
                                      panel.yOffsetMode === "freeze"
                                        ? "blue"
                                        : "gray"
                                    }
                                  >
                                    offset: {telemetryOffsetLabel}
                                  </Badge>
                                </>
                              )}
                            </Group>
                          ) : isStreamTracePanel(panel) ? (
                            <Group gap={6} align="center" wrap="wrap">
                              <Badge
                                variant="light"
                                color={panel.sourceMode === "raw" ? "orange" : "teal"}
                              >
                                {panel.sourceMode === "raw" ? "Raw source" : "DAG source"}
                              </Badge>
                              <Button
                                size="xs"
                                variant="light"
                                leftSection={<IconSettings size={14} />}
                                onClick={() => openStreamTraceOptionsModal(panel.id)}
                              >
                                Plot options
                              </Button>
                            </Group>
                          ) : isStreamScalarPanel(panel) ? (
                            <Group gap={6} align="center" wrap="wrap">
                              <Select
                                size="xs"
                                w={260}
                                searchable
                                placeholder="Select workspace"
                                comboboxProps={{ zIndex: 500 }}
                                data={streamWorkspaceOptions}
                                value={panel.workspaceId}
                                onChange={(value) =>
                                  setStreamAnalysisPanelWorkspace(panel.id, value)
                                }
                              />
                              <Select
                                size="xs"
                                w={320}
                                searchable
                                clearable
                                placeholder="Select scalar output"
                                comboboxProps={{ zIndex: 500 }}
                                data={integralOutputOptions}
                                value={panel.outputId}
                                onChange={(value) =>
                                  setStreamAnalysisPanelOutput(panel.id, value)
                                }
                              />
                              <Group gap={6} align="center">
                                <Text size="xs" c="dimmed">
                                  Window (s)
                                </Text>
                                <NumberInput
                                  size="xs"
                                  w={110}
                                  min={5}
                                  max={600}
                                  value={panel.timeWindowS}
                                  onChange={(value) =>
                                    setPanelTimeWindow(panel.id, Number(value))
                                  }
                                />
                              </Group>
                            </Group>
                          ) : isStreamParamsPanel(panel) ? (
                            <Group gap={6} align="center" wrap="wrap">
                              <Button
                                size="xs"
                                variant="light"
                                leftSection={<IconSettings size={14} />}
                                onClick={() => openStreamParamsOptionsModal(panel.id)}
                              >
                                Plot options
                              </Button>
                            </Group>
                          ) : (
                            <Group gap={6} align="center" wrap="wrap">
                              <Button
                                size="xs"
                                variant="light"
                                leftSection={<IconSettings size={14} />}
                                onClick={() => {
                                  if (isStreamBin2dPanel(panel)) {
                                    openStreamBin2dOptionsModal(panel.id);
                                    return;
                                  }
                                  openStreamBinStatsOptionsModal(panel.id);
                                }}
                              >
                                Plot options
                              </Button>
                            </Group>
                          )}
                        </Group>
                        <Group gap="xs">
                          <Button
                            size="xs"
                            variant="light"
                            onClick={() => {
                              if (isTelemetryPanel(panel) || isStreamScalarPanel(panel)) {
                                clearPanelBuffers(panel.id);
                                return;
                              }
                              if (isStreamParamsPanel(panel)) {
                                streamParamsLatestRef.set(panel.id, {});
                                setPlotTick((tick) => tick + 1);
                                return;
                              }
                              if (isStreamBinStatsPanel(panel)) {
                                void clearStreamBinStatsPanel(panel.id);
                                return;
                              }
                              if (isStreamBin2dPanel(panel)) {
                                void clearStreamBin2dPanel(panel.id);
                                return;
                              }
                              clearStreamPanelFrames(panel.id);
                            }}
                          >
                            {isStreamBinStatsPanel(panel) || isStreamBin2dPanel(panel)
                              ? "Clear binned data"
                              : "Clear"}
                          </Button>
                          <ActionIcon
                            variant="light"
                            color="red"
                            onClick={() => removePanel(panel.id)}
                            disabled={panels.length <= 1}
                          >
                            <IconTrash size={14} />
                          </ActionIcon>
                        </Group>
                      </Group>
                      {isTelemetryPanel(panel) ? (
                        <>
                          <PlotPanel
                            traces={panel.traces}
                            buffers={panelBuffers}
                            tick={plotTick}
                            timeWindowS={panel.timeWindowS}
                            colorScheme={computedColorScheme}
                            yScaleMode={panel.yScaleMode}
                            yMin={panel.yMin}
                            yMax={panel.yMax}
                            yDisplayMode={panel.yDisplayMode}
                            yOffset={telemetryOffset}
                          />
                          <Group gap="sm" wrap="wrap" mt="sm">
                            {panel.traces.map((trace, traceIndex) => {
                              const traceColor = traceColorAt(traceIndex);
                              return (
                                <span
                                  key={traceKeyId(trace)}
                                  className="trace-chip"
                                  draggable
                                  onDragStart={(event) =>
                                    handleTraceDragStart(panel.id, trace, event)
                                  }
                                  style={{
                                    cursor: "grab",
                                    color: traceColor,
                                    background: colorWithAlpha(
                                      traceColor,
                                      computedColorScheme === "dark" ? 0.22 : 0.14
                                    ),
                                    border: `1px solid ${colorWithAlpha(
                                      traceColor,
                                      computedColorScheme === "dark" ? 0.45 : 0.3
                                    )}`,
                                  }}
                                >
                                  {trace.deviceId}.{trace.signal}
                                  <ActionIcon
                                    size="sm"
                                    variant="subtle"
                                    color="red"
                                    onClick={() => removeTraceFromPanel(panel.id, trace)}
                                    aria-label={`Remove ${trace.deviceId}.${trace.signal}`}
                                    title="Remove trace"
                                  >
                                    <IconX size={14} />
                                  </ActionIcon>
                                </span>
                              );
                            })}
                          </Group>
                        </>
                      ) : isStreamTracePanel(panel) ? (
                        <>
                          {isStreamRawPanel(panel) ? (
                            <StreamRawPanel
                              frames={streamFramesRef.get(panel.id) ?? []}
                              overlayCount={panel.overlayCount}
                              channelIndex={panel.sourceMode === "raw" ? panel.channelIndex : 0}
                              tick={plotTick}
                              colorScheme={computedColorScheme}
                              units={panel.stream?.units ?? null}
                              extraSeries={
                                panel.sourceMode === "dag"
                                  ? streamTraceOverlaySeries(panel)
                                  : []
                              }
                              yScaleMode={panel.yScaleMode}
                              yMin={panel.yMin}
                              yMax={panel.yMax}
                            />
                          ) : (
                            <StreamWaterfallPanel
                              frames={streamFramesRef.get(panel.id) ?? []}
                              historyRows={panel.overlayCount}
                              channelIndex={panel.sourceMode === "raw" ? panel.channelIndex : 0}
                              tick={plotTick}
                              colorScheme={computedColorScheme}
                              zScaleMode={panel.yScaleMode}
                              zMin={panel.yMin}
                              zMax={panel.yMax}
                            />
                          )}
                          <Group gap="sm" wrap="wrap" mt="sm">
                            <Badge
                              variant="light"
                              color={panel.sourceMode === "raw" ? "orange" : "teal"}
                              onClick={() => openStreamTraceOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              source: {panel.sourceMode}
                            </Badge>
                            {panel.sourceMode === "raw" ? (
                              <>
                                {panel.stream ? (
                                  <Badge
                                    variant="light"
                                    color="orange"
                                    onClick={() => openStreamTraceOptionsModal(panel.id)}
                                    style={{ cursor: "pointer" }}
                                  >
                                    {panel.stream.deviceId}.{panel.stream.stream}
                                  </Badge>
                                ) : (
                                  <Text size="xs" c="dimmed">
                                    Select a stream to start plotting raw frames.
                                  </Text>
                                )}
                                {panel.stream &&
                                inferChannelCountFromShape(panel.stream.shape) > 1 ? (
                                  <Badge
                                    variant="light"
                                    color="indigo"
                                    onClick={() => openStreamTraceOptionsModal(panel.id)}
                                    style={{ cursor: "pointer" }}
                                  >
                                    ch: {panel.channelIndex}
                                  </Badge>
                                ) : null}
                              </>
                            ) : (
                              <>
                                {streamWorkspace ? (
                                  <Badge
                                    variant="light"
                                    color="teal"
                                    onClick={() => openStreamTraceOptionsModal(panel.id)}
                                    style={{ cursor: "pointer" }}
                                  >
                                    {streamWorkspace.name}
                                  </Badge>
                                ) : null}
                                {streamWorkspace?.stream ? (
                                  <Badge
                                    variant="light"
                                    color="orange"
                                    onClick={() => openStreamTraceOptionsModal(panel.id)}
                                    style={{ cursor: "pointer" }}
                                  >
                                    {streamWorkspace.stream.deviceId}.{streamWorkspace.stream.stream}
                                  </Badge>
                                ) : (
                                  <Text size="xs" c="dimmed">
                                    Bind this panel to a configured DAG workspace.
                                  </Text>
                                )}
                                <Badge
                                  variant="light"
                                  color="teal"
                                  onClick={() => openStreamTraceOptionsModal(panel.id)}
                                  style={{ cursor: "pointer" }}
                                >
                                  output: {panel.outputId ?? "none"}
                                </Badge>
                              </>
                            )}
                            {panel.overlayCount > 1 ? (
                              <Badge
                                variant="light"
                                color="indigo"
                                onClick={() => openStreamTraceOptionsModal(panel.id)}
                                style={{ cursor: "pointer" }}
                              >
                                {isStreamWaterfallPanel(panel) ? "rows" : "N"}:{" "}
                                {panel.overlayCount}
                              </Badge>
                            ) : null}
                            {panel.rollingWindow > 1 ? (
                              <Badge
                                variant="light"
                                color="indigo"
                                onClick={() => openStreamTraceOptionsModal(panel.id)}
                                style={{ cursor: "pointer" }}
                              >
                                avg({panel.averageMode}): {panel.rollingWindow}
                              </Badge>
                            ) : null}
                            <Badge
                              variant="light"
                              color="indigo"
                              onClick={() => openStreamTraceOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              decimator:{" "}
                              {panel.traceDecimator === "minmax"
                                ? "min-max"
                                : panel.traceDecimator}
                            </Badge>
                            <Badge
                              variant="light"
                              color="indigo"
                              onClick={() => openStreamTraceOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              pts: {panel.traceMaxPoints}
                            </Badge>
                            <Badge
                              variant="light"
                              color="indigo"
                              onClick={() => openStreamTraceOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              hz: {panel.traceMaxFps.toFixed(1)}
                            </Badge>
                            <Badge variant="light" color="indigo">
                              {isStreamWaterfallPanel(panel) ? "z" : "y"}:{" "}
                              {panel.yScaleMode === "manual" &&
                              Number.isFinite(panel.yMin ?? NaN) &&
                              Number.isFinite(panel.yMax ?? NaN)
                                ? `manual (${Number(panel.yMin).toPrecision(4)}, ${Number(
                                    panel.yMax
                                  ).toPrecision(4)})`
                                : "auto"}
                            </Badge>
                            <Badge
                              variant="light"
                              color={
                                panel.sourceMode === "raw"
                                  ? streamWsConnected
                                    ? "teal"
                                    : "red"
                                  : streamAnalysisWsConnected
                                  ? "teal"
                                  : "red"
                              }
                            >
                              {panel.sourceMode === "raw" ? "stream" : "analysis"} link:{" "}
                              {panel.sourceMode === "raw"
                                ? streamWsConnected
                                  ? "connected"
                                  : "disconnected"
                                : streamAnalysisWsConnected
                                ? "connected"
                                : "disconnected"}
                            </Badge>
                          </Group>
                        </>
                      ) : isStreamScalarPanel(panel) ? (
                        <>
                          <PlotPanel
                            traces={[streamScalarTrace(panel)]}
                            buffers={panelBuffers}
                            tick={plotTick}
                            timeWindowS={panel.timeWindowS}
                            colorScheme={computedColorScheme}
                            yScaleMode={panel.yScaleMode}
                            yMin={panel.yMin}
                            yMax={panel.yMax}
                          />
                          <Group gap="sm" wrap="wrap" mt="sm">
                            {streamWorkspace ? (
                              <Badge variant="light" color="teal">
                                {streamWorkspace.name}
                              </Badge>
                            ) : null}
                            {streamWorkspace?.stream ? (
                              <Badge variant="light" color="green">
                                {streamWorkspace.stream.deviceId}.{streamWorkspace.stream.stream}
                              </Badge>
                            ) : (
                              <Text size="xs" c="dimmed">
                                Bind this panel to a configured DAG workspace.
                              </Text>
                            )}
                            <Badge variant="light" color="teal">
                              output: {panel.outputId ?? "none"}
                            </Badge>
                            <Text size="xs" c="dimmed">
                              Analysis link{" "}
                              {streamAnalysisWsConnected ? "connected" : "disconnected"}
                            </Text>
                          </Group>
                        </>
                      ) : isStreamParamsPanel(panel) ? (
                        <>
                          <StreamParamsPanel
                            valuesByOutputId={streamParamsLatestRef.get(panel.id) ?? {}}
                            selectedOutputIds={panel.outputIds}
                            onCopyJson={(payload) => {
                              void copyTextToClipboard("Params JSON", payload);
                            }}
                          />
                          <Group gap="sm" wrap="wrap" mt="sm">
                            {streamWorkspace ? (
                              <Badge
                                variant="light"
                                color="teal"
                                onClick={() => openStreamParamsOptionsModal(panel.id)}
                                style={{ cursor: "pointer" }}
                              >
                                {streamWorkspace.name}
                              </Badge>
                            ) : null}
                            <Badge
                              variant="light"
                              color="indigo"
                              onClick={() => openStreamParamsOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              selected: {panel.outputIds.length}
                            </Badge>
                            <Badge
                              variant="light"
                              color={streamAnalysisWsConnected ? "teal" : "red"}
                            >
                              analysis link:{" "}
                              {streamAnalysisWsConnected ? "connected" : "disconnected"}
                            </Badge>
                          </Group>
                        </>
                      ) : isStreamBinStatsPanel(panel) ? (
                        <>
                        <StreamBinStatsPanel
                          series={binStatsSnapshot?.series ?? null}
                          overlaySeries={streamBinStatsOverlaySeries(panel)}
                          xLabel={binStatsXLabel}
                          uncertaintyMode={panel.uncertaintyMode}
                          uncertaintyScale={panel.uncertaintyScale}
                            tick={plotTick}
                            colorScheme={computedColorScheme}
                            yScaleMode={panel.yScaleMode}
                            yMin={panel.yMin}
                            yMax={panel.yMax}
                          />
                          <Group gap="sm" wrap="wrap" mt="sm">
                            {streamWorkspace ? (
                              <Badge
                                variant="light"
                                color="teal"
                                onClick={() => openStreamBinStatsOptionsModal(panel.id)}
                                style={{ cursor: "pointer" }}
                              >
                                {streamWorkspace.name}
                              </Badge>
                            ) : null}
                            {streamWorkspace?.stream ? (
                              <Badge
                                variant="light"
                                color="blue"
                                onClick={() => openStreamBinStatsOptionsModal(panel.id)}
                                style={{ cursor: "pointer" }}
                              >
                                {streamWorkspace.stream.deviceId}.{streamWorkspace.stream.stream}
                              </Badge>
                            ) : (
                              <Text size="xs" c="dimmed">
                                Bind this panel to a configured DAG workspace.
                              </Text>
                            )}
                            <Badge variant="light" color="indigo">
                              x: {binStatsXLabel}
                            </Badge>
                            <Badge
                              variant="light"
                              color="indigo"
                              onClick={() => openStreamBinStatsOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              output: {panel.outputId ?? "none"}
                            </Badge>
                            <Badge variant="light" color="indigo">
                              bins:{" "}
                              {(() => {
                                const active =
                                  binStatsSnapshot?.populatedBinCount ??
                                  binStatsSnapshot?.activeBinCount ??
                                  null;
                                const max = binStatsSnapshot?.maxBinCount ?? null;
                                if (active === null || max === null || max <= 0) {
                                  return "n/a";
                                }
                                return `${active}/${max}`;
                              })()}
                            </Badge>
                            <Badge
                              variant="light"
                              color="indigo"
                              onClick={() => openStreamBinStatsOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              mode: {panel.uncertaintyMode}
                            </Badge>
                            {panel.uncertaintyScale !== 1 ? (
                              <Badge
                                variant="light"
                                color="indigo"
                                onClick={() => openStreamBinStatsOptionsModal(panel.id)}
                                style={{ cursor: "pointer" }}
                              >
                                k: {panel.uncertaintyScale}
                              </Badge>
                            ) : null}
                            <Badge
                              variant="light"
                              color={streamAnalysisWsConnected ? "teal" : "red"}
                            >
                              analysis link:{" "}
                              {streamAnalysisWsConnected ? "connected" : "disconnected"}
                            </Badge>
                          </Group>
                        </>
                      ) : isStreamBin2dPanel(panel) ? (
                        <>
                          <StreamBin2dPanel
                            series={bin2dSnapshot?.series ?? null}
                            reducer={panel.reducer}
                            tick={plotTick}
                            colorScheme={computedColorScheme}
                            zScaleMode={panel.yScaleMode}
                            zMin={panel.yMin}
                            zMax={panel.yMax}
                          />
                          <Group gap="sm" wrap="wrap" mt="sm">
                            {streamWorkspace ? (
                              <Badge
                                variant="light"
                                color="teal"
                                onClick={() => openStreamBin2dOptionsModal(panel.id)}
                                style={{ cursor: "pointer" }}
                              >
                                {streamWorkspace.name}
                              </Badge>
                            ) : null}
                            <Badge
                              variant="light"
                              color="indigo"
                              onClick={() => openStreamBin2dOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              output: {panel.outputId ?? "none"}
                            </Badge>
                            <Badge variant="light" color="indigo">
                              x: {bin2dXLabel}
                            </Badge>
                            <Badge variant="light" color="indigo">
                              y: {bin2dYLabel}
                            </Badge>
                            <Badge
                              variant="light"
                              color="indigo"
                              onClick={() => openStreamBin2dOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              mode: {panel.reducer}
                            </Badge>
                            <Badge variant="light" color="indigo">
                              bins:{" "}
                              {(() => {
                                const xActive = bin2dSnapshot?.xActiveBinCount ?? null;
                                const yActive = bin2dSnapshot?.yActiveBinCount ?? null;
                                const xMax = bin2dSnapshot?.xMaxBinCount ?? null;
                                const yMax = bin2dSnapshot?.yMaxBinCount ?? null;
                                if (
                                  xActive === null ||
                                  yActive === null ||
                                  xMax === null ||
                                  yMax === null ||
                                  xMax <= 0 ||
                                  yMax <= 0
                                ) {
                                  return "n/a";
                                }
                                return `${xActive}x${yActive}/${xMax}x${yMax}`;
                              })()}
                            </Badge>
                            <Badge variant="light" color="indigo">
                              filled: {bin2dSnapshot?.populatedBinCount ?? "n/a"}
                            </Badge>
                            <Badge variant="light" color="indigo">
                              dropped: {bin2dSnapshot?.droppedSamples ?? "n/a"}
                            </Badge>
                            <Badge
                              variant="light"
                              color={streamAnalysisWsConnected ? "teal" : "red"}
                            >
                              analysis link:{" "}
                              {streamAnalysisWsConnected ? "connected" : "disconnected"}
                            </Badge>
                          </Group>
                        </>
                      ) : null}
                      
                    </Card>
                  );
                })}
              </div>
            </Stack>
          </section>
        </div>
      </AppShell.Main>

      <YAxisModal
        opened={yAxisModalPanelId !== null}
        onClose={closeYAxisModal}
        title={`Y axis ${yAxisModalPanel?.title ?? ""}`}
        autoRange={yAxisAutoRange}
        draftMin={yAxisDraftMin}
        onDraftMinChange={setYAxisDraftMin}
        draftMax={yAxisDraftMax}
        onDraftMaxChange={setYAxisDraftMax}
        draftInvalid={yAxisDraftInvalid}
        onApply={applyYAxisModal}
      />

      <StreamTraceOptionsModal
        opened={streamTraceOptionsPanel !== null}
        onClose={closeStreamTraceOptionsModal}
        panel={streamTraceOptionsPanel}
        streamTargetOptions={streamTargetOptions}
        streamWorkspaceOptions={streamWorkspaceOptions}
        traceOutputOptions={streamTraceOptionsTraceOutputOptions}
        overlayTraceOutputOptions={streamTraceOptionsOverlayOutputOptions}
        onSetSourceMode={setStreamTracePanelSourceMode}
        onSetOverlayCount={setStreamPanelOverlayCount}
        onSetRollingWindow={setStreamPanelRollingWindow}
        onSetAverageMode={setStreamPanelAverageMode}
        onRawTargetKeyChange={setStreamPanelTargetFromKey}
        onSetChannelIndex={setStreamPanelChannelIndex}
        onSetWorkspace={setStreamTracePanelWorkspace}
        onSetOutput={setStreamTracePanelOutput}
        onSetOverlayOutputs={setStreamTracePanelOverlayOutputs}
        onSetTraceDecimator={setStreamPanelTraceDecimator}
        onSetTraceMaxPoints={setStreamPanelTraceMaxPoints}
        onSetTraceMaxFps={setStreamPanelTraceMaxFps}
      />

      <StreamBinStatsOptionsModal
        opened={streamBinStatsOptionsPanel !== null}
        onClose={closeStreamBinStatsOptionsModal}
        panel={streamBinStatsOptionsPanel}
        streamWorkspaceOptions={streamWorkspaceOptions}
        outputOptions={streamBinStatsOptionsOutputOptions}
        overlayTraceOutputOptions={streamBinStatsOptionsTraceOverlayOptions}
        xAxisLabel={streamBinStatsOptionsXLabel}
        onSetWorkspace={setStreamAnalysisPanelWorkspace}
        onSetOutput={setStreamAnalysisPanelOutput}
        onSetOverlayOutputs={setStreamBinStatsOverlayOutputs}
        onSetUncertainty={setStreamBinStatsUncertainty}
      />

      <StreamParamsOptionsModal
        opened={streamParamsOptionsPanel !== null}
        onClose={closeStreamParamsOptionsModal}
        panel={streamParamsOptionsPanel}
        streamWorkspaceOptions={streamWorkspaceOptions}
        outputOptions={streamParamsOutputOptions}
        onSetWorkspace={setStreamAnalysisPanelWorkspace}
        onSetOutputs={setStreamParamsPanelOutputs}
      />

      <StreamBin2dOptionsModal
        opened={streamBin2dOptionsPanel !== null}
        onClose={closeStreamBin2dOptionsModal}
        panel={streamBin2dOptionsPanel}
        streamWorkspaceOptions={streamWorkspaceOptions}
        outputOptions={streamBin2dOptionsOutputOptions}
        xAxisLabel={streamBin2dOptionsXLabel}
        yAxisLabel={streamBin2dOptionsYLabel}
        onSetWorkspace={setStreamAnalysisPanelWorkspace}
        onSetOutput={setStreamAnalysisPanelOutput}
        onSetReducer={setStreamBin2dReducer}
      />

      <DaqWorkspacesModal
        opened={daqOpen}
        onClose={closeDaqModal}
        streamWorkspaceOptions={streamWorkspaceOptions}
        streamCatalogByKey={streamCatalogByKey}
        daqWorkspaceId={daqWorkspaceId}
        onWorkspaceChange={loadDaqWorkspaceDraft}
        onCreateWorkspace={createStreamWorkspace}
        workspaceStoreStatus={workspaceStoreStatus}
        daqWorkspace={daqWorkspace}
        daqDraftName={daqDraftName}
        onDraftNameChange={setDaqDraftName}
        daqDraftEnabled={daqDraftEnabled}
        onDraftEnabledChange={setDaqDraftEnabled}
        daqSectionCardStyle={daqSectionCardStyle}
        daqNodeCardBaseStyle={daqNodeCardBaseStyle}
        daqDraftNodes={daqDraftNodes}
        daqDraftOutputs={daqDraftOutputs}
        daqFocusedNodeId={daqFocusedNodeId}
        daqNodeCardRefs={daqNodeCardRefs}
        daqResetNodeBusyId={daqResetNodeBusyId}
        streamAnalysisRpcReady={streamAnalysisRpcReady}
        onResetDaqNodeAggregate={resetDaqNodeAggregate}
        onAddNode={addDaqNode}
        onRemoveNode={removeDaqNode}
        onSetNodeId={setDaqNodeId}
        onSetNodeOp={setDaqNodeOp}
        onSetNodeInput={setDaqNodeInput}
        onSetNodeParam={setDaqNodeParam}
        onAddOutput={addDaqOutput}
        onRemoveOutput={removeDaqOutput}
        onSetOutputId={setDaqOutputId}
        onSetOutputNode={setDaqOutputNode}
        daqPublishableNodeOptions={daqPublishableNodeOptions}
        daqResettableNodeIds={daqResettableNodeIds}
        onFocusNodeCard={focusDaqNodeCard}
        onReloadStore={reloadDaqWorkspaceStore}
        onSaveStore={saveDaqWorkspaceStore}
        workspaceStoreBusyAction={workspaceStoreBusyAction}
        onApplyWorkspace={applyDaqWorkspace}
      />

      <DeviceCommandModal
        opened={commandOpen}
        onClose={() => setCommandOpen(false)}
        title={
          commandDevice ? (
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
          )
        }
        capabilities={capabilitiesForActive}
        commandAction={commandAction}
        onActionChange={handleActionChange}
        commandLabel={commandLabel}
        onLabelChange={handleLabelChange}
        showAdvancedParams={showAdvancedParams}
        onShowAdvancedParamsChange={setShowAdvancedParams}
        activeParams={activeParams}
        commandParamValues={commandParamValues}
        onParamValueChange={(name, value) =>
          setCommandParamValues((prev) => ({
            ...prev,
            [name]: value,
          }))
        }
        commandParams={commandParams}
        onCommandParamsChange={setCommandParams}
        isPinned={isPinned}
        pinDisabled={!commandAction || !commandDevice}
        onTogglePin={handlePinClick}
        onExecute={executeCommand}
      />

      <HdfMeasurementNoteModal
        opened={hdfNoteModalOpen}
        onClose={() => setHdfNoteModalOpen(false)}
        title={`Measurement Note ${hdfWriterProcessId ?? ""}`}
        hdfWriterState={hdfWriterState}
        measurementType={hdfWriterStatus?.measurementType ?? null}
        measurementNotesRows={hdfWriterStatus?.measurementNotesRows ?? 0}
        filePath={hdfWriterStatus?.filePath ?? null}
        refreshLoading={hdfMeasurementSchemaLoading || hdfStatusBusy}
        refreshDisabled={hdfCommandsBlocked || hdfAnyCommandBusy}
        onRefresh={async () => {
          if (!hdfWriterProcessId) {
            return;
          }
          await Promise.all([
            refreshHdfWriterStatus(hdfWriterProcessId),
            fetchHdfMeasurementSchema(hdfWriterProcessId),
          ]);
        }}
        showMeasurementUi={hdfShowMeasurementUi}
        supportsMeasurementNote={hdfSupportsMeasurementNote}
        fields={hdfMeasurementSchema?.notes.fields ?? []}
        renderMeasurementFieldInput={renderMeasurementFieldInput}
        noteValuesDraft={hdfNoteValuesDraft}
        noteCustomByField={hdfNoteCustomByField}
        onSetFieldValue={setHdfNoteFieldValue}
        onSetFieldUseCustom={setHdfNoteFieldUseCustom}
        measurementNoteBusy={hdfMeasurementNoteBusy}
        addNoteDisabled={
          !hdfShowMeasurementUi || hdfCommandsBlocked || !hdfSupportsMeasurementNote
        }
        onAddNote={executeHdfMeasurementNote}
      />

      <HdfWriterModal
        opened={hdfModalOpen}
        onClose={() => setHdfModalOpen(false)}
        title={`HDF Writer ${hdfWriterProcessId ?? ""}`}
        hdfWriterState={hdfWriterState}
        hdfWriterProcessId={hdfWriterProcessId}
        hdfWriterStatus={hdfWriterStatus ?? null}
        hdfWriterLoading={hdfWriterLoading}
        hdfStatusBusy={hdfStatusBusy}
        hdfCommandsBlocked={hdfCommandsBlocked}
        hdfSupportsStatus={hdfSupportsStatus}
        hdfAnyCommandBusy={hdfAnyCommandBusy}
        onRefreshStatus={executeHdfStatus}
        hdfProcessCapabilitiesError={hdfProcessCapabilitiesError ?? null}
        hdfMeasurementSchemaConfigured={hdfMeasurementSchemaConfigured}
        hdfMeasurementSchemaAvailable={hdfMeasurementSchemaAvailable}
        hdfSelectableDeviceIds={hdfSelectableDeviceIds}
        hdfMeasurementSchemaDisplayPath={hdfMeasurementSchemaDisplayPath}
        hdfMeasurementSchemaDisplayError={hdfMeasurementSchemaDisplayError}
        hdfSupportsMeasurementSchemaGet={hdfSupportsMeasurementSchemaGet}
        hdfMeasurementSchemaLoading={hdfMeasurementSchemaLoading}
        onRefreshSchema={async () => {
          if (!hdfWriterProcessId) {
            return;
          }
          await fetchHdfMeasurementSchema(hdfWriterProcessId);
        }}
        hdfRotateFilenameDraft={hdfRotateFilenameDraft}
        onRotateFilenameChange={setHdfRotateFilenameDraft}
        hdfRotateDisabledDevicesDraft={hdfRotateDisabledDevicesDraft}
        onRotateDisabledDevicesChange={setHdfRotateDisabledDevicesDraft}
        hdfSelectableDeviceOptions={hdfSelectableDeviceOptions}
        hdfShowMeasurementUi={hdfShowMeasurementUi}
        hdfRotateMeasurementProfileDraft={hdfRotateMeasurementProfileDraft}
        hdfRotateProfileOptions={hdfRotateProfileOptions}
        onSelectRotateMeasurementProfile={selectHdfRotateMeasurementProfile}
        hdfRotateSelectedProfile={hdfRotateSelectedProfile}
        renderMeasurementFieldInput={renderMeasurementFieldInput}
        hdfRotateMeasurementValuesDraft={hdfRotateMeasurementValuesDraft}
        hdfRotateMeasurementCustomByField={hdfRotateMeasurementCustomByField}
        onSetRotateFieldValue={setHdfRotateFieldValue}
        onSetRotateFieldUseCustom={setHdfRotateFieldUseCustom}
        hdfRotateBusy={hdfRotateBusy}
        hdfSupportsRotate={hdfSupportsRotate}
        onExecuteRotate={executeHdfRotate}
        hdfSupportsMeasurementNote={hdfSupportsMeasurementNote}
        hdfMeasurementSchema={hdfMeasurementSchema}
        hdfNoteValuesDraft={hdfNoteValuesDraft}
        hdfNoteCustomByField={hdfNoteCustomByField}
        onSetNoteFieldValue={setHdfNoteFieldValue}
        onSetNoteFieldUseCustom={setHdfNoteFieldUseCustom}
        hdfMeasurementNoteBusy={hdfMeasurementNoteBusy}
        onExecuteMeasurementNote={executeHdfMeasurementNote}
        hdfDevicesGetBusy={hdfDevicesGetBusy}
        hdfSupportsDevicesGet={hdfSupportsDevicesGet}
        onExecuteDevicesGet={executeHdfDevicesGet}
        hdfEnableDevicesDraft={hdfEnableDevicesDraft}
        onEnableDevicesDraftChange={setHdfEnableDevicesDraft}
        hdfDevicesEnableBusy={hdfDevicesEnableBusy}
        hdfSupportsDevicesEnable={hdfSupportsDevicesEnable}
        onExecuteDevicesEnable={executeHdfDevicesEnable}
        hdfDisableDevicesDraft={hdfDisableDevicesDraft}
        onDisableDevicesDraftChange={setHdfDisableDevicesDraft}
        hdfDevicesDisableBusy={hdfDevicesDisableBusy}
        hdfSupportsDevicesDisable={hdfSupportsDevicesDisable}
        onExecuteDevicesDisable={executeHdfDevicesDisable}
      />

      <ProcessCommandModal
        opened={processCommandOpen}
        onClose={() => setProcessCommandOpen(false)}
        title={processCommandTitle}
        capabilities={capabilitiesForProcessCommand}
        commandAction={processCommandAction}
        onActionChange={handleProcessCommandActionChange}
        showAdvancedParams={processShowAdvancedParams}
        onShowAdvancedParamsChange={setProcessShowAdvancedParams}
        activeParams={activeProcessParams}
        commandParamValues={processCommandParamValues}
        onParamValueChange={(name, value) =>
          setProcessCommandParamValues((prev) => ({
            ...prev,
            [name]: value,
          }))
        }
        commandParams={processCommandParams}
        onCommandParamsChange={setProcessCommandParams}
        onExecute={executeProcessCommand}
      />

      <ProcessesModal
        opened={processOpen}
        onClose={() => setProcessOpen(false)}
        processes={processes}
        capabilitiesByProcess={capabilitiesByProcess}
        busyByProcess={processBusyById}
        errorByProcess={processCapabilitiesErrorById}
        onRefresh={refreshProcesses}
        onProcessAction={handleProcessAction}
        onOpenCommand={openProcessCommand}
      />

      <SettingsModal
        opened={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        settingsFileInputRef={settingsFileInputRef}
        onImportUiProfile={importUiProfile}
        onExportUiProfile={exportUiProfile}
        onReload={loadGatewayRuntimeSettings}
        loading={settingsLoading}
        error={settingsError}
        gatewaySettings={gatewaySettings}
        resolvedApiBase={resolvedApiBase}
        resolvedWsBase={resolvedWsBase}
        telemetryStreamStatus={telemetryStreamStatus}
      />

      <InterlocksModal
        opened={interlocksOpen}
        onClose={() => setInterlocksOpen(false)}
        onRefresh={refreshInterlocksModalData}
        devices={devices}
        processes={interlocksPanelProcesses}
        followerRulesByProcessId={followerRulesByProcessId}
        interlockStatusByProcessId={interlockStatusByProcessId}
        interlocksLoadingByProcessId={interlocksLoadingByProcessId}
        interlocksErrorByProcessId={interlocksErrorByProcessId}
        interlockRuleBusyByKey={interlockRuleBusyByKey}
        commandInterceptorRoutes={commandInterceptorRoutes}
        commandInterceptorRoutesLoading={commandInterceptorRoutesLoading}
        commandInterceptorRoutesError={commandInterceptorRoutesError}
        onRefreshProcess={(processId) =>
          refreshInterlockProcessStatus(processId, undefined, {
            showLoading: true,
          })
        }
        onToggleFollowerRule={toggleFollowerRule}
        onToggleInterlockRule={toggleInterlockRule}
      />

      <SequencerModal
        opened={sequencerOpen}
        onClose={() => setSequencerOpen(false)}
        processState={sequencerProcessState}
        runtimeState={sequencerRuntimeState}
        loaded={sequencerLoaded}
        currentStep={sequencerStatus?.currentStep ?? null}
        progress={sequencerProgress}
        progressPercent={sequencerProgressPercent}
        totalSteps={sequencerTotalSteps}
        completedSteps={sequencerCompletedSteps}
        loadedSource={sequencerStatus?.loadedSource ?? null}
        autoloadError={sequencerStatus?.autoloadError ?? null}
        statusError={sequencerStatus?.error ?? null}
        modalError={sequencerModalError}
        primaryIcon={sequencerPrimaryIcon}
        primaryAction={sequencerPrimaryAction}
        primaryLabel={sequencerPrimaryLabel}
        primaryDisabled={sequencerPrimaryDisabled}
        actionBusy={sequencerActionBusy}
        onRunAction={runSequencerAction}
        fileInputRef={sequencerFileInputRef}
        onFileInputChange={handleSequencerFileInput}
        yamlViewMode={sequencerYamlViewMode}
        onYamlViewModeChange={setSequencerYamlViewMode}
        loadedYamlBusy={sequencerLoadedYamlBusy}
        hasSequencerProcess={Boolean(sequencerProcess)}
        onShowLoadedYaml={async () => {
          if (!sequencerProcess) {
            return;
          }
          await fetchSequencerLoadedYaml(sequencerProcess.process_id, {
            applyToEditor: true,
          });
        }}
        validateBusy={sequencerValidateBusy}
        onValidate={validateSequencerYaml}
        loadBusy={sequencerLoadBusy}
        onLoad={loadSequencerYaml}
        editorRef={sequencerEditorRef}
        yamlText={sequencerYamlText}
        onYamlTextChange={onSequencerYamlTextChange}
        colorScheme={computedColorScheme}
        diagnostics={sequencerDiagnostics}
        onJumpToDiagnostic={jumpToSequencerDiagnostic}
      />

      <CommandHistoryModal
        opened={commandHistoryOpen}
        onClose={() => setCommandHistoryOpen(false)}
        filteredRows={filteredCommandHistoryRows}
        devices={devices}
        totalRows={commandHistoryRows.length}
        persistLimit={commandHistoryLimit}
        persistLimitMin={MIN_COMMAND_HISTORY_LIMIT}
        persistLimitMax={MAX_COMMAND_HISTORY_LIMIT}
        onPersistLimitChange={(value) =>
          setCommandHistoryLimit(
            clampCommandHistoryLimit(value, COMMAND_HISTORY_LIMIT_BOUNDS)
          )
        }
        autoScroll={commandHistoryAutoScroll}
        onAutoScrollChange={setCommandHistoryAutoScroll}
        onClear={() => setCommandHistoryRows([])}
        targetFilter={commandHistoryTargetFilter}
        onTargetFilterChange={setCommandHistoryTargetFilter}
        statusFilter={commandHistoryStatusFilter}
        onStatusFilterChange={setCommandHistoryStatusFilter}
        sourceFilter={commandHistorySourceFilter}
        onSourceFilterChange={setCommandHistorySourceFilter}
        sourceOptions={commandHistorySourceOptions}
        textFilter={commandHistoryTextFilter}
        onTextFilterChange={setCommandHistoryTextFilter}
        viewportRef={commandHistoryScrollRef}
        onCopyJson={(label, payload) => {
          void copyJsonToClipboard(label, payload);
        }}
      />

      <LogsModal
        opened={logsOpen}
        onClose={() => setLogsOpen(false)}
        connected={logsWsConnected}
        filteredRows={filteredLogRows}
        totalRows={logRows.length}
        autoScroll={logAutoScroll}
        onAutoScrollChange={setLogAutoScroll}
        loading={logLoading}
        onReload={() => {
          void loadLogTail();
        }}
        onClear={() => {
          logSeenRef.current = new Set();
          setLogRows([]);
          setExpandedLogByKey({});
        }}
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
        processes={processes}
        viewportRef={logScrollRef}
        expandedByKey={expandedLogByKey}
        onToggleExpanded={(entryKey) =>
          setExpandedLogByKey((prev) => ({
            ...prev,
            [entryKey]: !Boolean(prev[entryKey]),
          }))
        }
        onCopyMessage={(message) => {
          void copyTextToClipboard("Log message", message);
        }}
      />
    </AppShell>
  );
}


