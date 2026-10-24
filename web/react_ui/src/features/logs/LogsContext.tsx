import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type ReactNode,
  type SetStateAction,
} from "react";

import type { LogEntry } from "../../types";
import { LogRowStore, useLogRowsSnapshot } from "./logRowStore";
import { filterLogRows } from "./utils";

const LOG_SORT_NEWEST_FIRST_STORAGE_KEY = "ecui.logSortNewestFirst";
const MAX_LOG_ROWS = 2000;

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
 * - The Provider owns the **state container**. The live roster itself
 *   lives in a `LogRowStore` outside React state so a log line does not
 *   re-render every `useLogs()` consumer (App included); the filtered
 *   rows are derived by `useFilteredLogRows()`, which only the mounted
 *   Logs modal calls.
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
  /** Bounded, deduplicating roster (stable instance). */
  logStore: LogRowStore;
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
  /** When true, `useFilteredLogRows` orders rows newest-first. */
  logSortNewestFirst: boolean;
  setLogSortNewestFirst: Dispatch<SetStateAction<boolean>>;
  logLoading: boolean;
  setLogLoading: Dispatch<SetStateAction<boolean>>;
  expandedLogByKey: Record<string, boolean>;
  setExpandedLogByKey: Dispatch<SetStateAction<Record<string, boolean>>>;

  // -----------------------------------------------------------------
  // Refs (mutated from message handlers + scroll watchers)
  // -----------------------------------------------------------------
  /** DOM ref for the scroll container that holds the log rows. */
  logScrollRef: MutableRefObject<HTMLDivElement | null>;
}

const LogsContext = createContext<LogsContextValue | null>(null);

export function LogsProvider({ children }: { children: ReactNode }) {
  const [logStore] = useState(() => new LogRowStore(MAX_LOG_ROWS));
  const [logSeverityFilter, setLogSeverityFilter] = useState("all");
  const [logSourceFilter, setLogSourceFilter] = useState("all");
  const [logDeviceFilter, setLogDeviceFilter] = useState("all");
  const [logProcessFilter, setLogProcessFilter] = useState("all");
  const [logTextFilter, setLogTextFilter] = useState("");
  const [logAutoScroll, setLogAutoScroll] = useState(true);
  const [logSortNewestFirst, setLogSortNewestFirst] = useState<boolean>(() => {
    try {
      const raw = localStorage.getItem(LOG_SORT_NEWEST_FIRST_STORAGE_KEY);
      return raw === null ? true : raw === "true";
    } catch {
      return true;
    }
  });
  const [logLoading, setLogLoading] = useState(false);
  const [expandedLogByKey, setExpandedLogByKey] = useState<
    Record<string, boolean>
  >({});

  const logScrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(
        LOG_SORT_NEWEST_FIRST_STORAGE_KEY,
        String(logSortNewestFirst)
      );
    } catch {
      // ignore storage errors
    }
  }, [logSortNewestFirst]);

  const value = useMemo<LogsContextValue>(
    () => ({
      logStore,
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
      logSortNewestFirst,
      setLogSortNewestFirst,
      logLoading,
      setLogLoading,
      expandedLogByKey,
      setExpandedLogByKey,
      logScrollRef,
    }),
    [
      logStore,
      logSeverityFilter,
      logSourceFilter,
      logDeviceFilter,
      logProcessFilter,
      logTextFilter,
      logAutoScroll,
      logSortNewestFirst,
      logLoading,
      expandedLogByKey,
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

/**
 * Live roster with the five filter controls applied. Subscribes to the log
 * store, so call it only from the mounted Logs modal: the O(rows) filter
 * then runs only while the modal is open.
 */
export function useFilteredLogRows(): {
  rows: readonly LogEntry[];
  filteredRows: LogEntry[];
} {
  const {
    logStore,
    logSeverityFilter,
    logSourceFilter,
    logDeviceFilter,
    logProcessFilter,
    logTextFilter,
    logSortNewestFirst,
  } = useLogs();
  const rows = useLogRowsSnapshot(logStore);
  const filteredRows = useMemo(
    () =>
      filterLogRows(rows, {
        severity: logSeverityFilter,
        sourceKind: logSourceFilter,
        deviceId: logDeviceFilter,
        processId: logProcessFilter,
        text: logTextFilter,
        newestFirst: logSortNewestFirst,
      }),
    [
      rows,
      logSeverityFilter,
      logSourceFilter,
      logDeviceFilter,
      logProcessFilter,
      logTextFilter,
      logSortNewestFirst,
    ]
  );
  return { rows, filteredRows };
}
