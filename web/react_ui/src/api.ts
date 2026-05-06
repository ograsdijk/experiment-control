import {
  CommandInterceptorRoute,
  CapabilityMember,
  DeviceStatus,
  FollowerRuleStatus,
  InterlockInterceptorStatus,
  WatchdogStatus,
  LogEntry,
  ProcessStatus,
  StateMachineGraph,
  StateMachineHistoryEntry,
  StateMachineStatus,
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

export type CommandJournalStatusResult = {
  enabled: boolean;
  path?: string | null;
  start_error?: string | null;
  queue_depth?: number;
  queue_max?: number;
  batch_size?: number;
  flush_interval_ms?: number;
  retention?: {
    max_rows?: number | null;
    max_age_days?: number | null;
  } | null;
  written?: number;
  dropped?: number;
  write_errors?: number;
  pruned_rows?: number;
  last_error?: string | null;
  thread_alive?: boolean;
};

export type CommandJournalTailResult = {
  entries?: unknown[];
  count?: number;
  total_matched?: number;
  limit?: number;
  latest_id?: number | null;
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
      const hasCondition = Object.prototype.hasOwnProperty.call(
        ruleObj,
        "condition"
      );
      return {
        rule_id: asString(ruleObj.rule_id, `r${idx}`),
        name: asString(ruleObj.name, `rule_${idx}`),
        enabled: asBoolean(ruleObj.enabled, true),
        match: {
          device_id: asString(matchObj.device_id, ""),
          action: asString(matchObj.action, ""),
        },
        telemetry,
        condition: hasCondition ? ruleObj.condition : null,
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

function normalizeWatchdogStatus(raw: unknown): WatchdogStatus | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  const rulesRaw = Array.isArray(obj.rules) ? obj.rules : [];
  const rules = rulesRaw
    .map((ruleRaw) => {
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
      const actionsRaw = Array.isArray(ruleObj.actions) ? ruleObj.actions : [];
      const actions = actionsRaw
        .map((actionRaw) => {
          if (!actionRaw || typeof actionRaw !== "object") {
            return null;
          }
          const actionObj = actionRaw as Record<string, unknown>;
          const paramsRaw = actionObj.params;
          const params =
            paramsRaw && typeof paramsRaw === "object" && !Array.isArray(paramsRaw)
              ? (paramsRaw as Record<string, unknown>)
              : {};
          const timeoutRaw = asNumber(actionObj.timeout_s, Number.NaN);
          const retriesRaw = asNumber(actionObj.retries, Number.NaN);
          return {
            device_id: asString(actionObj.device_id, ""),
            action: asString(actionObj.action, ""),
            params,
            timeout_s: Number.isFinite(timeoutRaw) ? timeoutRaw : null,
            retries: Number.isFinite(retriesRaw) ? Math.trunc(retriesRaw) : 0,
          };
        })
        .filter((item): item is NonNullable<typeof item> => item !== null);
      const stableSinceRaw = asNumber(ruleObj.stable_since_mono, Number.NaN);
      const stableSinceMono = Number.isFinite(stableSinceRaw)
        ? stableSinceRaw
        : null;
      const lastTriggerRaw = asNumber(ruleObj.last_trigger_mono, Number.NaN);
      const lastTriggerMono = Number.isFinite(lastTriggerRaw)
        ? lastTriggerRaw
        : null;
      const lastEvaluatedRaw = asNumber(ruleObj.last_evaluated_mono, Number.NaN);
      const lastEvaluatedMono = Number.isFinite(lastEvaluatedRaw)
        ? lastEvaluatedRaw
        : null;
      const snapshotRaw = ruleObj.snapshot;
      const snapshot =
        snapshotRaw && typeof snapshotRaw === "object" && !Array.isArray(snapshotRaw)
          ? (snapshotRaw as Record<string, unknown>)
          : null;
      return {
        name: asString(ruleObj.name, ""),
        severity: asString(ruleObj.severity, "info"),
        message: asString(ruleObj.message, "") || null,
        condition: Object.prototype.hasOwnProperty.call(ruleObj, "condition")
          ? ruleObj.condition
          : null,
        telemetry,
        actions,
        stable_for_s: Number.isFinite(asNumber(ruleObj.stable_for_s, Number.NaN))
          ? asNumber(ruleObj.stable_for_s, 0)
          : null,
        cooldown_s: Number.isFinite(asNumber(ruleObj.cooldown_s, Number.NaN))
          ? asNumber(ruleObj.cooldown_s, 0)
          : null,
        latch: asBoolean(ruleObj.latch, false),
        on_unknown: asString(ruleObj.on_unknown, "") || null,
        latched: asBoolean(ruleObj.latched, false),
        alarm: Object.prototype.hasOwnProperty.call(ruleObj, "alarm")
          ? asBoolean(ruleObj.alarm, false)
          : null,
        unknown: Object.prototype.hasOwnProperty.call(ruleObj, "unknown")
          ? asBoolean(ruleObj.unknown, false)
          : null,
        snapshot,
        last_evaluated_mono: lastEvaluatedMono,
        stable_since_mono: stableSinceMono,
        last_trigger_mono: lastTriggerMono,
      };
    })
    .filter((rule): rule is NonNullable<typeof rule> => rule !== null);
  return {
    watchdog_id: asString(obj.watchdog_id, ""),
    enabled: asBoolean(obj.enabled, true),
    rules,
  };
}

function asOptionalRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function normalizeTimestamp(value: unknown): { t_wall?: number; t_mono?: number } | null {
  const obj = asOptionalRecord(value);
  if (!obj) {
    return null;
  }
  const tWallRaw = Number(obj.t_wall);
  const tMonoRaw = Number(obj.t_mono);
  const tWall = Number.isFinite(tWallRaw) ? tWallRaw : undefined;
  const tMono = Number.isFinite(tMonoRaw) ? tMonoRaw : undefined;
  if (tWall == null && tMono == null) {
    return null;
  }
  return { t_wall: tWall, t_mono: tMono };
}

function normalizeStateMachineStatus(raw: unknown): StateMachineStatus | null {
  const obj = asOptionalRecord(raw);
  if (!obj) {
    return null;
  }
  const state = asString(obj.state, "").trim();
  if (!state) {
    return null;
  }
  const allowedRaw = Array.isArray(obj.allowed_next_states)
    ? obj.allowed_next_states
    : [];
  const allowedNextStates = allowedRaw
    .map((item) => String(item ?? "").trim())
    .filter((item) => item.length > 0);
  const lastTransitionObj = asOptionalRecord(obj.last_transition);
  const stateAgeRaw = Number(obj.state_age_s);
  const statusAgeRaw = Number(obj.status_age_s);
  return {
    state,
    state_since: normalizeTimestamp(obj.state_since),
    state_age_s: Number.isFinite(stateAgeRaw) ? stateAgeRaw : null,
    last_error: asString(obj.last_error, "") || null,
    last_transition: lastTransitionObj
      ? {
          from_state: asString(lastTransitionObj.from_state, "") || null,
          to_state: asString(lastTransitionObj.to_state, "") || null,
          reason: asString(lastTransitionObj.reason, "") || null,
          ts: normalizeTimestamp(lastTransitionObj.ts),
          metadata: asOptionalRecord(lastTransitionObj.metadata),
        }
      : null,
    allowed_next_states: allowedNextStates,
    status_detail: asOptionalRecord(obj.status_detail),
    status_age_s: Number.isFinite(statusAgeRaw) ? statusAgeRaw : null,
  };
}

function normalizeStateMachineGraph(raw: unknown): StateMachineGraph | null {
  const obj = asOptionalRecord(raw);
  if (!obj) {
    return null;
  }
  const namespace = asString(obj.namespace, "").trim();
  if (!namespace) {
    return null;
  }

  const statesRaw = Array.isArray(obj.states) ? obj.states : [];
  const states = statesRaw
    .map((item) => String(item ?? "").trim())
    .filter((item) => item.length > 0);

  const transitionsRaw = Array.isArray(obj.transitions) ? obj.transitions : [];
  const transitions = transitionsRaw
    .map((item) => {
      const row = asOptionalRecord(item);
      if (!row) {
        return null;
      }
      const fromState = asString(row.from_state, "").trim();
      const toState = asString(row.to_state, "").trim();
      const note = asString(row.note, "").trim();
      if (!fromState && !toState) {
        return null;
      }
      return {
        from_state: fromState || null,
        to_state: toState || null,
        note: note || null,
      };
    })
    .filter((item): item is NonNullable<typeof item> => item !== null);

  const actionsRaw = Array.isArray(obj.actions) ? obj.actions : [];
  const actions = actionsRaw
    .map((item) => {
      const row = asOptionalRecord(item);
      if (!row) {
        return null;
      }
      const name = asString(row.name, "").trim();
      if (!name) {
        return null;
      }
      const paramsRaw = Array.isArray(row.params) ? row.params : [];
      const params = paramsRaw
        .map((paramRaw) => {
          const paramObj = asOptionalRecord(paramRaw);
          if (!paramObj) {
            return null;
          }
          const paramName = asString(paramObj.name, "").trim();
          if (!paramName) {
            return null;
          }
          return {
            name: paramName,
            kind: asString(paramObj.kind, "") || undefined,
            required:
              typeof paramObj.required === "boolean"
                ? paramObj.required
                : undefined,
            default: Object.prototype.hasOwnProperty.call(paramObj, "default")
              ? paramObj.default
              : undefined,
            annotation: asString(paramObj.annotation, "") || null,
          };
        })
        .filter((param): param is NonNullable<typeof param> => param !== null);

      const actionTransitionsRaw = Array.isArray(row.transitions)
        ? row.transitions
        : [];
      const actionTransitions = actionTransitionsRaw
        .map((transitionRaw) => {
          const transitionObj = asOptionalRecord(transitionRaw);
          if (!transitionObj) {
            return null;
          }
          const fromState = asString(transitionObj.from_state, "").trim();
          const toState = asString(transitionObj.to_state, "").trim();
          const note = asString(transitionObj.note, "").trim();
          if (!fromState && !toState && !note) {
            return null;
          }
          return {
            from_state: fromState || null,
            to_state: toState || null,
            note: note || null,
          };
        })
        .filter((transition): transition is NonNullable<typeof transition> => transition !== null);

      const effectsRaw = Array.isArray(row.effects) ? row.effects : [];
      const effects = effectsRaw
        .map((effectRaw) => {
          const effectObj = asOptionalRecord(effectRaw);
          if (!effectObj) {
            return null;
          }
          const deviceId = asString(effectObj.device_id, "").trim();
          const deviceAction = asString(effectObj.device_action, "").trim();
          if (!deviceId || !deviceAction) {
            return null;
          }
          return {
            device_id: deviceId,
            device_action: deviceAction,
            params: asOptionalRecord(effectObj.params),
            note: asString(effectObj.note, "") || null,
          };
        })
        .filter((effect): effect is NonNullable<typeof effect> => effect !== null);

      return {
        name,
        doc: asString(row.doc, "") || null,
        params,
        transitions: actionTransitions,
        effects,
      };
    })
    .filter((item): item is NonNullable<typeof item> => item !== null)
    .sort((a, b) => a.name.localeCompare(b.name));

  return {
    namespace,
    initial_state: asString(obj.initial_state, "") || null,
    states,
    transitions,
    actions,
  };
}

function normalizeStateMachineHistory(raw: unknown): StateMachineHistoryEntry[] {
  const rowsRaw = Array.isArray(raw)
    ? raw
    : asOptionalRecord(raw) && Array.isArray((raw as Record<string, unknown>).entries)
      ? ((raw as Record<string, unknown>).entries as unknown[])
      : [];
  const rows: StateMachineHistoryEntry[] = [];
  for (const item of rowsRaw) {
    const obj = asOptionalRecord(item);
    if (!obj) {
      continue;
    }
    rows.push({
      event: asString(obj.event, "") || null,
      from_state: asString(obj.from_state, "") || null,
      to_state: asString(obj.to_state, "") || null,
      state: asString(obj.state, "") || null,
      reason: asString(obj.reason, "") || null,
      message: asString(obj.message, "") || null,
      ok:
        typeof obj.ok === "boolean"
          ? obj.ok
          : obj.ok == null
            ? null
            : Boolean(obj.ok),
      source: asString(obj.source, "") || null,
      trigger_type: asString(obj.trigger_type, "") || null,
      trigger_name: asString(obj.trigger_name, "") || null,
      result: asString(obj.result, "") || null,
      error: asString(obj.error, "") || null,
      ts: normalizeTimestamp(obj.ts),
      metadata: asOptionalRecord(obj.metadata),
      raw: item,
    });
  }
  rows.sort((a, b) => {
    const at = Number(a.ts?.t_wall ?? Number.NaN);
    const bt = Number(b.ts?.t_wall ?? Number.NaN);
    const an = Number.isFinite(at) ? at : 0;
    const bn = Number.isFinite(bt) ? bt : 0;
    return an - bn;
  });
  return rows;
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
  params: Record<string, unknown>,
  opts?: {
    requestId?: string;
    sourceKind?: string;
    sourceId?: string;
  }
) {
  return apiFetch(`/api/devices/${deviceId}/call`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      action,
      params,
      request_id: opts?.requestId,
      source_kind: opts?.sourceKind,
      source_id: opts?.sourceId,
    }),
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
  params: Record<string, unknown>,
  opts?: {
    requestId?: string;
    sourceKind?: string;
    sourceId?: string;
  }
) {
  return apiFetch(`/api/processes/${processId}/call`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      action,
      params,
      request_id: opts?.requestId,
      source_kind: opts?.sourceKind,
      source_id: opts?.sourceId,
    }),
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
  processId: string,
  namespace: "follower" | "step_guard" = "follower"
): Promise<FollowerRuleStatus[]> {
  const resp = await callProcess(processId, `${namespace}.rules`, {});
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
  enabled: boolean,
  namespace: "follower" | "step_guard" = "follower"
) {
  return callProcess(
    processId,
    enabled ? `${namespace}.enable_rule` : `${namespace}.disable_rule`,
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

export async function fetchWatchdogStatus(
  processId: string
): Promise<WatchdogStatus[]> {
  const resp = await callProcess(processId, "watchdog.status", {});
  if (!resp.ok || !resp.result || typeof resp.result !== "object") {
    return [];
  }
  const watchdogsRaw = (resp.result as { watchdogs?: unknown }).watchdogs;
  if (!Array.isArray(watchdogsRaw)) {
    return [];
  }
  return watchdogsRaw
    .map((watchdog) => normalizeWatchdogStatus(watchdog))
    .filter((watchdog): watchdog is NonNullable<typeof watchdog> => watchdog !== null);
}

export async function setWatchdogEnabled(
  processId: string,
  watchdogId: string,
  enabled: boolean
) {
  return callProcess(
    processId,
    enabled ? "watchdog.enable" : "watchdog.disable",
    { watchdog_id: watchdogId }
  );
}

export async function clearWatchdogLatch(
  processId: string,
  watchdogId: string,
  ruleName: string
) {
  return callProcess(processId, "watchdog.clear_latch", {
    watchdog_id: watchdogId,
    rule: ruleName,
  });
}

export async function fetchStateMachineStatus(
  processId: string,
  statusAction: string
): Promise<StateMachineStatus | null> {
  const resp = await callProcess(processId, statusAction, {});
  if (!resp.ok) {
    return null;
  }
  return normalizeStateMachineStatus(resp.result);
}

export async function fetchStateMachineGraph(
  processId: string,
  graphAction: string
): Promise<StateMachineGraph | null> {
  const resp = await callProcess(processId, graphAction, {});
  if (!resp.ok) {
    return null;
  }
  return normalizeStateMachineGraph(resp.result);
}

export async function fetchStateMachineHistory(
  processId: string,
  historyAction: string,
  params?: Record<string, unknown>
): Promise<StateMachineHistoryEntry[]> {
  const resp = await callProcess(processId, historyAction, params ?? {});
  if (!resp.ok) {
    return [];
  }
  return normalizeStateMachineHistory(resp.result);
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

export type DefaultProfileFetchResult =
  | { ok: true; raw: unknown }
  | { ok: false; status: number; error: string };

export async function fetchDefaultUiProfile(): Promise<DefaultProfileFetchResult> {
  try {
    const resp = await fetch(`${API_BASE}/api/ui/default_profile`);
    if (resp.status === 404) {
      return { ok: false, status: 404, error: "no default profile" };
    }
    if (!resp.ok) {
      return {
        ok: false,
        status: resp.status,
        error: `server returned ${resp.status}`,
      };
    }
    const raw = await resp.json();
    return { ok: true, raw };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      error: error instanceof Error ? error.message : String(error),
    };
  }
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

export async function fetchCommandJournalStatus() {
  return apiFetch<CommandJournalStatusResult>("/api/commands/journal/status");
}

export async function fetchCommandJournalTail(params: Record<string, unknown>) {
  return apiFetch<CommandJournalTailResult>("/api/commands/journal/tail", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ params }),
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
