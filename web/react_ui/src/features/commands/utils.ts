import type {
  CommandHistoryEntry,
  CommandJournalEntry,
  CommandJournalStatus,
  CommandTargetKind,
} from "./types";

export function clampCommandHistoryLimit(
  value: number,
  defaults: {
    fallback: number;
    min: number;
    max: number;
  }
): number {
  if (!Number.isFinite(value)) {
    return defaults.fallback;
  }
  return Math.max(defaults.min, Math.min(defaults.max, Math.trunc(value)));
}

export function normalizeCommandHistory(
  raw: unknown,
  opts: {
    maxEntries: number;
    fallbackLimit: number;
    minLimit: number;
    maxLimit: number;
  }
): CommandHistoryEntry[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const out: CommandHistoryEntry[] = [];
  for (let idx = 0; idx < raw.length; idx += 1) {
    const item = raw[idx];
    if (!item || typeof item !== "object") {
      continue;
    }
    const obj = item as Record<string, unknown>;
    const targetKindRaw = String(obj.target_kind ?? obj.targetKind ?? "").toLowerCase();
    const target_kind: CommandTargetKind =
      targetKindRaw === "device" || targetKindRaw === "process"
        ? targetKindRaw
        : "process";
    const target_id = String(obj.target_id ?? obj.targetId ?? "").trim();
    const action = String(obj.action ?? "").trim();
    if (!target_id || !action) {
      continue;
    }
    const paramsRaw = obj.params;
    const params =
      paramsRaw && typeof paramsRaw === "object" && !Array.isArray(paramsRaw)
        ? { ...(paramsRaw as Record<string, unknown>) }
        : {};

    const source = String(obj.source ?? "unknown").trim() || "unknown";
    const tsWallRaw = obj.ts_wall_s ?? obj.tsWallS;
    const ts_wall_s =
      typeof tsWallRaw === "number" && Number.isFinite(tsWallRaw)
        ? tsWallRaw
        : Date.now() / 1000;
    const entryId = String(obj.id ?? `${ts_wall_s.toFixed(3)}-${idx}`);

    const responseRaw =
      obj.response && typeof obj.response === "object"
        ? (obj.response as Record<string, unknown>)
        : null;
    const ok = responseRaw ? responseRaw.ok === true : obj.ok === true;
    const response: CommandHistoryEntry["response"] = { ok };
    if (responseRaw && "result" in responseRaw) {
      response.result = responseRaw.result;
    } else if ("result" in obj) {
      response.result = obj.result;
    }
    const errorRaw =
      responseRaw && responseRaw.error && typeof responseRaw.error === "object"
        ? (responseRaw.error as Record<string, unknown>)
        : obj.error && typeof obj.error === "object"
          ? (obj.error as Record<string, unknown>)
          : null;
    if (errorRaw) {
      response.error = {
        code:
          typeof errorRaw.code === "string" && errorRaw.code
            ? errorRaw.code
            : undefined,
        message:
          typeof errorRaw.message === "string" && errorRaw.message
            ? errorRaw.message
            : undefined,
      };
    }

    out.push({
      id: entryId,
      ts_wall_s,
      target_kind,
      target_id,
      action,
      params,
      response,
      source,
    });
  }
  out.sort((a, b) => a.ts_wall_s - b.ts_wall_s || a.id.localeCompare(b.id));
  const clamped = clampCommandHistoryLimit(opts.maxEntries, {
    fallback: opts.fallbackLimit,
    min: opts.minLimit,
    max: opts.maxLimit,
  });
  if (out.length > clamped) {
    return out.slice(out.length - clamped);
  }
  return out;
}

function parseTargetFromDeviceId(deviceId: string): {
  targetKind: CommandTargetKind;
  targetId: string;
} {
  const text = String(deviceId ?? "").trim();
  const processPrefix = "process:";
  if (text.toLowerCase().startsWith(processPrefix)) {
    const stripped = text.slice(processPrefix.length).trim();
    return {
      targetKind: "process",
      targetId: stripped || text,
    };
  }
  return {
    targetKind: "device",
    targetId: text,
  };
}

function safeParseJson(text: string): {
  value: unknown;
  parseError: string | null;
} {
  const raw = String(text ?? "").trim();
  if (!raw) {
    return { value: null, parseError: null };
  }
  try {
    return { value: JSON.parse(raw), parseError: null };
  } catch (error) {
    return {
      value: null,
      parseError: String((error as Error)?.message ?? "invalid_json"),
    };
  }
}

function toFiniteNumber(value: unknown, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function toOptionalFiniteNumber(value: unknown): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function toOptionalString(value: unknown): string | null {
  if (value == null) {
    return null;
  }
  const text = String(value).trim();
  return text.length > 0 ? text : null;
}

export function normalizeCommandJournalStatus(raw: unknown): CommandJournalStatus | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  return {
    enabled: obj.enabled === true,
    path: toOptionalString(obj.path),
    start_error: toOptionalString(obj.start_error),
    queue_depth: toFiniteNumber(obj.queue_depth, 0),
    queue_max: toFiniteNumber(obj.queue_max, 0),
    batch_size: toFiniteNumber(obj.batch_size, 0),
    flush_interval_ms: toFiniteNumber(obj.flush_interval_ms, 0),
    retention:
      obj.retention && typeof obj.retention === "object" && !Array.isArray(obj.retention)
        ? {
            max_rows:
              obj.retention && typeof obj.retention === "object"
                ? toOptionalFiniteNumber(
                    (obj.retention as Record<string, unknown>).max_rows
                  )
                : null,
            max_age_days:
              obj.retention && typeof obj.retention === "object"
                ? toOptionalFiniteNumber(
                    (obj.retention as Record<string, unknown>).max_age_days
                  )
                : null,
          }
        : null,
    written: toFiniteNumber(obj.written, 0),
    dropped: toFiniteNumber(obj.dropped, 0),
    write_errors: toFiniteNumber(obj.write_errors, 0),
    pruned_rows: toFiniteNumber(obj.pruned_rows, 0),
    last_error: toOptionalString(obj.last_error),
    thread_alive: obj.thread_alive === true,
  };
}

export function normalizeCommandJournalRows(raw: unknown): CommandJournalEntry[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const out: CommandJournalEntry[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      continue;
    }
    const obj = item as Record<string, unknown>;
    const id = Number(obj.id);
    if (!Number.isFinite(id)) {
      continue;
    }
    const deviceId = String(obj.device_id ?? "").trim();
    const action = String(obj.action ?? "").trim();
    if (!deviceId || !action) {
      continue;
    }
    const { targetKind, targetId } = parseTargetFromDeviceId(deviceId);
    const paramsJson = String(obj.params_json ?? "");
    const paramsParsed = safeParseJson(paramsJson);
    const errorJson = String(obj.error_json ?? "");
    const errorParsed = safeParseJson(errorJson);
    const resultJson = String(obj.result_json ?? "");
    const resultParsed = safeParseJson(resultJson);
    const sourceKind = toOptionalString(obj.source_kind);
    const sourceId = toOptionalString(obj.source_id);
    let source = "unknown";
    if (sourceKind && sourceId) {
      source = `${sourceKind}:${sourceId}`;
    } else if (sourceKind) {
      source = sourceKind;
    } else if (sourceId) {
      source = sourceId;
    }

    out.push({
      id: Math.trunc(id),
      ts_wall_s: toFiniteNumber(obj.t_wall, Date.now() / 1000),
      ts_mono_s: toFiniteNumber(obj.t_mono, 0),
      instance_id: String(obj.instance_id ?? ""),
      device_id: deviceId,
      target_kind: targetKind,
      target_id: targetId,
      action,
      params_json: paramsJson,
      params:
        paramsParsed.value && typeof paramsParsed.value === "object" && !Array.isArray(paramsParsed.value)
          ? (paramsParsed.value as Record<string, unknown>)
          : null,
      params_parse_error: paramsParsed.parseError,
      ok: obj.ok === true,
      status: toOptionalString(obj.status),
      error_json: errorJson,
      error:
        errorParsed.value && typeof errorParsed.value === "object" && !Array.isArray(errorParsed.value)
          ? (errorParsed.value as Record<string, unknown>)
          : null,
      result_json: resultJson,
      result: resultParsed.value,
      request_id: toOptionalString(obj.request_id),
      caller_process_id: toOptionalString(obj.caller_process_id),
      source_kind: sourceKind,
      source_id: sourceId,
      source,
      is_remote_target: obj.is_remote_target === true,
    });
  }
  out.sort((a, b) => a.id - b.id);
  return out;
}
