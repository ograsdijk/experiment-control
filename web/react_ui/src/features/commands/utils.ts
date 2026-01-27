import type { CommandHistoryEntry, CommandTargetKind } from "./types";

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
