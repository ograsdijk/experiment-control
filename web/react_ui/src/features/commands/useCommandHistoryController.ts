import { useEffect, useMemo, useRef, useState } from "react";
import type { ApiResponse } from "../../api";
import type { CommandHistoryEntry } from "./types";
import {
  clampCommandHistoryLimit,
  normalizeCommandHistory,
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

type UseCommandHistoryControllerArgs = {
  callDeviceFn: (
    deviceId: string,
    action: string,
    params: Record<string, unknown>
  ) => Promise<ApiResponse<unknown>>;
  callProcessFn: (
    processId: string,
    action: string,
    params: Record<string, unknown>
  ) => Promise<ApiResponse<unknown>>;
};

export function useCommandHistoryController({
  callDeviceFn,
  callProcessFn,
}: UseCommandHistoryControllerArgs) {
  const [commandHistoryOpen, setCommandHistoryOpen] = useState(false);
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
      if (raw == null) {
        return true;
      }
      return raw === "true";
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

  const commandHistorySourceOptions = useMemo(() => {
    const out = new Set<string>();
    for (const row of commandHistoryRows) {
      const source = row.source.trim();
      if (source) {
        out.add(source);
      }
    }
    return [...out].sort((a, b) => a.localeCompare(b));
  }, [commandHistoryRows]);

  const filteredCommandHistoryRows = useMemo(() => {
    const needle = commandHistoryTextFilter.trim().toLowerCase();
    return commandHistoryRows.filter((row) => {
      const ok = row.response.ok === true;
      if (commandHistoryStatusFilter === "ok" && !ok) {
        return false;
      }
      if (commandHistoryStatusFilter === "error" && ok) {
        return false;
      }
      if (
        commandHistoryTargetFilter !== "all" &&
        row.target_kind !== commandHistoryTargetFilter
      ) {
        return false;
      }
      if (
        commandHistorySourceFilter !== "all" &&
        row.source !== commandHistorySourceFilter
      ) {
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
  }, [
    commandHistoryRows,
    commandHistoryStatusFilter,
    commandHistoryTargetFilter,
    commandHistorySourceFilter,
    commandHistoryTextFilter,
  ]);

  const commandHistoryHasError = useMemo(
    () => commandHistoryRows.some((row) => row.response.ok !== true),
    [commandHistoryRows]
  );

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
    if (
      commandHistorySourceFilter !== "all" &&
      !commandHistorySourceOptions.includes(commandHistorySourceFilter)
    ) {
      setCommandHistorySourceFilter("all");
    }
  }, [commandHistorySourceFilter, commandHistorySourceOptions]);

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

  return {
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
    commandHistoryHasError,
    appendCommandHistory,
    sendDeviceCommand,
    sendProcessCommand,
  };
}
