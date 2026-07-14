import type { Bin2dReducer, StreamBin2dSeries } from "../../components/StreamBin2dPanel";
import type {
  StreamBinStatsSeries,
  UncertaintyMode,
} from "../../components/StreamBinStatsPanel";
import type { TraceKey } from "../../types";

export type StreamDagOpId =
  | "source.stream"
  | "source.context_field"
  | "source.telemetry_nearest"
  | "scalar.add"
  | "scalar.subtract"
  | "scalar.multiply"
  | "scalar.divide"
  | "scalar.threshold"
  | "trace.divide"
  | "trace.add_scalar"
  | "trace.subtract_scalar"
  | "trace.multiply_scalar"
  | "trace.divide_scalar"
  | "trace.rolling_mean"
  | "trace.decimate"
  | "trace.crop"
  | "trace.subtract_background"
  | "trace.integrate"
  | "trace.scale"
  | "fit.curve_1d"
  | "fit.yhat"
  | "fit.xhat"
  | "fit.yhat_dense"
  | "fit.xhat_dense"
  | "fit.param"
  | "fit.params"
  | "fit.from_hist_agg"
  | "aggregate.bin_stats"
  | "aggregate.bin2d_stats";

export type StreamDagOutputKind =
  | "trace"
  | "scalar"
  | "hist_agg"
  | "hist2d"
  | "fit_1d"
  | "params_map";

export type StreamFitParamSample = {
  value: number | null;
  stderr: number | null;
};

export type StreamFitParamsMap = Record<string, StreamFitParamSample>;

export type StreamParamsOutputValue = number | StreamFitParamsMap;

export type StreamDagNodeConfig = {
  nodeId: string;
  op: StreamDagOpId;
  params: Record<string, unknown>;
  inputs: Record<string, string>;
};

export type StreamDagOutputConfig = {
  outputId: string;
  nodeId: string;
};

export type StreamDagParamField = {
  name: string;
  label: string;
  kind: "string" | "number" | "integer" | "boolean";
  optional?: boolean;
  placeholder?: string;
  options?: Array<{ value: string; label: string }>;
};

export type StreamDagOpDef = {
  label: string;
  inputs: string[];
  optionalInputs?: string[];
  outputKind: StreamDagOutputKind;
  params: StreamDagParamField[];
};

export type PanelKind =
  | "telemetry"
  | "stream_raw"
  | "stream_waterfall"
  | "stream_scalar"
  | "stream_params"
  | "stream_bin_stats"
  | "stream_bin2d";

export type StreamTraceSourceMode = "raw" | "dag";
export type StreamTraceDecimator = "stride" | "mean" | "minmax" | "m4";
export type StreamTraceAverageMode = "block" | "rolling";
export type YScaleMode = "auto" | "manual";
export type YDisplayMode = "absolute" | "delta";
export type YOffsetMode = "auto" | "freeze";
export type TelemetrySmoothingMode = "none" | "sma" | "ema";

export type StreamTarget = {
  deviceId: string;
  stream: string;
  units?: string | null;
  shape?: number[];
};

export type StreamAnalysisSettings = {
  traceStartIdx: number;
  traceStopIdx: number | null;
  backgroundEnabled: boolean;
  backgroundStartIdx: number;
  backgroundStopIdx: number;
};

export type StreamBinStatsSettings = {
  contextField: string;
  xMin: number;
  xMax: number;
  binCount: number;
  autoRange: boolean;
};

export type StreamAnalysisWorkspaceConfig = {
  workspaceId: string;
  name: string;
  stream: StreamTarget | null;
  channelIndex: number;
  analysis: StreamAnalysisSettings;
  binStats: StreamBinStatsSettings;
  graphNodes: StreamDagNodeConfig[];
  publishOutputs: StreamDagOutputConfig[];
  enabled: boolean;
};

export type StreamWorkspaceSummary = {
  workspaceId: string;
  revision: number;
  etag: string | null;
};

export type StreamWorkspaceStoreStatus = {
  path: string | null;
  exists: boolean;
  dirty: boolean;
  workspaceCount: number;
  lastLoadedWallS: number | null;
  lastSavedWallS: number | null;
  lastError: string | null;
};

export type PlotTelemetryPanelState = {
  id: string;
  title: string;
  kind: "telemetry";
  traces: TraceKey[];
  timeWindowS: number;
  yScaleMode: YScaleMode;
  yMin: number | null;
  yMax: number | null;
  yDisplayMode: YDisplayMode;
  yOffsetMode: YOffsetMode;
  yOffsetValue: number | null;
  smoothingMode: TelemetrySmoothingMode;
  smoothingWindowS: number;
};

export type PlotStreamPanelState = {
  id: string;
  title: string;
  kind: "stream_raw";
  sourceMode: StreamTraceSourceMode;
  stream: StreamTarget | null;
  overlayCount: number;
  channelIndex: number;
  /**
   * Additional channels (beyond the primary `channelIndex`) plotted on
   * the same raw stream panel. Empty for single-channel panels. When
   * non-empty the panel is in "multi-channel" mode: one line per
   * channel showing the latest frame only (overlay-N is ignored).
   */
  extraChannelIndices: number[];
  workspaceId: string;
  outputId: string | null;
  overlayOutputIds: string[];
  traceDecimator: StreamTraceDecimator;
  traceMaxPoints: number;
  traceMaxFps: number;
  rollingWindow: number;
  averageMode: StreamTraceAverageMode;
  yScaleMode: YScaleMode;
  yMin: number | null;
  yMax: number | null;
};

export type PlotStreamWaterfallPanelState = {
  id: string;
  title: string;
  kind: "stream_waterfall";
  sourceMode: StreamTraceSourceMode;
  stream: StreamTarget | null;
  overlayCount: number;
  channelIndex: number;
  workspaceId: string;
  outputId: string | null;
  overlayOutputIds: string[];
  traceDecimator: StreamTraceDecimator;
  traceMaxPoints: number;
  traceMaxFps: number;
  rollingWindow: number;
  averageMode: StreamTraceAverageMode;
  yScaleMode: YScaleMode;
  yMin: number | null;
  yMax: number | null;
};

export type PlotStreamScalarPanelState = {
  id: string;
  title: string;
  kind: "stream_scalar";
  workspaceId: string;
  outputId: string | null;
  stream: StreamTarget | null;
  channelIndex: number;
  analysis: StreamAnalysisSettings;
  timeWindowS: number;
  yScaleMode: YScaleMode;
  yMin: number | null;
  yMax: number | null;
};

export type PlotStreamParamsPanelState = {
  id: string;
  title: string;
  kind: "stream_params";
  workspaceId: string;
  outputIds: string[];
};

export type PlotStreamBinStatsPanelState = {
  id: string;
  title: string;
  kind: "stream_bin_stats";
  workspaceId: string;
  outputId: string | null;
  overlayOutputIds: string[];
  fitOverlayOutputIds: string[];
  stream: StreamTarget | null;
  channelIndex: number;
  analysis: StreamAnalysisSettings;
  binStats: StreamBinStatsSettings;
  uncertaintyMode: UncertaintyMode;
  uncertaintyScale: number;
  showBinMarkers: boolean;
  xOffset: number;
  xScale: number;
  yScaleMode: YScaleMode;
  yMin: number | null;
  yMax: number | null;
};

export type PlotStreamBin2dPanelState = {
  id: string;
  title: string;
  kind: "stream_bin2d";
  workspaceId: string;
  outputId: string | null;
  reducer: Bin2dReducer;
  yScaleMode: YScaleMode;
  yMin: number | null;
  yMax: number | null;
};

export type PlotPanelState =
  | PlotTelemetryPanelState
  | PlotStreamPanelState
  | PlotStreamWaterfallPanelState
  | PlotStreamScalarPanelState
  | PlotStreamParamsPanelState
  | PlotStreamBinStatsPanelState
  | PlotStreamBin2dPanelState;

export type StreamFrameSample = {
  seq: number;
  shape: number[];
  values: unknown;
  truncated?: boolean;
  originalShape?: number[];
  originalPointCount?: number | null;
  maxPayloadPoints?: number | null;
};

export type StreamAnalysisWorkspaceSubscription = {
  workspaceId: string;
  kinds: Array<"scalar" | "hist_agg" | "hist2d" | "trace" | "params_map" | "fit_1d">;
  traceDecimator?: StreamTraceDecimator;
  traceMaxPoints?: number;
  traceMaxFps?: number;
  traceRollingWindow?: number;
  traceAverageMode?: StreamTraceAverageMode;
};

export type RawStreamSubscription = {
  deviceId: string;
  stream: string;
  channelIndex: number;
  traceDecimator: StreamTraceDecimator;
  traceMaxPoints: number;
  traceMaxFps: number;
  rollingWindow: number;
  averageMode: StreamTraceAverageMode;
};

export type StreamBinStatsSnapshot = {
  series: StreamBinStatsSeries;
  activeBinCount: number | null;
  populatedBinCount: number | null;
  maxBinCount: number | null;
  xMin: number | null;
  xMax: number | null;
  autoRange: boolean | null;
};

export type StreamFitCurveSnapshot = {
  x: number[];
  yhat: number[];
  xDense: number[] | null;
  yhatDense: number[] | null;
};

export type StreamBin2dSnapshot = {
  series: StreamBin2dSeries;
  xActiveBinCount: number | null;
  yActiveBinCount: number | null;
  xMaxBinCount: number | null;
  yMaxBinCount: number | null;
  populatedBinCount: number | null;
  xMin: number | null;
  xMax: number | null;
  yMin: number | null;
  yMax: number | null;
  xAutoRange: boolean | null;
  yAutoRange: boolean | null;
  droppedSamples: number | null;
};

export type PanelDragPayload = {
  kind: "panel";
  panelId: string;
};

export type TraceDragPayload = {
  kind: "trace";
  deviceId: string;
  signal: string;
  originPanelId?: string;
};

export type DropPayload = {
  kind?: "panel" | "trace";
  panelId?: string;
  deviceId?: string;
  signal?: string;
  originPanelId?: string;
};

export type ReorderMode = "swap" | "before" | "after";
