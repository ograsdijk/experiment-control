import { useEffect, useMemo, useRef, useState } from "react";
import type {
  ApiResponse,
  CommandJournalTailResult,
  CommandJournalStatusResult,
} from "../../api";
import {
  fetchCommandJournalStatus,
  fetchCommandJournalTail,
} from "../../api";
import type {
  CommandHistoryEntry,
  CommandHistoryMode,
  CommandJournalEntry,
  CommandJournalStatus,
  CommandRestoreExecutionReport,
  CommandRestorePreviewRow,
} from "./types";
import {
  clampCommandHistoryLimit,
  normalizeCommandHistory,
  normalizeCommandJournalRows,
  normalizeCommandJournalStatus,
} from "./utils";
import { toPrettyJson } from "../logs/utils";

const DEFAULT_COMMAND_HISTORY_LIMIT = 200;
const MIN_COMMAND_HISTORY_LIMIT = 20;
const MAX_COMMAND_HISTORY_LIMIT = 2000;
const COMMAND_HISTORY_LIMIT_BOUNDS = {
  fallback: DEFAULT_COMMAND_HISTORY_LIMIT,
  min: MIN_COMMAND_HISTORY_LIMIT,
  max: MAX_COMMAND_HISTORY_LIMIT,
} as const;
const COMMAND_HISTORY_STORAGE_KEY = "ecui.commandHistory";
const COMMAND_HISTORY_LIMIT_STORAGE_KEY = "ecui.commandHistoryLimit";
const COMMAND_HISTORY_AUTOSCROLL_STORAGE_KEY = "ecui.commandHistoryAutoScroll";
const COMMAND_HISTORY_MODE_STORAGE_KEY = "ecui.commandHistoryMode";

const DEFAULT_COMMAND_JOURNAL_LIMIT = 500;
const MIN_COMMAND_JOURNAL_LIMIT = 20;
const MAX_COMMAND_JOURNAL_LIMIT = 5000;
const COMMAND_JOURNAL_LIMIT_BOUNDS = {
  fallback: DEFAULT_COMMAND_JOURNAL_LIMIT,
  min: MIN_COMMAND_JOURNAL_LIMIT,
  max: MAX_COMMAND_JOURNAL_LIMIT,
} as const;
const COMMAND_JOURNAL_LIMIT_STORAGE_KEY = "ecui.commandJournalLimit";
const COMMAND_JOURNAL_LAST_PER_DEVICE_ONLY_STORAGE_KEY =
  "ecui.commandJournalLastPerDeviceOnly";
const COMMAND_RESTORE_INCLUDE_FAILED_STORAGE_KEY =
  "ecui.commandRestoreIncludeFailed";
const COMMAND_RESTORE_INCLUDE_REMOTE_STORAGE_KEY =
  "ecui.commandRestoreIncludeRemote";
const COMMAND_RESTORE_INCLUDE_PROCESS_CONTROL_STORAGE_KEY =
  "ecui.commandRestoreIncludeProcessControl";

type CallOptions = {
  requestId?: string;
  sourceKind?: string;
  sourceId?: string;
};

type UseCommandHistoryControllerArgs = {
  callDeviceFn: (
    deviceId: string,
    action: string,
    params: Record<string, unknown>,
    options?: CallOptions
  ) => Promise<ApiResponse<unknown>>;
  callProcessFn: (
    processId: string,
    action: string,
    params: Record<string, unknown>,
    options?: CallOptions
  ) => Promise<ApiResponse<unknown>>;
  fetchCommandJournalStatusFn?: () => Promise<
    ApiResponse<CommandJournalStatusResult>
  >;
  fetchCommandJournalTailFn?: (
    params: Record<string, unknown>
  ) => Promise<ApiResponse<CommandJournalTailResult>>;
};

function asBoolStorage(value: string | null, fallback: boolean): boolean {
  if (value == null) {
    return fallback;
  }
  if (value === "true" || value === "1") {
    return true;
  }
  if (value === "false" || value === "0") {
    return false;
  }
  return fallback;
}

function normalizeMode(value: string | null): CommandHistoryMode {
  const text = String(value ?? "").trim().toLowerCase();
  if (text === "live" || text === "journal" || text === "restore") {
    return text;
  }
  return "live";
}

function summarizeApiError(error: unknown): string {
  if (!error || typeof error !== "object") {
    return "unknown_error";
  }
  const obj = error as Record<string, unknown>;
  const message = String(obj.message ?? "").trim();
  if (message) {
    return message;
  }
  const code = String(obj.code ?? "").trim();
  if (code) {
    return code;
  }
  return "unknown_error";
}

function isProcessControlAction(action: string): boolean {
  const text = String(action ?? "").trim().toLowerCase();
  return (
    text === "manager.processes.start" ||
    text === "manager.processes.stop" ||
    text === "manager.processes.restart"
  );
}

function buildSourceOptionsFromLive(rows: CommandHistoryEntry[]): string[] {
  const out = new Set<string>();
  for (const row of rows) {
    const source = row.source.trim();
    if (source) {
      out.add(source);
    }
  }
  return [...out].sort((a, b) => a.localeCompare(b));
}

function buildSourceOptionsFromJournal(rows: CommandJournalEntry[]): string[] {
  const out = new Set<string>();
  for (const row of rows) {
    const source = row.source.trim();
    if (source) {
      out.add(source);
    }
  }
  return [...out].sort((a, b) => a.localeCompare(b));
}

function filterLiveRows(
  rows: CommandHistoryEntry[],
  opts: {
    statusFilter: string;
    targetFilter: string;
    sourceFilter: string;
    textFilter: string;
  }
): CommandHistoryEntry[] {
  const needle = opts.textFilter.trim().toLowerCase();
  return rows.filter((row) => {
    const ok = row.response.ok === true;
    if (opts.statusFilter === "ok" && !ok) {
      return false;
    }
    if (opts.statusFilter === "error" && ok) {
      return false;
    }
    if (opts.targetFilter !== "all" && row.target_kind !== opts.targetFilter) {
      return false;
    }
    if (opts.sourceFilter !== "all" && row.source !== opts.sourceFilter) {
      return false;
    }
    if (!needle) {
      return true;
    }
    const haystack = [
      row.target_kind,
      row.target_id,
      row.action,
      row.source,
      row.response.error?.code ?? "",
      row.response.error?.message ?? "",
      toPrettyJson(row.params),
      toPrettyJson(row.response.result),
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(needle);
  });
}

function filterJournalRows(
  rows: CommandJournalEntry[],
  opts: {
    statusFilter: string;
    targetFilter: string;
    sourceFilter: string;
    textFilter: string;
  }
): CommandJournalEntry[] {
  const needle = opts.textFilter.trim().toLowerCase();
  return rows.filter((row) => {
    const ok = row.ok === true;
    if (opts.statusFilter === "ok" && !ok) {
      return false;
    }
    if (opts.statusFilter === "error" && ok) {
      return false;
    }
    if (opts.targetFilter !== "all" && row.target_kind !== opts.targetFilter) {
      return false;
    }
    if (opts.sourceFilter !== "all" && row.source !== opts.sourceFilter) {
      return false;
    }
    if (!needle) {
      return true;
    }
    const haystack = [
      row.target_kind,
      row.target_id,
      row.action,
      row.source,
      row.params_json,
      row.error_json,
      row.result_json,
      row.status ?? "",
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(needle);
  });
}

function latestPerTargetActionRows(rows: CommandJournalEntry[]): CommandJournalEntry[] {
  const latestByTargetAction = new Map<string, CommandJournalEntry>();
  for (const row of rows) {
    const key = `${row.target_kind}\u0000${row.target_id}\u0000${row.action}`;
    const prev = latestByTargetAction.get(key);
    if (!prev || row.id > prev.id) {
      latestByTargetAction.set(key, row);
    }
  }
  const out = [...latestByTargetAction.values()];
  out.sort((a, b) => a.id - b.id);
  return out;
}

export function useCommandHistoryController({
  callDeviceFn,
  callProcessFn,
  fetchCommandJournalStatusFn = fetchCommandJournalStatus,
  fetchCommandJournalTailFn = fetchCommandJournalTail,
}: UseCommandHistoryControllerArgs) {
  const [commandHistoryOpen, setCommandHistoryOpen] = useState(false);
  const [commandHistoryMode, setCommandHistoryMode] = useState<CommandHistoryMode>(() => {
    try {
      return normalizeMode(localStorage.getItem(COMMAND_HISTORY_MODE_STORAGE_KEY));
    } catch {
      return "live";
    }
  });
  const [commandHistoryRows, setCommandHistoryRows] = useState<
    CommandHistoryEntry[]
  >(() => {
    try {
      const raw = localStorage.getItem(COMMAND_HISTORY_STORAGE_KEY);
      if (!raw) {
        return [];
      }
      const limitRaw = localStorage.getItem(COMMAND_HISTORY_LIMIT_STORAGE_KEY);
      const limit = clampCommandHistoryLimit(
        limitRaw != null ? Number(limitRaw) : DEFAULT_COMMAND_HISTORY_LIMIT,
        COMMAND_HISTORY_LIMIT_BOUNDS
      );
      return normalizeCommandHistory(JSON.parse(raw), {
        maxEntries: limit,
        fallbackLimit: DEFAULT_COMMAND_HISTORY_LIMIT,
        minLimit: MIN_COMMAND_HISTORY_LIMIT,
        maxLimit: MAX_COMMAND_HISTORY_LIMIT,
      });
    } catch {
      return [];
    }
  });
  const [commandHistoryLimit, setCommandHistoryLimit] = useState(() => {
    try {
      const raw = localStorage.getItem(COMMAND_HISTORY_LIMIT_STORAGE_KEY);
      const parsed = raw ? Number(raw) : Number.NaN;
      return clampCommandHistoryLimit(parsed, COMMAND_HISTORY_LIMIT_BOUNDS);
    } catch {
      return DEFAULT_COMMAND_HISTORY_LIMIT;
    }
  });
  const [commandHistoryAutoScroll, setCommandHistoryAutoScroll] = useState(() => {
    try {
      const raw = localStorage.getItem(COMMAND_HISTORY_AUTOSCROLL_STORAGE_KEY);
      return asBoolStorage(raw, true);
    } catch {
      return true;
    }
  });
  const [commandHistoryStatusFilter, setCommandHistoryStatusFilter] =
    useState("all");
  const [commandHistoryTargetFilter, setCommandHistoryTargetFilter] =
    useState("all");
  const [commandHistorySourceFilter, setCommandHistorySourceFilter] =
    useState("all");
  const [commandHistoryTextFilter, setCommandHistoryTextFilter] = useState("");
  const commandHistoryCounterRef = useRef(0);

  const [commandJournalStatus, setCommandJournalStatus] =
    useState<CommandJournalStatus | null>(null);
  const [commandJournalStatusLoading, setCommandJournalStatusLoading] =
    useState(false);
  const [commandJournalStatusError, setCommandJournalStatusError] =
    useState<string | null>(null);
  const [commandJournalRows, setCommandJournalRows] = useState<
    CommandJournalEntry[]
  >([]);
  const [commandJournalLimit, setCommandJournalLimit] = useState(() => {
    try {
      const raw = localStorage.getItem(COMMAND_JOURNAL_LIMIT_STORAGE_KEY);
      const parsed = raw ? Number(raw) : Number.NaN;
      return clampCommandHistoryLimit(parsed, COMMAND_JOURNAL_LIMIT_BOUNDS);
    } catch {
      return DEFAULT_COMMAND_JOURNAL_LIMIT;
    }
  });
  const [commandJournalLoading, setCommandJournalLoading] = useState(false);
  const [commandJournalError, setCommandJournalError] = useState<string | null>(null);
  const [commandJournalTotalMatched, setCommandJournalTotalMatched] = useState(0);
  const [commandJournalLatestId, setCommandJournalLatestId] = useState<number | null>(
    null
  );
  const [commandJournalStatusFilter, setCommandJournalStatusFilter] =
    useState("all");
  const [commandJournalTargetFilter, setCommandJournalTargetFilter] =
    useState("all");
  const [commandJournalSourceFilter, setCommandJournalSourceFilter] =
    useState("all");
  const [commandJournalTextFilter, setCommandJournalTextFilter] = useState("");
  const [commandJournalLastPerDeviceOnly, setCommandJournalLastPerDeviceOnly] =
    useState(() => {
      try {
        return asBoolStorage(
          localStorage.getItem(COMMAND_JOURNAL_LAST_PER_DEVICE_ONLY_STORAGE_KEY),
          false
        );
      } catch {
        return false;
      }
    });
  const [selectedCommandJournalIds, setSelectedCommandJournalIds] = useState<
    Record<number, boolean>
  >({});

  const [commandRestoreIncludeFailed, setCommandRestoreIncludeFailed] = useState(() => {
    try {
      return asBoolStorage(
        localStorage.getItem(COMMAND_RESTORE_INCLUDE_FAILED_STORAGE_KEY),
        false
      );
    } catch {
      return false;
    }
  });
  const [commandRestoreIncludeRemote, setCommandRestoreIncludeRemote] = useState(() => {
    try {
      return asBoolStorage(
        localStorage.getItem(COMMAND_RESTORE_INCLUDE_REMOTE_STORAGE_KEY),
        false
      );
    } catch {
      return false;
    }
  });
  const [commandRestoreIncludeProcessControl, setCommandRestoreIncludeProcessControl] =
    useState(() => {
      try {
        return asBoolStorage(
          localStorage.getItem(COMMAND_RESTORE_INCLUDE_PROCESS_CONTROL_STORAGE_KEY),
          false
        );
      } catch {
        return false;
      }
    });
  const [commandRestoreBusy, setCommandRestoreBusy] = useState(false);
  const [commandRestoreLastReport, setCommandRestoreLastReport] =
    useState<CommandRestoreExecutionReport | null>(null);

  const commandHistorySourceOptions = useMemo(
    () => buildSourceOptionsFromLive(commandHistoryRows),
    [commandHistoryRows]
  );

  const filteredCommandHistoryRows = useMemo(
    () =>
      filterLiveRows(commandHistoryRows, {
        statusFilter: commandHistoryStatusFilter,
        targetFilter: commandHistoryTargetFilter,
        sourceFilter: commandHistorySourceFilter,
        textFilter: commandHistoryTextFilter,
      }),
    [
      commandHistoryRows,
      commandHistoryStatusFilter,
      commandHistoryTargetFilter,
      commandHistorySourceFilter,
      commandHistoryTextFilter,
    ]
  );

  const commandHistoryHasError = useMemo(
    () => commandHistoryRows.some((row) => row.response.ok !== true),
    [commandHistoryRows]
  );

  const commandJournalSourceOptions = useMemo(
    () => buildSourceOptionsFromJournal(commandJournalRows),
    [commandJournalRows]
  );

  const filteredCommandJournalRows = useMemo(
    () => {
      const base = filterJournalRows(commandJournalRows, {
        statusFilter: commandJournalStatusFilter,
        targetFilter: commandJournalTargetFilter,
        sourceFilter: commandJournalSourceFilter,
        textFilter: commandJournalTextFilter,
      });
      return commandJournalLastPerDeviceOnly ? latestPerTargetActionRows(base) : base;
    },
    [
      commandJournalRows,
      commandJournalStatusFilter,
      commandJournalTargetFilter,
      commandJournalSourceFilter,
      commandJournalTextFilter,
      commandJournalLastPerDeviceOnly,
    ]
  );

  const selectedCommandJournalRows = useMemo(() => {
    const selectedIds = new Set(
      Object.entries(selectedCommandJournalIds)
        .filter((entry) => entry[1])
        .map((entry) => Number(entry[0]))
        .filter((id) => Number.isFinite(id))
    );
    return commandJournalRows.filter((row) => selectedIds.has(row.id));
  }, [commandJournalRows, selectedCommandJournalIds]);

  const commandRestorePreviewRows = useMemo<CommandRestorePreviewRow[]>(() => {
    return [...selectedCommandJournalRows]
      .sort((a, b) => a.id - b.id)
      .map((row) => {
        let skipReason: string | null = null;
        if (row.params_parse_error) {
          skipReason = `invalid params_json: ${row.params_parse_error}`;
        } else if (!commandRestoreIncludeFailed && !row.ok) {
          skipReason = "command result was not ok";
        } else if (!commandRestoreIncludeRemote && row.is_remote_target) {
          skipReason = "remote target replay is disabled";
        } else if (
          !commandRestoreIncludeProcessControl &&
          row.target_kind === "process" &&
          isProcessControlAction(row.action)
        ) {
          skipReason = "process control action replay is disabled";
        } else if (!row.target_id || !row.action) {
          skipReason = "missing target or action";
        }
        return {
          id: row.id,
          ts_wall_s: row.ts_wall_s,
          target_kind: row.target_kind,
          target_id: row.target_id,
          action: row.action,
          source: row.source,
          ok: row.ok,
          is_remote_target: row.is_remote_target,
          include: skipReason == null,
          skip_reason: skipReason,
          params:
            row.params ??
            (row.params_json.trim().length === 0 ? {} : null),
          params_json: row.params_json,
        };
      });
  }, [
    selectedCommandJournalRows,
    commandRestoreIncludeFailed,
    commandRestoreIncludeRemote,
    commandRestoreIncludeProcessControl,
  ]);

  useEffect(() => {
    setCommandHistoryRows((prev) => {
      if (prev.length <= commandHistoryLimit) {
        return prev;
      }
      return prev.slice(prev.length - commandHistoryLimit);
    });
  }, [commandHistoryLimit]);

  useEffect(() => {
    try {
      localStorage.setItem(
        COMMAND_HISTORY_STORAGE_KEY,
        JSON.stringify(commandHistoryRows)
      );
    } catch {
      // ignore storage errors
    }
  }, [commandHistoryRows]);

  useEffect(() => {
    try {
      localStorage.setItem(
        COMMAND_HISTORY_LIMIT_STORAGE_KEY,
        String(commandHistoryLimit)
      );
    } catch {
      // ignore storage errors
    }
  }, [commandHistoryLimit]);

  useEffect(() => {
    try {
      localStorage.setItem(
        COMMAND_HISTORY_AUTOSCROLL_STORAGE_KEY,
        String(commandHistoryAutoScroll)
      );
    } catch {
      // ignore storage errors
    }
  }, [commandHistoryAutoScroll]);

  useEffect(() => {
    try {
      localStorage.setItem(COMMAND_HISTORY_MODE_STORAGE_KEY, commandHistoryMode);
    } catch {
      // ignore storage errors
    }
  }, [commandHistoryMode]);

  useEffect(() => {
    try {
      localStorage.setItem(
        COMMAND_JOURNAL_LIMIT_STORAGE_KEY,
        String(commandJournalLimit)
      );
    } catch {
      // ignore storage errors
    }
  }, [commandJournalLimit]);

  useEffect(() => {
    try {
      localStorage.setItem(
        COMMAND_JOURNAL_LAST_PER_DEVICE_ONLY_STORAGE_KEY,
        String(commandJournalLastPerDeviceOnly)
      );
    } catch {
      // ignore storage errors
    }
  }, [commandJournalLastPerDeviceOnly]);

  useEffect(() => {
    try {
      localStorage.setItem(
        COMMAND_RESTORE_INCLUDE_FAILED_STORAGE_KEY,
        String(commandRestoreIncludeFailed)
      );
    } catch {
      // ignore storage errors
    }
  }, [commandRestoreIncludeFailed]);

  useEffect(() => {
    try {
      localStorage.setItem(
        COMMAND_RESTORE_INCLUDE_REMOTE_STORAGE_KEY,
        String(commandRestoreIncludeRemote)
      );
    } catch {
      // ignore storage errors
    }
  }, [commandRestoreIncludeRemote]);

  useEffect(() => {
    try {
      localStorage.setItem(
        COMMAND_RESTORE_INCLUDE_PROCESS_CONTROL_STORAGE_KEY,
        String(commandRestoreIncludeProcessControl)
      );
    } catch {
      // ignore storage errors
    }
  }, [commandRestoreIncludeProcessControl]);

  useEffect(() => {
    if (
      commandHistorySourceFilter !== "all" &&
      !commandHistorySourceOptions.includes(commandHistorySourceFilter)
    ) {
      setCommandHistorySourceFilter("all");
    }
  }, [commandHistorySourceFilter, commandHistorySourceOptions]);

  useEffect(() => {
    if (
      commandJournalSourceFilter !== "all" &&
      !commandJournalSourceOptions.includes(commandJournalSourceFilter)
    ) {
      setCommandJournalSourceFilter("all");
    }
  }, [commandJournalSourceFilter, commandJournalSourceOptions]);

  const refreshCommandJournalStatus = async () => {
    setCommandJournalStatusLoading(true);
    const resp = await fetchCommandJournalStatusFn();
    if (!resp.ok) {
      setCommandJournalStatus(null);
      setCommandJournalStatusError(summarizeApiError(resp.error));
      setCommandJournalStatusLoading(false);
      return;
    }
    const normalized = normalizeCommandJournalStatus(
      resp.result as CommandJournalStatusResult | null | undefined
    );
    setCommandJournalStatus(normalized);
    setCommandJournalStatusError(null);
    setCommandJournalStatusLoading(false);
  };

  const refreshCommandJournalTail = async () => {
    setCommandJournalLoading(true);
    const resp = await fetchCommandJournalTailFn({
      limit: commandJournalLimit,
    });
    if (!resp.ok) {
      setCommandJournalError(summarizeApiError(resp.error));
      setCommandJournalLoading(false);
      return;
    }
    const result =
      resp.result && typeof resp.result === "object"
        ? (resp.result as Record<string, unknown>)
        : {};
    const rows = normalizeCommandJournalRows(result.entries);
    setCommandJournalRows(rows);
    const totalMatchedRaw = Number(result.total_matched);
    setCommandJournalTotalMatched(
      Number.isFinite(totalMatchedRaw) ? Math.max(0, Math.trunc(totalMatchedRaw)) : rows.length
    );
    const latestIdRaw = Number(result.latest_id);
    setCommandJournalLatestId(Number.isFinite(latestIdRaw) ? Math.trunc(latestIdRaw) : null);
    setSelectedCommandJournalIds((prev) => {
      const existing = new Set(rows.map((row) => row.id));
      const next: Record<number, boolean> = {};
      let changed = false;
      for (const [key, value] of Object.entries(prev)) {
        const id = Number(key);
        if (!value || !Number.isFinite(id)) {
          continue;
        }
        if (existing.has(id)) {
          next[id] = true;
        } else {
          changed = true;
        }
      }
      return changed ? next : prev;
    });
    setCommandJournalError(null);
    setCommandJournalLoading(false);
  };

  const refreshCommandJournal = async () => {
    await Promise.all([
      refreshCommandJournalStatus(),
      refreshCommandJournalTail(),
    ]);
  };

  useEffect(() => {
    if (!commandHistoryOpen) {
      return;
    }
    let cancelled = false;
    let ticks = 0;
    const load = async () => {
      if (cancelled) {
        return;
      }
      await refreshCommandJournalTail();
      ticks += 1;
      if (ticks % 3 === 1) {
        await refreshCommandJournalStatus();
      }
    };
    void load();
    const interval = window.setInterval(() => {
      void load();
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [commandHistoryOpen, commandJournalLimit]);

  const appendCommandHistory = (
    entry: Omit<CommandHistoryEntry, "id" | "ts_wall_s">
  ) => {
    const nowS = Date.now() / 1000;
    commandHistoryCounterRef.current += 1;
    const id = `${Math.trunc(nowS * 1000)}-${commandHistoryCounterRef.current}`;
    setCommandHistoryRows((prev) => {
      const next = [...prev, { ...entry, id, ts_wall_s: nowS }];
      if (next.length <= commandHistoryLimit) {
        return next;
      }
      return next.slice(next.length - commandHistoryLimit);
    });
  };

  const sendDeviceCommand = async (
    deviceId: string,
    action: string,
    params: Record<string, unknown>,
    source: string
  ) => {
    const response = await callDeviceFn(deviceId, action, params);
    appendCommandHistory({
      target_kind: "device",
      target_id: deviceId,
      action,
      params,
      response: response as ApiResponse<unknown>,
      source,
    });
    return response;
  };

  const sendProcessCommand = async (
    processId: string,
    action: string,
    params: Record<string, unknown>,
    source: string
  ) => {
    const response = await callProcessFn(processId, action, params);
    appendCommandHistory({
      target_kind: "process",
      target_id: processId,
      action,
      params,
      response: response as ApiResponse<unknown>,
      source,
    });
    return response;
  };

  const toggleCommandJournalSelection = (id: number, selected?: boolean) => {
    setSelectedCommandJournalIds((prev) => {
      const was = prev[id] === true;
      const nextSelected = selected == null ? !was : selected;
      if (was === nextSelected) {
        return prev;
      }
      if (!nextSelected) {
        const next = { ...prev };
        delete next[id];
        return next;
      }
      return { ...prev, [id]: true };
    });
  };

  const selectAllFilteredCommandJournal = () => {
    setSelectedCommandJournalIds((prev) => {
      const next = { ...prev };
      for (const row of filteredCommandJournalRows) {
        next[row.id] = true;
      }
      return next;
    });
  };

  const clearCommandJournalSelection = () => {
    setSelectedCommandJournalIds({});
  };

  const executeCommandRestore = async () => {
    if (commandRestoreBusy) {
      return null;
    }
    const preview = commandRestorePreviewRows;
    const report: CommandRestoreExecutionReport = {
      attempted: preview.length,
      executed: 0,
      skipped: preview.filter((row) => !row.include).length,
      ok: 0,
      error: 0,
      rows: [],
    };
    setCommandRestoreBusy(true);
    try {
      for (const row of preview) {
        if (!row.include) {
          continue;
        }
        const params = row.params ?? {};
        let response: ApiResponse<unknown>;
        if (row.target_kind === "process") {
          response = await callProcessFn(
            row.target_id,
            row.action,
            params,
            {
              sourceKind: "webui",
              sourceId: "state_restore",
            }
          );
        } else {
          response = await callDeviceFn(
            row.target_id,
            row.action,
            params,
            {
              sourceKind: "webui",
              sourceId: "state_restore",
            }
          );
        }
        appendCommandHistory({
          target_kind: row.target_kind,
          target_id: row.target_id,
          action: row.action,
          params,
          response,
          source: "state_restore",
        });
        report.executed += 1;
        const ok = response.ok === true;
        if (ok) {
          report.ok += 1;
        } else {
          report.error += 1;
        }
        report.rows.push({
          id: row.id,
          target_kind: row.target_kind,
          target_id: row.target_id,
          action: row.action,
          ok,
          error_code: String(response.error?.code ?? "").trim() || null,
          error_message: String(response.error?.message ?? "").trim() || null,
        });
      }
      setCommandRestoreLastReport(report);
      return report;
    } finally {
      setCommandRestoreBusy(false);
      void refreshCommandJournalTail();
    }
  };

  return {
    commandHistoryOpen,
    setCommandHistoryOpen,
    commandHistoryMode,
    setCommandHistoryMode,
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
    commandHistoryHasError,
    commandJournalStatus,
    commandJournalStatusLoading,
    commandJournalStatusError,
    commandJournalRows,
    commandJournalLimit,
    setCommandJournalLimit,
    commandJournalLoading,
    commandJournalError,
    commandJournalTotalMatched,
    commandJournalLatestId,
    commandJournalStatusFilter,
    setCommandJournalStatusFilter,
    commandJournalTargetFilter,
    setCommandJournalTargetFilter,
    commandJournalSourceFilter,
    setCommandJournalSourceFilter,
    commandJournalTextFilter,
    setCommandJournalTextFilter,
    commandJournalLastPerDeviceOnly,
    setCommandJournalLastPerDeviceOnly,
    commandJournalSourceOptions,
    filteredCommandJournalRows,
    selectedCommandJournalIds,
    selectedCommandJournalRows,
    toggleCommandJournalSelection,
    selectAllFilteredCommandJournal,
    clearCommandJournalSelection,
    commandRestoreIncludeFailed,
    setCommandRestoreIncludeFailed,
    commandRestoreIncludeRemote,
    setCommandRestoreIncludeRemote,
    commandRestoreIncludeProcessControl,
    setCommandRestoreIncludeProcessControl,
    commandRestorePreviewRows,
    commandRestoreBusy,
    commandRestoreLastReport,
    executeCommandRestore,
    refreshCommandJournalStatus,
    refreshCommandJournalTail,
    refreshCommandJournal,
    appendCommandHistory,
    sendDeviceCommand,
    sendProcessCommand,
  };
}
