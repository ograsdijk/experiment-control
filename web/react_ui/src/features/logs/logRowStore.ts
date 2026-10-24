import { useSyncExternalStore } from "react";

import type { LogEntry } from "../../types";
import { logEntryKey } from "./utils";

/**
 * Bounded, deduplicating log roster held outside React state.
 *
 * App.tsx appends every live log line here. Keeping the roster out of
 * context state means a log line no longer re-renders App: only the Logs
 * modal subscribes (via `useLogRowsSnapshot`), and it is mounted only while
 * open. Rows are stored oldest→newest with a parallel key array, so trimming
 * evicts exactly the dropped keys from the dedup set instead of rebuilding
 * it from the retained rows.
 */
export class LogRowStore {
  private rows: LogEntry[] = [];
  private keys: string[] = [];
  private seen = new Set<string>();
  private snapshot: readonly LogEntry[] = [];
  private snapshotDirty = false;
  private readonly listeners = new Set<() => void>();

  constructor(private readonly maxRows: number) {}

  /** Append entries (oldest first); returns only the newly accepted ones. */
  append(entries: readonly LogEntry[]): LogEntry[] {
    const accepted: LogEntry[] = [];
    for (const entry of entries) {
      const key = logEntryKey(entry);
      if (this.seen.has(key)) {
        continue;
      }
      this.seen.add(key);
      this.rows.push(entry);
      this.keys.push(key);
      accepted.push(entry);
    }
    if (accepted.length === 0) {
      return accepted;
    }
    const overflow = this.rows.length - this.maxRows;
    if (overflow > 0) {
      this.rows.splice(0, overflow);
      for (const key of this.keys.splice(0, overflow)) {
        this.seen.delete(key);
      }
    }
    this.markChanged();
    return accepted;
  }

  /** Replace the roster (e.g. a fresh tail fetch), keeping the newest rows. */
  replace(entries: readonly LogEntry[]): void {
    this.rows = [];
    this.keys = [];
    this.seen = new Set();
    this.append(entries);
    this.markChanged();
  }

  clear(): void {
    this.replace([]);
  }

  get size(): number {
    return this.rows.length;
  }

  has(entry: LogEntry): boolean {
    return this.seen.has(logEntryKey(entry));
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /** Stable between changes, so it is safe for useSyncExternalStore. */
  getSnapshot = (): readonly LogEntry[] => {
    if (this.snapshotDirty) {
      this.snapshot = this.rows.slice();
      this.snapshotDirty = false;
    }
    return this.snapshot;
  };

  private markChanged(): void {
    this.snapshotDirty = true;
    for (const listener of [...this.listeners]) {
      listener();
    }
  }
}

/** Subscribe to the store's rows (oldest→newest). */
export function useLogRowsSnapshot(store: LogRowStore): readonly LogEntry[] {
  return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
}
