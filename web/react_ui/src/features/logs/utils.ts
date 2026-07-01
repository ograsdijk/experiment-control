import type { LogEntry } from "../../types";

export function normalizeLogEntry(raw: unknown): LogEntry | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const entry = raw as Record<string, unknown>;
  const tsRaw = entry.ts;
  const ts =
    tsRaw && typeof tsRaw === "object"
      ? {
          t_wall:
            typeof (tsRaw as { t_wall?: unknown }).t_wall === "number"
              ? ((tsRaw as { t_wall?: number }).t_wall as number)
              : undefined,
          t_mono:
            typeof (tsRaw as { t_mono?: unknown }).t_mono === "number"
              ? ((tsRaw as { t_mono?: number }).t_mono as number)
              : undefined,
        }
      : undefined;
  return {
    version: typeof entry.version === "number" ? entry.version : undefined,
    severity: typeof entry.severity === "string" ? entry.severity : undefined,
    topic: typeof entry.topic === "string" ? entry.topic : undefined,
    source_kind:
      typeof entry.source_kind === "string" ? entry.source_kind : undefined,
    source_id: typeof entry.source_id === "string" ? entry.source_id : undefined,
    device_id: typeof entry.device_id === "string" ? entry.device_id : undefined,
    process_id:
      typeof entry.process_id === "string" ? entry.process_id : undefined,
    stream: typeof entry.stream === "string" ? entry.stream : undefined,
    message: typeof entry.message === "string" ? entry.message : undefined,
    payload_json:
      typeof entry.payload_json === "string" ? entry.payload_json : undefined,
    ts,
  };
}

export function logEntryKey(entry: LogEntry): string {
  return [
    entry.ts?.t_mono ?? "",
    entry.severity ?? "",
    entry.topic ?? "",
    entry.source_kind ?? "",
    entry.source_id ?? "",
    entry.message ?? "",
    entry.payload_json ?? "",
  ].join("|");
}

// Local wall-clock timestamp including the date, matching the TUI's
// "%Y-%m-%d %H:%M:%S" format. The date was previously omitted, so webui log
// and command timestamps were ambiguous across day boundaries.
const INVALID_WALL_TIME = "---------- --:--:--";

function formatWallDateTimeSeconds(value: number): string {
  if (!Number.isFinite(value)) {
    return INVALID_WALL_TIME;
  }
  const d = new Date(value * 1000);
  const yyyy = String(d.getFullYear()).padStart(4, "0");
  const mo = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${yyyy}-${mo}-${dd} ${hh}:${mm}:${ss}`;
}

export function formatLogTime(entry: LogEntry): string {
  const tWall = entry.ts?.t_wall;
  if (typeof tWall !== "number" || !Number.isFinite(tWall)) {
    return INVALID_WALL_TIME;
  }
  return formatWallDateTimeSeconds(tWall);
}

export function formatWallTimeSeconds(value: number): string {
  return formatWallDateTimeSeconds(value);
}

export function logSourceKindColor(sourceKind: string | null | undefined): string {
  const normalized = String(sourceKind ?? "").toLowerCase();
  if (normalized === "manager") {
    return "blue";
  }
  if (normalized === "driver") {
    return "orange";
  }
  if (normalized === "process") {
    return "violet";
  }
  return "gray";
}

export function toPrettyJson(value: unknown): string {
  if (value === undefined) {
    return "undefined";
  }
  try {
    return JSON.stringify(value, null, 2) ?? "undefined";
  } catch {
    return String(value);
  }
}
