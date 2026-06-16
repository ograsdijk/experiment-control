import type { UncertaintyMode } from "../../components/StreamBinStatsPanel";
import type { StreamCatalogEntry } from "../../types";
import {
  defaultInputsForOp,
  defaultParamsForOp,
  nodeKindFromOp,
  normalizeDagNode,
  normalizeDagOutput,
  STREAM_DAG_OPS,
} from "./dag";
import type {
  StreamAnalysisSettings,
  StreamAnalysisWorkspaceConfig,
  StreamBinStatsSettings,
  StreamDagNodeConfig,
  StreamDagOutputConfig,
  StreamDagOutputKind,
  StreamWorkspaceStoreStatus,
  StreamWorkspaceSummary,
  StreamTarget,
} from "./types";
import {
  DEFAULT_BIN_COUNT,
  DEFAULT_BIN_OUTPUT_ID,
  DEFAULT_BIN_X_MAX,
  DEFAULT_BIN_X_MIN,
  DEFAULT_INTEGRAL_OUTPUT_ID,
  DEFAULT_STREAM_CONTEXT_FIELD,
  normalizeNonNegativeInt,
  normalizePositiveInt,
  normalizeShape,
  streamTargetKey,
} from "./utils";

export function defaultGraphForLegacyWorkspace(
  workspace: Pick<
    StreamAnalysisWorkspaceConfig,
    "stream" | "channelIndex" | "analysis" | "binStats"
  >
): { nodes: StreamDagNodeConfig[]; outputs: StreamDagOutputConfig[] } {
  const nodes: StreamDagNodeConfig[] = [
    {
      nodeId: "src",
      op: "source.stream",
      params: {
        device_id: workspace.stream?.deviceId ?? "",
        stream: workspace.stream?.stream ?? "",
        channel_mode: "single",
        channel_index: workspace.channelIndex ?? 0,
        channel_indices: String(workspace.channelIndex ?? 0),
      },
      inputs: {},
    },
  ];
  let traceNodeId = "src";
  if (workspace.analysis.backgroundEnabled) {
    nodes.push({
      nodeId: "bg",
      op: "trace.subtract_background",
      params: {
        bg_start_idx: workspace.analysis.backgroundStartIdx,
        bg_stop_idx: workspace.analysis.backgroundStopIdx,
      },
      inputs: { trace: traceNodeId },
    });
    traceNodeId = "bg";
  }
  nodes.push({
    nodeId: "crop",
    op: "trace.crop",
    params: {
      start_idx: workspace.analysis.traceStartIdx,
      ...(workspace.analysis.traceStopIdx === null
        ? {}
        : { stop_idx: workspace.analysis.traceStopIdx }),
    },
    inputs: { trace: traceNodeId },
  });
  traceNodeId = "crop";
  nodes.push({
    nodeId: "integral",
    op: "trace.integrate",
    params: {},
    inputs: { trace: traceNodeId },
  });
  nodes.push({
    nodeId: "ctx_x",
    op: "source.context_field",
    params: { field: workspace.binStats.contextField },
    inputs: {},
  });
  nodes.push({
    nodeId: "bin",
    op: "aggregate.bin_stats",
    params: {
      auto_range: workspace.binStats.autoRange,
      x_min: workspace.binStats.xMin,
      x_max: workspace.binStats.xMax,
      bin_count: workspace.binStats.binCount,
    },
    inputs: { x: "ctx_x", y: "integral" },
  });
  return {
    nodes,
    outputs: [
      { outputId: DEFAULT_INTEGRAL_OUTPUT_ID, nodeId: "integral" },
      { outputId: DEFAULT_BIN_OUTPUT_ID, nodeId: "bin" },
    ],
  };
}

export function workspaceNodeMap(
  workspace: StreamAnalysisWorkspaceConfig
): Map<string, StreamDagNodeConfig> {
  return new Map(workspace.graphNodes.map((node) => [node.nodeId, node]));
}

export function workspaceOutputKind(
  workspace: StreamAnalysisWorkspaceConfig | null,
  outputId: string
): StreamDagOutputKind | null {
  if (!workspace) {
    return null;
  }
  const output = workspace.publishOutputs.find((item) => item.outputId === outputId);
  if (!output) {
    return null;
  }
  const node = workspace.graphNodes.find((item) => item.nodeId === output.nodeId);
  if (!node) {
    return null;
  }
  return nodeKindFromOp(node.op);
}

export function workspaceOutputOptionsByKind(
  workspace: StreamAnalysisWorkspaceConfig | null,
  kind: "scalar" | "hist_agg" | "hist2d" | "trace" | "params_map" | "fit_1d"
): Array<{ value: string; label: string }> {
  if (!workspace) {
    return [];
  }
  const nodeById = workspaceNodeMap(workspace);
  const out: Array<{ value: string; label: string }> = [];
  for (const output of workspace.publishOutputs) {
    const node = nodeById.get(output.nodeId);
    if (!node) {
      continue;
    }
    const nodeKind = nodeKindFromOp(node.op);
    if (nodeKind !== kind) {
      continue;
    }
    out.push({
      value: output.outputId,
      label: `${output.outputId} <- ${output.nodeId} (${node.op})`,
    });
  }
  return out.sort((a, b) => a.label.localeCompare(b.label));
}

export function workspaceXAxisLabel(
  workspace: StreamAnalysisWorkspaceConfig | null,
  outputId: string | null
): string {
  const labelForScalarSource = (
    node: StreamDagNodeConfig | undefined,
    fallback: string
  ): string => {
    if (!node) {
      return fallback;
    }
    if (node.op === "source.context_field") {
      const field = String(node.params.field ?? "").trim();
      return field || fallback;
    }
    if (node.op === "source.telemetry_nearest") {
      const deviceId = String(node.params.device_id ?? "").trim();
      const signal = String(node.params.signal ?? "").trim();
      if (deviceId || signal) {
        return `${deviceId || "?"}.${signal || "?"}`;
      }
      return fallback;
    }
    return fallback;
  };

  if (!workspace || !outputId) {
    return DEFAULT_STREAM_CONTEXT_FIELD;
  }
  const output = workspace.publishOutputs.find((item) => item.outputId === outputId);
  if (!output) {
    return DEFAULT_STREAM_CONTEXT_FIELD;
  }
  const nodeById = workspaceNodeMap(workspace);
  const outNode = nodeById.get(output.nodeId);
  if (!outNode || outNode.op !== "aggregate.bin_stats") {
    return DEFAULT_STREAM_CONTEXT_FIELD;
  }
  const xSourceId = outNode.inputs.x;
  if (!xSourceId) {
    return DEFAULT_STREAM_CONTEXT_FIELD;
  }
  const xNode = nodeById.get(xSourceId);
  return labelForScalarSource(xNode, DEFAULT_STREAM_CONTEXT_FIELD);
}

export function workspaceBin2dAxisLabel(
  workspace: StreamAnalysisWorkspaceConfig | null,
  outputId: string | null,
  axis: "x" | "y"
): string {
  const labelForScalarSource = (
    node: StreamDagNodeConfig | undefined,
    fallback: string
  ): string => {
    if (!node) {
      return fallback;
    }
    if (node.op === "source.context_field") {
      const field = String(node.params.field ?? "").trim();
      return field || fallback;
    }
    if (node.op === "source.telemetry_nearest") {
      const deviceId = String(node.params.device_id ?? "").trim();
      const signal = String(node.params.signal ?? "").trim();
      if (deviceId || signal) {
        return `${deviceId || "?"}.${signal || "?"}`;
      }
      return fallback;
    }
    return fallback;
  };

  const fallback = axis === "x" ? DEFAULT_STREAM_CONTEXT_FIELD : "context_y";
  if (!workspace || !outputId) {
    return fallback;
  }
  const output = workspace.publishOutputs.find((item) => item.outputId === outputId);
  if (!output) {
    return fallback;
  }
  const nodeById = workspaceNodeMap(workspace);
  const outNode = nodeById.get(output.nodeId);
  if (!outNode || outNode.op !== "aggregate.bin2d_stats") {
    return fallback;
  }
  const sourceId = String(outNode.inputs[axis] ?? "").trim();
  if (!sourceId) {
    return fallback;
  }
  const srcNode = nodeById.get(sourceId);
  return labelForScalarSource(srcNode, fallback);
}

export function workspaceStreamFromGraphNodes(
  nodes: StreamDagNodeConfig[],
  streamCatalogByKey: Map<string, StreamCatalogEntry>
): { stream: StreamTarget | null; channelIndex: number } {
  const src = nodes.find((node) => node.op === "source.stream") ?? null;
  if (!src) {
    return { stream: null, channelIndex: 0 };
  }
  const deviceId = String(src.params.device_id ?? "").trim();
  const streamName = String(src.params.stream ?? "").trim();
  const channelIndicesText = String(src.params.channel_indices ?? "").trim();
  const firstFromList = channelIndicesText
    .split(/[\s,;]+/)
    .map((item) => Number(item))
    .find((value) => Number.isFinite(value) && value >= 0);
  const channelIndexRaw =
    firstFromList ??
    Number(src.params.channel_index);
  const channelIndex =
    Number.isFinite(channelIndexRaw) && channelIndexRaw >= 0
      ? Math.trunc(channelIndexRaw)
      : 0;
  if (!deviceId || !streamName) {
    return { stream: null, channelIndex };
  }
  const key = streamTargetKey(deviceId, streamName);
  const meta = streamCatalogByKey.get(key);
  return {
    stream: {
      deviceId,
      stream: streamName,
      units: typeof meta?.units === "string" ? meta.units : undefined,
      shape: normalizeShape(meta?.shape),
    },
    channelIndex,
  };
}

export function defaultOutputForKind(
  workspace: StreamAnalysisWorkspaceConfig | null,
  kind: "scalar" | "hist_agg" | "hist2d" | "trace" | "params_map" | "fit_1d"
): string | null {
  const options = workspaceOutputOptionsByKind(workspace, kind);
  return options[0]?.value ?? null;
}

export function defaultStreamAnalysisSettings(): StreamAnalysisSettings {
  return {
    traceStartIdx: 0,
    traceStopIdx: null,
    backgroundEnabled: false,
    backgroundStartIdx: 0,
    backgroundStopIdx: 0,
  };
}

export function normalizeStreamAnalysisSettings(raw: unknown): StreamAnalysisSettings {
  const defaults = defaultStreamAnalysisSettings();
  if (!raw || typeof raw !== "object") {
    return defaults;
  }
  const obj = raw as {
    traceStartIdx?: unknown;
    traceStopIdx?: unknown;
    backgroundEnabled?: unknown;
    backgroundStartIdx?: unknown;
    backgroundStopIdx?: unknown;
  };
  const traceStartIdx = normalizeNonNegativeInt(obj.traceStartIdx, defaults.traceStartIdx);
  const traceStopRaw = Number(obj.traceStopIdx);
  const traceStopIdx =
    Number.isFinite(traceStopRaw) && traceStopRaw > 0
      ? Math.trunc(traceStopRaw)
      : null;
  const backgroundEnabled = obj.backgroundEnabled === true;
  const backgroundStartIdx = normalizeNonNegativeInt(
    obj.backgroundStartIdx,
    defaults.backgroundStartIdx
  );
  const backgroundStopIdx = normalizeNonNegativeInt(
    obj.backgroundStopIdx,
    defaults.backgroundStopIdx
  );
  return {
    traceStartIdx,
    traceStopIdx,
    backgroundEnabled,
    backgroundStartIdx,
    backgroundStopIdx,
  };
}

export function normalizeUncertaintyMode(raw: unknown): UncertaintyMode {
  if (raw === "ci95") {
    return "sem";
  }
  if (raw === "std" || raw === "sem") {
    return raw;
  }
  return "sem";
}

export function defaultStreamBinStatsSettings(): StreamBinStatsSettings {
  return {
    contextField: DEFAULT_STREAM_CONTEXT_FIELD,
    xMin: DEFAULT_BIN_X_MIN,
    xMax: DEFAULT_BIN_X_MAX,
    binCount: DEFAULT_BIN_COUNT,
    autoRange: true,
  };
}

export function normalizeStreamBinStatsSettings(raw: unknown): StreamBinStatsSettings {
  const defaults = defaultStreamBinStatsSettings();
  if (!raw || typeof raw !== "object") {
    return defaults;
  }
  const obj = raw as {
    contextField?: unknown;
    xMin?: unknown;
    xMax?: unknown;
    binCount?: unknown;
    autoRange?: unknown;
  };
  const contextFieldRaw =
    typeof obj.contextField === "string" ? obj.contextField.trim() : "";
  const xMinRaw = Number(obj.xMin);
  const xMaxRaw = Number(obj.xMax);
  let xMin = Number.isFinite(xMinRaw) ? xMinRaw : defaults.xMin;
  let xMax = Number.isFinite(xMaxRaw) ? xMaxRaw : defaults.xMax;
  if (xMax <= xMin) {
    const center = Number.isFinite(xMin) ? xMin : defaults.xMin;
    xMin = center;
    xMax = center + 1;
  }
  const binCount = normalizePositiveInt(obj.binCount, defaults.binCount);
  const autoRange = obj.autoRange !== false;
  return {
    contextField: contextFieldRaw || defaults.contextField,
    xMin,
    xMax,
    binCount,
    autoRange,
  };
}

export function defaultStreamWorkspaceName(workspaceId: string): string {
  const match = /(?:workspace|ws)-(\d+)/i.exec(workspaceId);
  if (match) {
    return `Workspace ${match[1]}`;
  }
  return workspaceId;
}

export function defaultStreamAnalysisWorkspaceConfig(
  workspaceId: string
): StreamAnalysisWorkspaceConfig {
  const base: StreamAnalysisWorkspaceConfig = {
    workspaceId,
    name: defaultStreamWorkspaceName(workspaceId),
    stream: null,
    channelIndex: 0,
    analysis: defaultStreamAnalysisSettings(),
    binStats: defaultStreamBinStatsSettings(),
    graphNodes: [],
    publishOutputs: [],
    enabled: true,
  };
  const graph = defaultGraphForLegacyWorkspace(base);
  base.graphNodes = graph.nodes;
  base.publishOutputs = graph.outputs;
  return base;
}

export function streamWorkspaceSort(
  a: StreamAnalysisWorkspaceConfig,
  b: StreamAnalysisWorkspaceConfig
): number {
  const nameCmp = a.name.localeCompare(b.name);
  if (nameCmp !== 0) {
    return nameCmp;
  }
  return a.workspaceId.localeCompare(b.workspaceId);
}

export function nextWorkspaceCounter(
  workspaces: Record<string, StreamAnalysisWorkspaceConfig>
): number {
  let maxId = 0;
  for (const workspaceId of Object.keys(workspaces)) {
    const match = /(?:workspace|ws)-(\d+)/i.exec(workspaceId);
    if (!match) {
      continue;
    }
    const value = Number(match[1]);
    if (Number.isFinite(value)) {
      maxId = Math.max(maxId, Math.trunc(value));
    }
  }
  return maxId + 1;
}

export function normalizeStreamWorkspaceRecord(
  raw: unknown
): Record<string, StreamAnalysisWorkspaceConfig> {
  if (!raw || typeof raw !== "object") {
    return {};
  }
  const out: Record<string, StreamAnalysisWorkspaceConfig> = {};
  for (const [workspaceIdRaw, value] of Object.entries(raw as Record<string, unknown>)) {
    const workspaceId = String(workspaceIdRaw ?? "").trim();
    if (!workspaceId || !value || typeof value !== "object") {
      continue;
    }
    const obj = value as {
      workspaceId?: unknown;
      name?: unknown;
      stream?: unknown;
      channelIndex?: unknown;
      analysis?: unknown;
      binStats?: unknown;
      graphNodes?: unknown;
      publishOutputs?: unknown;
      graph?: unknown;
      publish?: unknown;
      enabled?: unknown;
    };
    const streamRaw =
      obj.stream && typeof obj.stream === "object"
        ? (obj.stream as {
            deviceId?: unknown;
            stream?: unknown;
            units?: unknown;
            shape?: unknown;
          })
        : null;
    const streamDeviceId =
      streamRaw && typeof streamRaw.deviceId === "string"
        ? streamRaw.deviceId
        : "";
    const streamName =
      streamRaw && typeof streamRaw.stream === "string" ? streamRaw.stream : "";
    const streamTarget =
      streamDeviceId && streamName
        ? {
            deviceId: streamDeviceId,
            stream: streamName,
            units:
              streamRaw && typeof streamRaw.units === "string"
                ? streamRaw.units
                : undefined,
            shape: normalizeShape(streamRaw?.shape),
          }
        : null;
    const channelIndexRaw = Number(obj.channelIndex);
    const normalized: StreamAnalysisWorkspaceConfig = {
      workspaceId:
        typeof obj.workspaceId === "string" && obj.workspaceId.trim().length > 0
          ? obj.workspaceId.trim()
          : workspaceId,
      name:
        typeof obj.name === "string" && obj.name.trim().length > 0
          ? obj.name.trim()
          : defaultStreamWorkspaceName(workspaceId),
      stream: streamTarget,
      channelIndex:
        Number.isFinite(channelIndexRaw) && channelIndexRaw >= 0
          ? Math.trunc(channelIndexRaw)
          : 0,
      analysis: normalizeStreamAnalysisSettings(obj.analysis),
      binStats: normalizeStreamBinStatsSettings(obj.binStats),
      graphNodes: [],
      publishOutputs: [],
      enabled: obj.enabled !== false,
    };
    const graphNodesRaw =
      obj.graphNodes ??
      (obj.graph && typeof obj.graph === "object"
        ? (obj.graph as { nodes?: unknown }).nodes
        : null);
    const publishOutputsRaw =
      obj.publishOutputs ??
      (obj.publish && typeof obj.publish === "object"
        ? (obj.publish as { outputs?: unknown }).outputs
        : null);
    const graphNodes = Array.isArray(graphNodesRaw)
      ? graphNodesRaw
          .map((entry) => normalizeDagNode(entry))
          .filter((entry): entry is StreamDagNodeConfig => entry !== null)
      : [];
    const publishOutputs = Array.isArray(publishOutputsRaw)
      ? publishOutputsRaw
          .map((entry) => normalizeDagOutput(entry))
          .filter((entry): entry is StreamDagOutputConfig => entry !== null)
      : [];
    if (graphNodes.length > 0) {
      normalized.graphNodes = graphNodes;
    } else {
      normalized.graphNodes = defaultGraphForLegacyWorkspace(normalized).nodes;
    }
    if (publishOutputs.length > 0) {
      normalized.publishOutputs = publishOutputs;
    } else {
      normalized.publishOutputs = defaultGraphForLegacyWorkspace(normalized).outputs;
    }
    const derived = workspaceStreamFromGraphNodes(normalized.graphNodes, new Map());
    if (derived.stream) {
      normalized.stream = {
        ...derived.stream,
        units: normalized.stream?.units ?? derived.stream.units,
        shape:
          normalized.stream?.shape && normalized.stream.shape.length > 0
            ? normalized.stream.shape
            : derived.stream.shape,
      };
    }
    normalized.channelIndex = derived.channelIndex;
    out[workspaceId] = normalized;
  }
  return out;
}

export function normalizeWorkspaceSummaries(raw: unknown): StreamWorkspaceSummary[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const out: StreamWorkspaceSummary[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") {
      continue;
    }
    const obj = entry as Record<string, unknown>;
    const workspaceId = String(obj.workspace_id ?? obj.workspaceId ?? "").trim();
    if (!workspaceId) {
      continue;
    }
    const revisionRaw = Number(obj.revision);
    const revision =
      Number.isFinite(revisionRaw) && revisionRaw >= 0
        ? Math.trunc(revisionRaw)
        : 0;
    const etag = typeof obj.etag === "string" ? obj.etag : null;
    out.push({ workspaceId, revision, etag });
  }
  out.sort((a, b) => a.workspaceId.localeCompare(b.workspaceId));
  return out;
}

export function normalizeWorkspaceStoreStatus(raw: unknown): StreamWorkspaceStoreStatus {
  if (!raw || typeof raw !== "object") {
    return {
      path: null,
      exists: false,
      dirty: false,
      workspaceCount: 0,
      lastLoadedWallS: null,
      lastSavedWallS: null,
      lastError: null,
    };
  }
  const obj = raw as Record<string, unknown>;
  const workspaceCountRaw = Number(obj.workspace_count ?? obj.workspaceCount ?? 0);
  const lastLoadedRaw = Number(obj.last_loaded_wall_s ?? obj.lastLoadedWallS);
  const lastSavedRaw = Number(obj.last_saved_wall_s ?? obj.lastSavedWallS);
  return {
    path:
      typeof obj.path === "string" && obj.path.trim().length > 0
        ? obj.path.trim()
        : null,
    exists: obj.exists === true,
    dirty: obj.dirty === true,
    workspaceCount:
      Number.isFinite(workspaceCountRaw) && workspaceCountRaw >= 0
        ? Math.trunc(workspaceCountRaw)
        : 0,
    lastLoadedWallS: Number.isFinite(lastLoadedRaw) ? lastLoadedRaw : null,
    lastSavedWallS: Number.isFinite(lastSavedRaw) ? lastSavedRaw : null,
    lastError:
      typeof obj.last_error === "string" && obj.last_error.trim().length > 0
        ? obj.last_error
        : typeof obj.lastError === "string" && obj.lastError.trim().length > 0
          ? obj.lastError
          : null,
  };
}

export function defaultNodeParamsAndInputs(op: StreamDagNodeConfig["op"]): {
  params: Record<string, unknown>;
  inputs: Record<string, string>;
} {
  return {
    params: defaultParamsForOp(op),
    inputs: defaultInputsForOp(op),
  };
}

export function hasKnownDagOp(op: unknown): op is StreamDagNodeConfig["op"] {
  const opRaw = String(op ?? "").trim();
  return Boolean(opRaw) && Object.prototype.hasOwnProperty.call(STREAM_DAG_OPS, opRaw);
}
