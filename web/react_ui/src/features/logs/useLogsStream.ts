import { useEffect, useRef, useState } from "react";
import { buildWsUrl, fetchLogTail } from "../../api";
import { normalizeLogEntry } from "./utils";
import { openReconnectingSocket, reconnectDelayMs } from "../common/reconnectingSocket";
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
  /**
   * Optional callback fired once per coalesced batch (oldest first). Live
   * lines are buffered for `LOG_BATCH_FLUSH_MS` and reconnect catch-up is
   * delivered as one batch, so a burst costs one state update.
   */
  onEntries?: (entries: LogEntry[]) => void;
  /**
   * Keep the in-hook `entries` buffer (default true). Consumers that only
   * use the callbacks should pass false so a log line does not re-render
   * the calling component.
   */
  trackEntries?: boolean;
};

export type UseLogsStreamResult = {
  entries: LogEntry[];
  wsConnected: boolean;
};

/** Upper bound of the logs socket's reconnect delay (before jitter). */
export function logsReconnectDelayMs(attempt: number): number {
  return reconnectDelayMs(attempt, { jitterRatio: 0 });
}

/** Window over which live log lines are coalesced into one dispatch. */
export const LOG_BATCH_FLUSH_MS = 50;

/**
 * Subscribe to /ws/logs and accumulate normalized entries (newest first).
 */
export function useLogsStream(options: UseLogsStreamOptions = {}): UseLogsStreamResult {
  const { maxEntries = 100, seedLimit, onEntry, onEntries, trackEntries = true } = options;
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const onEntryRef = useRef(onEntry);
  onEntryRef.current = onEntry;
  const onEntriesRef = useRef(onEntries);
  onEntriesRef.current = onEntries;
  const trackEntriesRef = useRef(trackEntries);
  trackEntriesRef.current = trackEntries;

  useEffect(() => {
    if (!trackEntries || !seedLimit || seedLimit <= 0) {
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
  }, [seedLimit, maxEntries, trackEntries]);

  useEffect(() => {
    let disposed = false;
    let pending: LogEntry[] = [];
    let flushTimer: ReturnType<typeof setTimeout> | null = null;

    // Dispatch a batch (oldest first): one setEntries, one onEntries.
    const dispatchBatch = (batch: LogEntry[]) => {
      if (disposed || batch.length === 0) {
        return;
      }
      if (trackEntriesRef.current) {
        const newestFirst = batch.slice(-maxEntries).reverse();
        setEntries((prev) => [...newestFirst, ...prev].slice(0, maxEntries));
      }
      const batchHandler = onEntriesRef.current;
      if (batchHandler) {
        try {
          batchHandler(batch);
        } catch {
          // A bad listener must not tear down the socket.
        }
      }
      const handler = onEntryRef.current;
      if (!handler) {
        return;
      }
      for (const entry of batch) {
        try {
          handler(entry);
        } catch {
          continue;
        }
      }
    };

    const flushPending = () => {
      if (flushTimer !== null) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
      const batch = pending;
      pending = [];
      dispatchBatch(batch);
    };

    const enqueueEntry = (entry: LogEntry) => {
      pending.push(entry);
      if (flushTimer === null) {
        flushTimer = setTimeout(flushPending, LOG_BATCH_FLUSH_MS);
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
        const batch = list
          .map((item) => normalizeLogEntry(item))
          .filter((entry): entry is LogEntry => entry !== null);
        // Catch-up rows are older than any live lines buffered meanwhile;
        // deliver both in one chronological batch.
        if (flushTimer !== null) {
          clearTimeout(flushTimer);
          flushTimer = null;
        }
        const live = pending;
        pending = [];
        dispatchBatch([...batch, ...live]);
      } catch {
        return;
      }
    };

    const socket = openReconnectingSocket({
      url: buildWsUrl("/ws/logs"),
      onOpen: ({ reconnect }) => {
        setWsConnected(true);
        if (reconnect) {
          void catchUp();
        }
      },
      onClose: () => setWsConnected(false),
      onMessage: (event) => {
        try {
          const msg = JSON.parse(event.data) as LogMessage;
          if (msg.topic !== "manager.log") {
            return;
          }
          const entry = normalizeLogEntry(msg.payload);
          if (entry) {
            enqueueEntry(entry);
          }
        } catch {
          return;
        }
      },
    });

    return () => {
      // Deliver lines still waiting in the batch window before teardown.
      flushPending();
      disposed = true;
      socket.close();
    };
  }, [maxEntries]);

  return { entries, wsConnected };
}
