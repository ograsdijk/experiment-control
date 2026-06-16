import type {
  StreamDagNodeConfig,
  StreamDagOpDef,
  StreamDagOpId,
  StreamDagOutputConfig,
  StreamDagOutputKind,
  StreamDagParamField,
} from "./types";
import {
  DEFAULT_BIN_COUNT,
  DEFAULT_BIN_X_MAX,
  DEFAULT_BIN_X_MIN,
  DEFAULT_STREAM_CONTEXT_FIELD,
  DEFAULT_TRACE_MAX_POINTS,
} from "./utils";

export const SPECIAL_SAMPLE_INDEX_INPUT = "__sample_index__";

export const STREAM_DAG_OPS: Record<StreamDagOpId, StreamDagOpDef> = {
  "source.stream": {
    label: "source.stream",
    inputs: [],
    outputKind: "trace",
    params: [
      { name: "device_id", label: "device_id", kind: "string" },
      { name: "stream", label: "stream", kind: "string" },
      { name: "channel_mode", label: "channel_mode", kind: "string", optional: true },
      { name: "channel_index", label: "channel_index", kind: "integer", optional: true },
      {
        name: "channel_indices",
        label: "channel_indices",
        kind: "string",
        optional: true,
        placeholder: "all or e.g. 0,1,2",
      },
    ],
  },
  "source.context_field": {
    label: "source.context_field",
    inputs: [],
    outputKind: "scalar",
    params: [{ name: "field", label: "field", kind: "string" }],
  },
  "source.telemetry_nearest": {
    label: "source.telemetry_nearest",
    inputs: [],
    outputKind: "scalar",
    params: [
      { name: "device_id", label: "device_id", kind: "string" },
      { name: "signal", label: "signal", kind: "string" },
      { name: "max_dt_s", label: "max_dt_s", kind: "number" },
    ],
  },
  "scalar.add": {
    label: "scalar.add",
    inputs: ["a", "b"],
    outputKind: "scalar",
    params: [],
  },
  "scalar.subtract": {
    label: "scalar.subtract",
    inputs: ["a", "b"],
    outputKind: "scalar",
    params: [],
  },
  "scalar.multiply": {
    label: "scalar.multiply",
    inputs: ["a", "b"],
    outputKind: "scalar",
    params: [],
  },
  "scalar.divide": {
    label: "scalar.divide",
    inputs: ["a", "b"],
    outputKind: "scalar",
    params: [],
  },
  "scalar.threshold": {
    label: "scalar.threshold",
    inputs: ["x"],
    outputKind: "scalar",
    params: [
      { name: "threshold", label: "threshold", kind: "number" },
      {
        name: "mode",
        label: "mode",
        kind: "string",
        optional: true,
        options: [
          { value: "gt", label: ">" },
          { value: "gte", label: ">=" },
          { value: "lt", label: "<" },
          { value: "lte", label: "<=" },
        ],
      },
    ],
  },
  "trace.divide": {
    label: "trace.divide",
    inputs: ["a", "b"],
    outputKind: "trace",
    params: [],
  },
  "trace.add_scalar": {
    label: "trace.add_scalar",
    inputs: ["trace", "scalar"],
    outputKind: "trace",
    params: [],
  },
  "trace.subtract_scalar": {
    label: "trace.subtract_scalar",
    inputs: ["trace", "scalar"],
    outputKind: "trace",
    params: [],
  },
  "trace.multiply_scalar": {
    label: "trace.multiply_scalar",
    inputs: ["trace", "scalar"],
    outputKind: "trace",
    params: [],
  },
  "trace.divide_scalar": {
    label: "trace.divide_scalar",
    inputs: ["trace", "scalar"],
    outputKind: "trace",
    params: [],
  },
  "trace.rolling_mean": {
    label: "trace.rolling_mean",
    inputs: ["trace"],
    outputKind: "trace",
    params: [{ name: "window_traces", label: "window_traces", kind: "integer" }],
  },
  "trace.decimate": {
    label: "trace.decimate",
    inputs: ["trace"],
    outputKind: "trace",
    params: [
      { name: "method", label: "method", kind: "string", optional: true },
      { name: "target_points", label: "target_points", kind: "integer" },
    ],
  },
  "trace.crop": {
    label: "trace.crop",
    inputs: ["trace"],
    outputKind: "trace",
    params: [
      { name: "start_idx", label: "start_idx", kind: "integer" },
      {
        name: "stop_idx",
        label: "stop_idx",
        kind: "integer",
        optional: true,
        placeholder: "optional",
      },
    ],
  },
  "trace.subtract_background": {
    label: "trace.subtract_background",
    inputs: ["trace"],
    outputKind: "trace",
    params: [
      { name: "bg_start_idx", label: "bg_start_idx", kind: "integer" },
      { name: "bg_stop_idx", label: "bg_stop_idx", kind: "integer" },
    ],
  },
  "trace.integrate": {
    label: "trace.integrate",
    inputs: ["trace"],
    outputKind: "scalar",
    params: [],
  },
  "trace.scale": {
    label: "trace.scale",
    inputs: ["trace"],
    outputKind: "trace",
    params: [{ name: "factor", label: "factor", kind: "number" }],
  },
  "fit.curve_1d": {
    label: "fit.curve_1d",
    inputs: ["x", "y"],
    optionalInputs: ["gate"],
    outputKind: "fit_1d",
    params: [
      {
        name: "model",
        label: "model",
        kind: "string",
        optional: true,
        options: [
          { value: "gaussian", label: "gaussian" },
          { value: "lorentzian", label: "lorentzian" },
        ],
      },
      {
        name: "baseline_mode",
        label: "baseline_mode",
        kind: "string",
        optional: true,
        options: [
          { value: "none", label: "none" },
          { value: "constant", label: "constant" },
          { value: "linear", label: "linear" },
        ],
      },
      { name: "every_n", label: "every_n", kind: "integer", optional: true },
      { name: "sigma_y", label: "sigma_y", kind: "number", optional: true },
      {
        name: "dense_eval_points",
        label: "dense_eval_points",
        kind: "integer",
        optional: true,
      },
    ],
  },
  "fit.yhat": {
    label: "fit.yhat",
    inputs: ["fit"],
    outputKind: "trace",
    params: [],
  },
  "fit.xhat": {
    label: "fit.xhat",
    inputs: ["fit"],
    outputKind: "trace",
    params: [],
  },
  "fit.yhat_dense": {
    label: "fit.yhat_dense",
    inputs: ["fit"],
    outputKind: "trace",
    params: [],
  },
  "fit.xhat_dense": {
    label: "fit.xhat_dense",
    inputs: ["fit"],
    outputKind: "trace",
    params: [],
  },
  "fit.param": {
    label: "fit.param",
    inputs: ["fit"],
    outputKind: "scalar",
    params: [
      { name: "name", label: "name", kind: "string", optional: true },
      {
        name: "field",
        label: "field",
        kind: "string",
        optional: true,
        options: [
          { value: "value", label: "value" },
          { value: "stderr", label: "stderr" },
        ],
      },
    ],
  },
  "fit.params": {
    label: "fit.params",
    inputs: ["fit"],
    outputKind: "params_map",
    params: [],
  },
  "fit.from_hist_agg": {
    label: "fit.from_hist_agg",
    inputs: ["hist"],
    optionalInputs: ["gate"],
    outputKind: "fit_1d",
    params: [
      {
        name: "y_source",
        label: "y_source",
        kind: "string",
        optional: true,
        options: [
          { value: "mean", label: "mean" },
          { value: "std", label: "std" },
          { value: "sem", label: "sem" },
          { value: "count", label: "count" },
        ],
      },
      {
        name: "model",
        label: "model",
        kind: "string",
        optional: true,
        options: [
          { value: "gaussian", label: "gaussian" },
          { value: "lorentzian", label: "lorentzian" },
        ],
      },
      {
        name: "baseline_mode",
        label: "baseline_mode",
        kind: "string",
        optional: true,
        options: [
          { value: "none", label: "none" },
          { value: "constant", label: "constant" },
          { value: "linear", label: "linear" },
        ],
      },
      { name: "every_n", label: "every_n", kind: "integer", optional: true },
      { name: "sigma_y", label: "sigma_y", kind: "number", optional: true },
      {
        name: "dense_eval_points",
        label: "dense_eval_points",
        kind: "integer",
        optional: true,
      },
      {
        name: "chi2_sigma_source",
        label: "chi2_sigma_source",
        kind: "string",
        optional: true,
        options: [
          { value: "sem", label: "sem" },
          { value: "std", label: "std" },
          { value: "none", label: "none" },
        ],
      },
      { name: "min_count", label: "min_count", kind: "integer", optional: true },
      { name: "x_min", label: "x_min", kind: "number", optional: true },
      { name: "x_max", label: "x_max", kind: "number", optional: true },
    ],
  },
  "aggregate.bin_stats": {
    label: "aggregate.bin_stats",
    inputs: ["x", "y"],
    optionalInputs: ["gate"],
    outputKind: "hist_agg",
    params: [
      { name: "auto_range", label: "auto_range", kind: "boolean" },
      { name: "x_min", label: "x_min", kind: "number", placeholder: "e.g. 1.958e9" },
      { name: "x_max", label: "x_max", kind: "number", placeholder: "e.g. 1.962e9" },
      { name: "bin_count", label: "bin_count", kind: "integer" },
    ],
  },
  "aggregate.bin2d_stats": {
    label: "aggregate.bin2d_stats",
    inputs: ["x", "y", "z"],
    optionalInputs: ["gate"],
    outputKind: "hist2d",
    params: [
      { name: "x_auto_range", label: "x_auto_range", kind: "boolean" },
      { name: "x_min", label: "x_min", kind: "number", placeholder: "e.g. 1.958e9" },
      { name: "x_max", label: "x_max", kind: "number", placeholder: "e.g. 1.962e9" },
      { name: "x_bin_count", label: "x_bin_count", kind: "integer" },
      { name: "y_auto_range", label: "y_auto_range", kind: "boolean" },
      { name: "y_min", label: "y_min", kind: "number", placeholder: "e.g. 0.4" },
      { name: "y_max", label: "y_max", kind: "number", placeholder: "e.g. 1.1" },
      { name: "y_bin_count", label: "y_bin_count", kind: "integer" },
    ],
  },
};

export const STREAM_DAG_INPUT_KINDS: Record<
  StreamDagOpId,
  Partial<Record<string, StreamDagOutputKind>>
> = {
  "source.stream": {},
  "source.context_field": {},
  "source.telemetry_nearest": {},
  "scalar.add": { a: "scalar", b: "scalar" },
  "scalar.subtract": { a: "scalar", b: "scalar" },
  "scalar.multiply": { a: "scalar", b: "scalar" },
  "scalar.divide": { a: "scalar", b: "scalar" },
  "scalar.threshold": { x: "scalar" },
  "trace.divide": { a: "trace", b: "trace" },
  "trace.add_scalar": { trace: "trace", scalar: "scalar" },
  "trace.subtract_scalar": { trace: "trace", scalar: "scalar" },
  "trace.multiply_scalar": { trace: "trace", scalar: "scalar" },
  "trace.divide_scalar": { trace: "trace", scalar: "scalar" },
  "trace.rolling_mean": { trace: "trace" },
  "trace.decimate": { trace: "trace" },
  "trace.crop": { trace: "trace" },
  "trace.subtract_background": { trace: "trace" },
  "trace.integrate": { trace: "trace" },
  "trace.scale": { trace: "trace" },
  "fit.curve_1d": { x: "trace", y: "trace", gate: "scalar" },
  "fit.yhat": { fit: "fit_1d" },
  "fit.xhat": { fit: "fit_1d" },
  "fit.yhat_dense": { fit: "fit_1d" },
  "fit.xhat_dense": { fit: "fit_1d" },
  "fit.param": { fit: "fit_1d" },
  "fit.params": { fit: "fit_1d" },
  "fit.from_hist_agg": { hist: "hist_agg", gate: "scalar" },
  "aggregate.bin_stats": { x: "scalar", y: "scalar", gate: "scalar" },
  "aggregate.bin2d_stats": { x: "scalar", y: "scalar", z: "scalar", gate: "scalar" },
};

export const STREAM_DAG_OP_OPTIONS = (Object.keys(STREAM_DAG_OPS) as StreamDagOpId[]).map(
  (op) => ({
    value: op,
    label: STREAM_DAG_OPS[op].label,
  })
);

export function defaultParamsForOp(op: StreamDagOpId): Record<string, unknown> {
  if (op === "source.stream") {
    return {
      device_id: "",
      stream: "",
      channel_mode: "single",
      channel_index: 0,
      channel_indices: "",
    };
  }
  if (op === "source.context_field") {
    return { field: DEFAULT_STREAM_CONTEXT_FIELD };
  }
  if (op === "source.telemetry_nearest") {
    return { device_id: "", signal: "", max_dt_s: 2.0 };
  }
  if (op === "scalar.threshold") {
    return { threshold: 0, mode: "gt" };
  }
  if (op === "trace.crop") {
    return { start_idx: 0 };
  }
  if (op === "trace.subtract_background") {
    return { bg_start_idx: 0, bg_stop_idx: 1 };
  }
  if (op === "trace.scale") {
    return { factor: 1.0 };
  }
  if (op === "trace.rolling_mean") {
    return { window_traces: 1 };
  }
  if (op === "trace.decimate") {
    return { method: "minmax", target_points: DEFAULT_TRACE_MAX_POINTS };
  }
  if (op === "fit.curve_1d") {
    return {
      model: "gaussian",
      baseline_mode: "none",
      every_n: 1,
      dense_eval_points: 200,
    };
  }
  if (op === "fit.param") {
    return { name: "center", field: "value" };
  }
  if (op === "fit.from_hist_agg") {
    return {
      y_source: "mean",
      model: "gaussian",
      baseline_mode: "none",
      every_n: 1,
      dense_eval_points: 200,
      chi2_sigma_source: "sem",
      min_count: 1,
    };
  }
  if (op === "aggregate.bin_stats") {
    return {
      auto_range: false,
      x_min: DEFAULT_BIN_X_MIN,
      x_max: DEFAULT_BIN_X_MAX,
      bin_count: DEFAULT_BIN_COUNT,
    };
  }
  if (op === "aggregate.bin2d_stats") {
    return {
      x_auto_range: false,
      x_min: DEFAULT_BIN_X_MIN,
      x_max: DEFAULT_BIN_X_MAX,
      x_bin_count: DEFAULT_BIN_COUNT,
      y_auto_range: false,
      y_min: -1,
      y_max: 1,
      y_bin_count: DEFAULT_BIN_COUNT,
    };
  }
  return {};
}

export function defaultInputsForOp(op: StreamDagOpId): Record<string, string> {
  const spec = STREAM_DAG_OPS[op];
  const out: Record<string, string> = {};
  for (const name of spec.inputs) {
    out[name] = "";
  }
  for (const name of spec.optionalInputs ?? []) {
    out[name] = "";
  }
  return out;
}

export function nodeKindFromOp(op: string): StreamDagOutputKind | null {
  if (!Object.prototype.hasOwnProperty.call(STREAM_DAG_OPS, op)) {
    return null;
  }
  return STREAM_DAG_OPS[op as StreamDagOpId].outputKind;
}

export function isPublishableNodeKind(
  kind: StreamDagOutputKind | null
): kind is "scalar" | "hist_agg" | "hist2d" | "trace" | "params_map" | "fit_1d" {
  return (
    kind === "scalar" ||
    kind === "hist_agg" ||
    kind === "hist2d" ||
    kind === "trace" ||
    kind === "params_map" ||
    kind === "fit_1d"
  );
}

export function cloneDagNodes(nodes: StreamDagNodeConfig[]): StreamDagNodeConfig[] {
  return nodes.map((node) => ({
    nodeId: node.nodeId,
    op: node.op,
    params: { ...node.params },
    inputs: { ...node.inputs },
  }));
}

export function cloneDagOutputs(outputs: StreamDagOutputConfig[]): StreamDagOutputConfig[] {
  return outputs.map((output) => ({
    outputId: output.outputId,
    nodeId: output.nodeId,
  }));
}

export function normalizeDagNode(raw: unknown): StreamDagNodeConfig | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const obj = raw as {
    node_id?: unknown;
    op?: unknown;
    params?: unknown;
    inputs?: unknown;
  };
  const nodeId = String(obj.node_id ?? "").trim();
  const opText = String(obj.op ?? "").trim();
  const legacyMode =
    opText === "source.stream_average"
      ? "average"
      : opText === "source.stream_sum"
        ? "sum"
        : null;
  const opRaw = (legacyMode ? "source.stream" : opText) as StreamDagOpId;
  if (!nodeId || !Object.prototype.hasOwnProperty.call(STREAM_DAG_OPS, opRaw)) {
    return null;
  }
  const paramsSrc =
    obj.params && typeof obj.params === "object"
      ? (obj.params as Record<string, unknown>)
      : {};
  const inputsSrc =
    obj.inputs && typeof obj.inputs === "object"
      ? (obj.inputs as Record<string, unknown>)
      : {};
  const params = { ...defaultParamsForOp(opRaw) };
  const spec = STREAM_DAG_OPS[opRaw];
  for (const field of spec.params) {
    if (Object.prototype.hasOwnProperty.call(paramsSrc, field.name)) {
      params[field.name] = paramsSrc[field.name];
    }
  }
  if (legacyMode && String(params.channel_mode ?? "").trim().length <= 0) {
    params.channel_mode = legacyMode;
  }
  const inputs = defaultInputsForOp(opRaw);
  for (const port of spec.inputs) {
    const rawValue = inputsSrc[port];
    if (typeof rawValue === "string" && rawValue.trim()) {
      inputs[port] = rawValue.trim();
    }
  }
  for (const port of spec.optionalInputs ?? []) {
    const rawValue = inputsSrc[port];
    if (typeof rawValue === "string" && rawValue.trim()) {
      inputs[port] = rawValue.trim();
    }
  }
  return { nodeId, op: opRaw, params, inputs };
}

export function normalizeDagOutput(raw: unknown): StreamDagOutputConfig | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const obj = raw as { outputId?: unknown; output_id?: unknown; nodeId?: unknown; node_id?: unknown };
  const outputId = String(obj.outputId ?? obj.output_id ?? "").trim();
  const nodeId = String(obj.nodeId ?? obj.node_id ?? "").trim();
  if (!outputId || !nodeId) {
    return null;
  }
  return { outputId, nodeId };
}

export function coerceDagParamValue(raw: unknown, kind: StreamDagParamField["kind"]): unknown {
  if (kind === "boolean") {
    if (typeof raw === "boolean") {
      return raw;
    }
    const text = String(raw ?? "").trim().toLowerCase();
    return text === "1" || text === "true" || text === "yes" || text === "on";
  }
  if (typeof raw === "number") {
    if (kind === "integer") {
      return Math.trunc(raw);
    }
    return raw;
  }
  const text = String(raw ?? "").trim();
  if (!text) {
    return "";
  }
  if (kind === "string") {
    return text;
  }
  const value = Number(text);
  if (!Number.isFinite(value)) {
    return text;
  }
  return kind === "integer" ? Math.trunc(value) : value;
}
