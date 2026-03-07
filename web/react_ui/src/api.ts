import {
  CommandInterceptorRoute,
  CapabilityMember,
  DeviceStatus,
  FollowerRuleStatus,
  InterlockInterceptorStatus,
  LogEntry,
  ProcessStatus,
  StreamFrameMessage,
  StreamCatalogEntry,
  TelemetrySignal,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const WS_BASE = import.meta.env.VITE_WS_BASE ?? API_BASE;

export type ApiError = {
  code?: string;
  message?: string;
  details?: unknown;
  kind?: string;
  process_id?: string;
  device_id?: string;
  action?: string;
  interceptor_id?: string;
  rule?: string;
  [key: string]: unknown;
};

export type ApiResponse<T> = {
  ok: boolean;
  result?: T;
  error?: ApiError;
};

export type GatewaySettingsInfo = {
  router_rpc: string;
  manager_pub: string;
  instance_id?: string | null;
  router_rpc_hint?: string;
  manager_pub_hint?: string;
  rpc_timeout_ms: number;
  telemetry_topics: string[];
  log_topics: string[];
  stream_topics?: string[];
  stream_analysis_topics?: string[];
  api_origin?: string;
  api_host?: string | null;
  host_ip_candidates?: string[];
  loopback_warning?: boolean;
  loopback_warning_message?: string;
};

export type InstanceRuntimeStatus = {
  instance_id: string;
  started_ts?: {
    t_wall?: number;
    t_mono?: number;
  } | null;
  manager_pid?: number | null;
  manager_reachable?: boolean;
  lock_effective_status?: string;
  lock_effective_help?: string;
  lock_status?: Record<string, unknown> | null;
  last_orphan_cleanup?: Record<string, unknown> | null;
};

function isCapabilitiesPayload(value: unknown): boolean {
  if (!value || typeof value !== "object") {
    return false;
  }
  const obj = value as Record<string, unknown>;
  return (
    typeof obj.version === "number" &&
    Array.isArray(obj.members)
  );
}

function rejectUnexpectedCapabilities<T>(
  resp: ApiResponse<T>,
  expected: string
): ApiResponse<T> {
  if (!resp.ok) {
    return resp;
  }
  if (!isCapabilitiesPayload(resp.result)) {
    return resp;
  }
  return {
    ok: false,
    error: {
      code: "unexpected_capabilities_response",
      message: `Received process capabilities instead of ${expected}.`,
    },
  };
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function normalizeFollowerRule(raw: unknown, idx: number): FollowerRuleStatus | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  const maxStepRaw = asNumber(obj.max_step_hz, Number.NaN);
  const maxStepHz = Number.isFinite(maxStepRaw) ? maxStepRaw : null;
  const telemetryMaxAgeRaw = asNumber(obj.telemetry_max_age_s, Number.NaN);
  const telemetryMaxAgeS = Number.isFinite(telemetryMaxAgeRaw)
    ? telemetryMaxAgeRaw
    : null;
  const currentFreqSignalRaw = asString(obj.current_freq_signal, "").trim();
  const currentFreqSignal = currentFreqSignalRaw.length > 0 ? currentFreqSignalRaw : null;
  const effectsRaw = Array.isArray(obj.effects) ? obj.effects : [];
  const effects = effectsRaw
    .map((effectRaw) => {
      if (!effectRaw || typeof effectRaw !== "object") {
        return null;
      }
      const effectObj = effectRaw as Record<string, unknown>;
      return {
        device_id: asString(effectObj.device_id, ""),
        action: asString(effectObj.action, ""),
        param: asString(effectObj.param, ""),
      };
    })
    .filter((effect): effect is NonNullable<typeof effect> => effect !== null);

  return {
    rule_id: asString(obj.rule_id, `r${idx}`),
    name: asString(obj.name, `rule_${idx}`),
    enabled: asBoolean(obj.enabled, true),
    device_id: asString(obj.device_id, ""),
    trigger_action: asString(obj.trigger_action, ""),
    trigger_param: asString(obj.trigger_param, "freq_hz"),
    min_freq_hz: asNumber(obj.min_freq_hz, Number.NaN),
    max_freq_hz: asNumber(obj.max_freq_hz, Number.NaN),
    max_step_hz: maxStepHz,
    current_freq_signal: currentFreqSignal,
    telemetry_max_age_s: telemetryMaxAgeS,
    csv_path: asString(obj.csv_path, ""),
    effects,
  };
}

function normalizeInterlockStatus(
  raw: unknown
): InterlockInterceptorStatus | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  const routesRaw = Array.isArray(obj.routes) ? obj.routes : [];
  const routes = routesRaw
    .map((routeRaw) => {
      if (!routeRaw || typeof routeRaw !== "object") {
        return null;
      }
      const routeObj = routeRaw as Record<string, unknown>;
      return {
        device_id: asString(routeObj.device_id, ""),
        action: asString(routeObj.action, ""),
      };
    })
    .filter((route): route is NonNullable<typeof route> => route !== null);

  const rulesRaw = Array.isArray(obj.rules) ? obj.rules : [];
  const rules = rulesRaw
    .map((ruleRaw, idx) => {
      if (!ruleRaw || typeof ruleRaw !== "object") {
        return null;
      }
      const ruleObj = ruleRaw as Record<string, unknown>;
      const telemetryRaw = Array.isArray(ruleObj.telemetry)
        ? ruleObj.telemetry
        : [];
      const telemetry = telemetryRaw
        .map((telemetryItemRaw) => {
          if (!telemetryItemRaw || typeof telemetryItemRaw !== "object") {
            return null;
          }
          const telemetryObj = telemetryItemRaw as Record<string, unknown>;
          return {
            as: asString(telemetryObj.as, ""),
            device_id: asString(telemetryObj.device_id, ""),
            signal: asString(telemetryObj.signal, ""),
            max_age_s: asNumber(telemetryObj.max_age_s, 0),
          };
        })
        .filter((item): item is NonNullable<typeof item> => item !== null);
      const matchObj =
        ruleObj.match && typeof ruleObj.match === "object"
          ? (ruleObj.match as Record<string, unknown>)
          : {};
      const onBlockObj =
        ruleObj.on_block && typeof ruleObj.on_block === "object"
          ? (ruleObj.on_block as Record<string, unknown>)
          : null;
      return {
        rule_id: asString(ruleObj.rule_id, `r${idx}`),
        name: asString(ruleObj.name, `rule_${idx}`),
        enabled: asBoolean(ruleObj.enabled, true),
        match: {
          device_id: asString(matchObj.device_id, ""),
          action: asString(matchObj.action, ""),
        },
        telemetry,
        on_block: onBlockObj
          ? {
              code: asString(onBlockObj.code, "") || null,
              message: asString(onBlockObj.message, "") || null,
            }
          : null,
        has_allow_transform: asBoolean(ruleObj.has_allow_transform, false),
      };
    })
    .filter((rule): rule is NonNullable<typeof rule> => rule !== null);

  return {
    interceptor_id: asString(obj.interceptor_id, ""),
    enabled: asBoolean(obj.enabled, true),
    source: asString(obj.source, "") || null,
    rule_count: asNumber(obj.rule_count, rules.length),
    enabled_rule_count: asNumber(
      obj.enabled_rule_count,
      rules.filter((rule) => rule.enabled).length
    ),
    routes,
    rules,
  };
}

function normalizeCommandInterceptorRoute(
  raw: unknown
): CommandInterceptorRoute | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  const processId = asString(obj.process_id, "").trim();
  const deviceId = asString(obj.device_id, "").trim();
  const action = asString(obj.action, "").trim();
  const orderRaw = asNumber(obj.order, Number.NaN);
  if (!processId || !deviceId || !action || !Number.isFinite(orderRaw)) {
    return null;
  }
  return {
    order: Math.trunc(orderRaw),
    process_id: processId,
    device_id: deviceId,
    action,
  };
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<ApiResponse<T>> {
  const resp = await fetch(`${API_BASE}${path}`, init);
  return resp.json();
}

export async function fetchDevices(): Promise<DeviceStatus[]> {
  const resp = await apiFetch<DeviceStatus[]>("/api/devices");
  if (!resp.ok || !resp.result) {
    return [];
  }
  return resp.result;
}

export type TelemetrySnapshotResult = {
  generated_ts?: { t_wall?: number; t_mono?: number };
  devices?: Record<string, Record<string, TelemetrySignal>>;
};

export async function fetchTelemetrySnapshot(): Promise<
  Record<string, Record<string, TelemetrySignal>>
> {
  const resp = await apiFetch<TelemetrySnapshotResult>("/api/snapshots/telemetry");
  if (!resp.ok || !resp.result || typeof resp.result !== "object") {
    return {};
  }
  const rawDevices =
    resp.result.devices && typeof resp.result.devices === "object"
      ? resp.result.devices
      : {};
  const out: Record<string, Record<string, TelemetrySignal>> = {};
  for (const [deviceIdRaw, signalsRaw] of Object.entries(rawDevices)) {
    const deviceId = String(deviceIdRaw ?? "").trim();
    if (!deviceId || !signalsRaw || typeof signalsRaw !== "object") {
      continue;
    }
    const signalsOut: Record<string, TelemetrySignal> = {};
    for (const [signalNameRaw, signalRaw] of Object.entries(
      signalsRaw as Record<string, unknown>
    )) {
      const signalName = String(signalNameRaw ?? "").trim();
      if (!signalName || !signalRaw || typeof signalRaw !== "object") {
        continue;
      }
      const signalObj = signalRaw as Record<string, unknown>;
      const tsRaw =
        signalObj.ts && typeof signalObj.ts === "object"
          ? (signalObj.ts as Record<string, unknown>)
          : null;
      const valueRaw = signalObj.value;
      const value =
        typeof valueRaw === "number" ||
        typeof valueRaw === "string" ||
        typeof valueRaw === "boolean" ||
        valueRaw === null
          ? valueRaw
          : null;
      signalsOut[signalName] = {
        value,
        units:
          signalObj.units === null || typeof signalObj.units === "string"
            ? (signalObj.units as string | null)
            : null,
        quality:
          signalObj.quality === null || typeof signalObj.quality === "string"
            ? (signalObj.quality as string | null)
            : null,
        ts: (() => {
          if (!tsRaw) {
            return undefined;
          }
          const tWallRaw = Number(tsRaw.t_wall);
          const tMonoRaw = Number(tsRaw.t_mono);
          const tWall = Number.isFinite(tWallRaw) ? tWallRaw : undefined;
          const tMono = Number.isFinite(tMonoRaw) ? tMonoRaw : undefined;
          if (tWall === undefined && tMono === undefined) {
            return undefined;
          }
          return { t_wall: tWall, t_mono: tMono };
        })(),
      };
    }
    out[deviceId] = signalsOut;
  }
  return out;
}

export async function fetchStreams(): Promise<StreamCatalogEntry[]> {
  const resp = await apiFetch<StreamCatalogEntry[]>("/api/streams");
  if (!resp.ok || !resp.result) {
    return [];
  }
  return resp.result;
}

export async function fetchCapabilities(deviceId: string): Promise<CapabilityMember[]> {
  const resp = await apiFetch<{ members?: CapabilityMember[] }>(
    `/api/devices/${deviceId}/capabilities`
  );
  if (!resp.ok || !resp.result) {
    return [];
  }
  return resp.result.members ?? [];
}

export async function callDevice(
  deviceId: string,
  action: string,
  params: Record<string, unknown>
) {
  return apiFetch(`/api/devices/${deviceId}/call`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, params }),
  });
}

export async function connectDevice(deviceId: string) {
  return apiFetch(`/api/devices/${deviceId}/connect`, { method: "POST" });
}

export async function startDevice(deviceId: string) {
  return apiFetch(`/api/devices/${deviceId}/start`, { method: "POST" });
}

export async function disconnectDevice(deviceId: string) {
  return apiFetch(`/api/devices/${deviceId}/disconnect`, { method: "POST" });
}

export async function restartDevice(deviceId: string, force = false) {
  return apiFetch(`/api/devices/${deviceId}/restart`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force }),
  });
}

export async function fetchProcesses(): Promise<ProcessStatus[]> {
  const resp = await apiFetch<ProcessStatus[]>("/api/processes");
  if (!resp.ok || !resp.result) {
    return [];
  }
  return resp.result;
}

export async function startProcess(processId: string) {
  return apiFetch(`/api/processes/${processId}/start`, { method: "POST" });
}

export async function stopProcess(processId: string) {
  return apiFetch(`/api/processes/${processId}/stop`, { method: "POST" });
}

export async function restartProcess(processId: string) {
  return apiFetch(`/api/processes/${processId}/restart`, { method: "POST" });
}

export async function fetchProcessCapabilities(
  processId: string
): Promise<CapabilityMember[]> {
  const resp = await apiFetch<{ members?: CapabilityMember[] }>(
    `/api/processes/${processId}/capabilities`
  );
  if (!resp.ok || !resp.result) {
    return [];
  }
  return resp.result.members ?? [];
}

export async function callProcess(
  processId: string,
  action: string,
  params: Record<string, unknown>
) {
  return apiFetch(`/api/processes/${processId}/call`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, params }),
  });
}

export async function fetchStreamOperators() {
  const resp = await apiFetch<{ operators?: unknown[] }>("/api/stream/operators");
  return rejectUnexpectedCapabilities(resp, "stream operator catalog");
}

export async function fetchStreamWorkspaceList() {
  const resp = await apiFetch<{ workspaces?: unknown[] }>("/api/stream/workspaces");
  return rejectUnexpectedCapabilities(resp, "workspace list");
}

export async function fetchStreamWorkspace(workspaceId: string) {
  const resp = await apiFetch<{
    workspace?: Record<string, unknown>;
    raw?: Record<string, unknown>;
  }>(`/api/stream/workspaces/${encodeURIComponent(workspaceId)}`);
  return rejectUnexpectedCapabilities(resp, `workspace ${workspaceId}`);
}

export async function fetchStreamWorkspaceSnapshot(
  workspaceId: string,
  opts?: {
    kinds?: string[];
    outputIds?: string[];
    maxTracePoints?: number | null;
  }
) {
  const params = new URLSearchParams();
  if (opts?.kinds && opts.kinds.length > 0) {
    params.set("kinds", opts.kinds.join(","));
  }
  if (opts?.outputIds && opts.outputIds.length > 0) {
    params.set("output_ids", opts.outputIds.join(","));
  }
  if (
    typeof opts?.maxTracePoints === "number" &&
    Number.isFinite(opts.maxTracePoints)
  ) {
    params.set("max_trace_points", String(Math.max(1, Math.trunc(opts.maxTracePoints))));
  }
  const query = params.toString();
  const path = `/api/stream/workspaces/${encodeURIComponent(workspaceId)}/snapshot${
    query ? `?${query}` : ""
  }`;
  const resp = await apiFetch<Record<string, unknown>>(path);
  return rejectUnexpectedCapabilities(resp, `workspace.snapshot(${workspaceId})`);
}

export async function fetchRawStreamSnapshot(opts: {
  deviceId: string;
  stream: string;
  channelIndex: number;
  traceDecimator: string;
  traceMaxPoints: number;
  traceMaxFps: number;
  rollingWindow: number;
  averageMode: string;
}): Promise<StreamFrameMessage | null> {
  const params = new URLSearchParams();
  params.set("device_id", opts.deviceId);
  params.set("stream", opts.stream);
  params.set("channel_index", String(opts.channelIndex));
  params.set("trace_decimator", opts.traceDecimator);
  params.set("trace_max_points", String(opts.traceMaxPoints));
  params.set("trace_max_fps", String(opts.traceMaxFps));
  params.set("rolling_window", String(opts.rollingWindow));
  params.set("trace_average_mode", opts.averageMode);
  const resp = await apiFetch<StreamFrameMessage | null>(
    `/api/streams/raw_snapshot?${params.toString()}`
  );
  if (!resp.ok || !resp.result || typeof resp.result !== "object") {
    return null;
  }
  return resp.result as StreamFrameMessage;
}

export async function putStreamWorkspace(
  workspaceId: string,
  workspace: Record<string, unknown>,
  expectedRevision?: number | null
) {
  const payload: Record<string, unknown> = { workspace };
  if (typeof expectedRevision === "number" && Number.isFinite(expectedRevision)) {
    payload.expected_revision = Math.max(0, Math.trunc(expectedRevision));
  }
  const resp = await apiFetch(`/api/stream/workspaces/${encodeURIComponent(workspaceId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return rejectUnexpectedCapabilities(resp, `workspace.put(${workspaceId})`);
}

export async function validateStreamWorkspace(
  workspaceId: string,
  workspace: Record<string, unknown>
) {
  const resp = await apiFetch(`/api/stream/workspaces/${encodeURIComponent(workspaceId)}/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace }),
  });
  return rejectUnexpectedCapabilities(resp, `workspace.validate(${workspaceId})`);
}

export async function resetStreamWorkspace(
  workspaceId: string,
  nodeId?: string | null
) {
  const resp = await apiFetch(`/api/stream/workspaces/${encodeURIComponent(workspaceId)}/reset`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...(nodeId ? { node_id: nodeId } : {}),
    }),
  });
  return rejectUnexpectedCapabilities(resp, `workspace.reset(${workspaceId})`);
}

export async function deleteStreamWorkspace(
  workspaceId: string,
  expectedRevision?: number | null
) {
  const query =
    typeof expectedRevision === "number" && Number.isFinite(expectedRevision)
      ? `?expected_revision=${encodeURIComponent(String(Math.max(0, Math.trunc(expectedRevision))))}`
      : "";
  const resp = await apiFetch(`/api/stream/workspaces/${encodeURIComponent(workspaceId)}${query}`, {
    method: "DELETE",
  });
  return rejectUnexpectedCapabilities(resp, `workspace.delete(${workspaceId})`);
}

export async function fetchStreamWorkspaceStoreStatus() {
  const resp = await apiFetch<Record<string, unknown>>("/api/stream/workspace_store/status");
  return rejectUnexpectedCapabilities(resp, "workspace_store.status");
}

export async function saveStreamWorkspaceStore(path?: string | null) {
  const payload =
    typeof path === "string" && path.trim().length > 0
      ? { path: path.trim() }
      : {};
  const resp = await apiFetch<Record<string, unknown>>("/api/stream/workspace_store/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return rejectUnexpectedCapabilities(resp, "workspace_store.save");
}

export async function reloadStreamWorkspaceStore(path?: string | null) {
  const payload =
    typeof path === "string" && path.trim().length > 0
      ? { path: path.trim() }
      : {};
  const resp = await apiFetch<Record<string, unknown>>("/api/stream/workspace_store/reload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return rejectUnexpectedCapabilities(resp, "workspace_store.reload");
}

export async function fetchFollowerRules(
  processId: string
): Promise<FollowerRuleStatus[]> {
  const resp = await callProcess(processId, "follower.rules", {});
  if (!resp.ok || !resp.result || typeof resp.result !== "object") {
    return [];
  }
  const rulesRaw = (resp.result as { rules?: unknown }).rules;
  if (!Array.isArray(rulesRaw)) {
    return [];
  }
  return rulesRaw
    .map((rule, idx) => normalizeFollowerRule(rule, idx))
    .filter((rule): rule is NonNullable<typeof rule> => rule !== null);
}

export async function setFollowerRuleEnabled(
  processId: string,
  ruleId: string,
  enabled: boolean
) {
  return callProcess(
    processId,
    enabled ? "follower.enable_rule" : "follower.disable_rule",
    { rule_id: ruleId }
  );
}

export async function fetchInterlockStatus(
  processId: string
): Promise<InterlockInterceptorStatus[]> {
  const resp = await callProcess(processId, "interlock.status", {});
  if (!resp.ok || !resp.result || typeof resp.result !== "object") {
    return [];
  }
  const interceptorsRaw = (resp.result as { interceptors?: unknown }).interceptors;
  if (!Array.isArray(interceptorsRaw)) {
    return [];
  }
  return interceptorsRaw
    .map((interceptor) => normalizeInterlockStatus(interceptor))
    .filter(
      (interceptor): interceptor is NonNullable<typeof interceptor> =>
      interceptor !== null
    );
}

export async function fetchCommandInterceptorRoutes(): Promise<
  CommandInterceptorRoute[]
> {
  const resp = await apiFetch<{ routes?: unknown[] }>(
    "/api/interlocks/interceptor_routes"
  );
  if (!resp.ok || !resp.result) {
    return [];
  }
  const routesRaw = Array.isArray(resp.result.routes) ? resp.result.routes : [];
  return routesRaw
    .map((route) => normalizeCommandInterceptorRoute(route))
    .filter((route): route is NonNullable<typeof route> => route !== null)
    .sort((a, b) => a.order - b.order);
}

export async function setInterlockRuleEnabled(
  processId: string,
  interceptorId: string,
  ruleId: string,
  enabled: boolean
) {
  return callProcess(
    processId,
    enabled ? "interlock.enable_rule" : "interlock.disable_rule",
    { interceptor_id: interceptorId, rule_id: ruleId }
  );
}

export async function fetchLogTail(params: Record<string, unknown>) {
  return apiFetch<{
    entries?: LogEntry[];
    count?: number;
    total_matched?: number;
    limit?: number;
    latest_t_mono?: number | null;
  }>("/api/logs/tail", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ params }),
  });
}

export async function fetchGatewaySettings(): Promise<GatewaySettingsInfo | null> {
  const resp = await apiFetch<GatewaySettingsInfo>("/api/settings");
  if (!resp.ok || !resp.result) {
    return null;
  }
  return resp.result;
}

export async function fetchInstanceRuntimeStatus(): Promise<InstanceRuntimeStatus | null> {
  const resp = await apiFetch<InstanceRuntimeStatus>("/api/instance/runtime");
  if (!resp.ok || !resp.result) {
    return null;
  }
  return resp.result;
}

export async function cleanupInstanceOrphans(params?: {
  dry_run?: boolean;
  stale_only?: boolean;
  timeout_s?: number;
}): Promise<ApiResponse<Record<string, unknown>>> {
  return apiFetch<Record<string, unknown>>("/api/instance/cleanup_orphans", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dry_run: params?.dry_run ?? true,
      stale_only: params?.stale_only ?? true,
      timeout_s: params?.timeout_s ?? 2.0,
    }),
  });
}

export function buildWsUrl(path: string) {
  if (WS_BASE) {
    const url = new URL(WS_BASE);
    const scheme = url.protocol === "https:" ? "wss" : "ws";
    return `${scheme}://${url.host}${path}`;
  }
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${path}`;
}
