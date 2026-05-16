import { useEffect, useRef, useState } from "react";
import { buildWsUrl, fetchLogTail } from "../../api";
import { normalizeLogEntry } from "./utils";
import type { LogEntry, LogMessage } from "../../types";

export type UseLogsStreamOptions = {
  /** Max entries to keep in the ring buffer. Defaults to 100. */
  maxEntries?: number;
  /**
   * Seed the buffer with a one-shot fetchLogTail call on mount. Provide a
   * positive integer for the limit, or 0/undefined to skip seeding.
   */
  seedLimit?: number;
  /**
   * Optional callback fired for each accepted log entry after the in-hook
   * buffer is updated. Use when an outer consumer (App.tsx's main log panel,
   * for example) keeps its own buffer. Errors thrown by the callback are
   * swallowed so a bad listener can't tear down the socket.
   */
  onEntry?: (entry: LogEntry) => void;
};

export type UseLogsStreamResult = {
  entries: LogEntry[];
  wsConnected: boolean;
};

/**
 * Subscribe to /ws/logs and accumulate normalized entries (newest first).
 */
export function useLogsStream(options: UseLogsStreamOptions = {}): UseLogsStreamResult {
  const { maxEntries = 100, seedLimit, onEntry } = options;
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const onEntryRef = useRef(onEntry);
  onEntryRef.current = onEntry;

  useEffect(() => {
    if (!seedLimit || seedLimit <= 0) {
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const resp = await fetchLogTail({ limit: seedLimit });
        if (cancelled || !resp.ok || !resp.result) {
          return;
        }
        const raw = resp.result as { entries?: unknown[]; items?: unknown[] };
        const list = raw.entries ?? raw.items ?? [];
        const normalised = list
          .map((entry) => normalizeLogEntry(entry))
          .filter((entry): entry is LogEntry => entry !== null);
        if (normalised.length === 0) {
          return;
        }
        setEntries((prev) => [...normalised.reverse(), ...prev].slice(0, maxEntries));
      } catch {
        // Best-effort seed; WS will populate as events arrive.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [seedLimit, maxEntries]);

  useEffect(() => {
    const ws = new WebSocket(buildWsUrl("/ws/logs"));
    ws.onopen = () => setWsConnected(true);
    ws.onclose = () => setWsConnected(false);
    ws.onerror = () => setWsConnected(false);
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as LogMessage;
        if (msg.topic !== "manager.log") {
          return;
        }
        const entry = normalizeLogEntry(msg.payload);
        if (!entry) {
          return;
        }
        setEntries((prev) => [entry, ...prev].slice(0, maxEntries));
        const handler = onEntryRef.current;
        if (handler) {
          try {
            handler(entry);
          } catch {
            // Don't let a faulty listener tear down the socket.
          }
        }
      } catch {
        // Ignore malformed frames.
      }
    };
    return () => {
      ws.close();
    };
  }, [maxEntries]);

  return { entries, wsConnected };
}
