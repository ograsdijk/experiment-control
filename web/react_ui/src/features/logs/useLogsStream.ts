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

export function logsReconnectDelayMs(attempt: number): number {
  return Math.min(1000 * 2 ** Math.max(0, attempt), 30000);
}

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
    let disposed = false;
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectAttempt = 0;
    let needsCatchUp = false;

    const dispatchEntry = (entry: LogEntry) => {
      setEntries((prev) => [entry, ...prev].slice(0, maxEntries));
      const handler = onEntryRef.current;
      if (!handler) {
        return;
      }
      try {
        handler(entry);
      } catch {
        return;
      }
    };

    const catchUp = async () => {
      try {
        const resp = await fetchLogTail({ limit: maxEntries });
        if (disposed || !resp.ok || !resp.result) {
          return;
        }
        const raw = resp.result as { entries?: unknown[]; items?: unknown[] };
        const list = raw.entries ?? raw.items ?? [];
        for (const item of list) {
          const entry = normalizeLogEntry(item);
          if (entry) {
            dispatchEntry(entry);
          }
        }
      } catch {
        return;
      }
    };

    const connect = () => {
      if (disposed) {
        return;
      }
      ws = new WebSocket(buildWsUrl("/ws/logs"));
      ws.onopen = () => {
        reconnectAttempt = 0;
        setWsConnected(true);
        if (needsCatchUp) {
          needsCatchUp = false;
          void catchUp();
        }
      };
      ws.onclose = () => {
        setWsConnected(false);
        if (disposed) {
          return;
        }
        needsCatchUp = true;
        const delay = logsReconnectDelayMs(reconnectAttempt);
        reconnectAttempt += 1;
        reconnectTimer = setTimeout(connect, delay);
      };
      ws.onerror = () => setWsConnected(false);
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as LogMessage;
          if (msg.topic !== "manager.log") {
            return;
          }
          const entry = normalizeLogEntry(msg.payload);
          if (entry) {
            dispatchEntry(entry);
          }
        } catch {
          return;
        }
      };
    };

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
      }
      ws?.close();
    };
  }, [maxEntries]);

  return { entries, wsConnected };
}
