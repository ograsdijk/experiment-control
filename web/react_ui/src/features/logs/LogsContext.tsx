import {
  createContext,
  useContext,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type ReactNode,
  type SetStateAction,
} from "react";

import type { LogEntry } from "../../types";

/**
 * Shared state container for the log-viewer UI.
 *
 * App.tsx historically held the live log roster, the five filter
 * controls (severity / source kind / device / process / text), the
 * autoscroll + loading flags, the expanded-by-key map, and the four
 * supporting refs (seen-keys dedup, scroll DOM ref, baseline flag,
 * last-seen key tracker) inline. All of those moved here so the log
 * panel and any future log-modal extraction can subscribe directly via
 * `useLogs()` without prop-drilling.
 *
 * **Scope choices** (mirrors the round-8/9/10 Context shape):
 *
 * - The Provider owns the **state container + the `filteredLogRows`
 *   derivation** (single source of truth, single dependency list).
 * - The WS subscription (`useLogsStream`) and the message-routing
 *   side effects stay in App.tsx for now — they call into helpers that
 *   haven't been extracted yet.
 * - The standalone `useLogsStream` hook in `features/logs/` is
 *   **not touched**. `beamline-vacuum` imports it directly via
 *   `@ec-ui/features/logs/useLogsStream`; that public API stays
 *   byte-identical from a downstream perspective.
 */

export interface LogsContextValue {
  // -----------------------------------------------------------------
  // Live log roster + UI state
  // -----------------------------------------------------------------
  logRows: LogEntry[];
  setLogRows: Dispatch<SetStateAction<LogEntry[]>>;
  logSeverityFilter: string;
  setLogSeverityFilter: Dispatch<SetStateAction<string>>;
  logSourceFilter: string;
  setLogSourceFilter: Dispatch<SetStateAction<string>>;
  logDeviceFilter: string;
  setLogDeviceFilter: Dispatch<SetStateAction<string>>;
  logProcessFilter: string;
  setLogProcessFilter: Dispatch<SetStateAction<string>>;
  logTextFilter: string;
  setLogTextFilter: Dispatch<SetStateAction<string>>;
  logAutoScroll: boolean;
  setLogAutoScroll: Dispatch<SetStateAction<boolean>>;
  logLoading: boolean;
  setLogLoading: Dispatch<SetStateAction<boolean>>;
  expandedLogByKey: Record<string, boolean>;
  setExpandedLogByKey: Dispatch<SetStateAction<Record<string, boolean>>>;
  /** logRows with all 5 filter controls applied. Recomputes only when
   *  inputs change. */
  filteredLogRows: LogEntry[];

  // -----------------------------------------------------------------
  // Refs (mutated from message handlers + scroll watchers)
  // -----------------------------------------------------------------
  /** Dedup set for log entries already routed to logRows. */
  logSeenRef: MutableRefObject<Set<string>>;
  /** DOM ref for the scroll container that holds the log rows. */
  logScrollRef: MutableRefObject<HTMLDivElement | null>;
  /** Set true on first non-empty `logRows` baseline so the new-row
   *  unread counter doesn't fire on the initial load. */
  logRowsBaselineReadyRef: MutableRefObject<boolean>;
  /** Last seen logEntryKey, used by the new-row detector to avoid
   *  re-counting already-shown entries on rerenders. */
  logRowsLastKeyRef: MutableRefObject<string | null>;
}

const LogsContext = createContext<LogsContextValue | null>(null);

export function LogsProvider({ children }: { children: ReactNode }) {
  const [logRows, setLogRows] = useState<LogEntry[]>([]);
  const [logSeverityFilter, setLogSeverityFilter] = useState("all");
  const [logSourceFilter, setLogSourceFilter] = useState("all");
  const [logDeviceFilter, setLogDeviceFilter] = useState("all");
  const [logProcessFilter, setLogProcessFilter] = useState("all");
  const [logTextFilter, setLogTextFilter] = useState("");
  const [logAutoScroll, setLogAutoScroll] = useState(true);
  const [logLoading, setLogLoading] = useState(false);
  const [expandedLogByKey, setExpandedLogByKey] = useState<
    Record<string, boolean>
  >({});

  const logSeenRef = useRef<Set<string>>(new Set());
  const logScrollRef = useRef<HTMLDivElement | null>(null);
  const logRowsBaselineReadyRef = useRef<boolean>(false);
  const logRowsLastKeyRef = useRef<string | null>(null);

  const filteredLogRows = useMemo(() => {
    const needle = logTextFilter.trim().toLowerCase();
    return logRows.filter((entry) => {
      const severity = String(entry.severity ?? "").toLowerCase();
      if (logSeverityFilter !== "all" && severity !== logSeverityFilter) {
        return false;
      }
      const sourceKind = String(entry.source_kind ?? "").toLowerCase();
      if (logSourceFilter !== "all" && sourceKind !== logSourceFilter) {
        return false;
      }
      const deviceId = String(entry.device_id ?? "");
      if (logDeviceFilter !== "all" && deviceId !== logDeviceFilter) {
        return false;
      }
      const processId = String(entry.process_id ?? "");
      if (logProcessFilter !== "all" && processId !== logProcessFilter) {
        return false;
      }
      if (!needle) {
        return true;
      }
      const haystack = `${entry.topic ?? ""} ${entry.message ?? ""} ${
        entry.payload_json ?? ""
      }`.toLowerCase();
      return haystack.includes(needle);
    });
  }, [
    logRows,
    logSeverityFilter,
    logSourceFilter,
    logDeviceFilter,
    logProcessFilter,
    logTextFilter,
  ]);

  const value = useMemo<LogsContextValue>(
    () => ({
      logRows,
      setLogRows,
      logSeverityFilter,
      setLogSeverityFilter,
      logSourceFilter,
      setLogSourceFilter,
      logDeviceFilter,
      setLogDeviceFilter,
      logProcessFilter,
      setLogProcessFilter,
      logTextFilter,
      setLogTextFilter,
      logAutoScroll,
      setLogAutoScroll,
      logLoading,
      setLogLoading,
      expandedLogByKey,
      setExpandedLogByKey,
      filteredLogRows,
      logSeenRef,
      logScrollRef,
      logRowsBaselineReadyRef,
      logRowsLastKeyRef,
    }),
    [
      logRows,
      logSeverityFilter,
      logSourceFilter,
      logDeviceFilter,
      logProcessFilter,
      logTextFilter,
      logAutoScroll,
      logLoading,
      expandedLogByKey,
      filteredLogRows,
    ]
  );

  return <LogsContext.Provider value={value}>{children}</LogsContext.Provider>;
}

export function useLogs(): LogsContextValue {
  const ctx = useContext(LogsContext);
  if (ctx === null) {
    throw new Error("useLogs must be called inside a <LogsProvider>");
  }
  return ctx;
}
