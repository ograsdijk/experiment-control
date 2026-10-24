import { describe, expect, it, vi } from "vitest";

import type { LogEntry } from "../../types";
import { LogRowStore } from "./logRowStore";

function entry(n: number, severity = "info"): LogEntry {
  return { severity, message: `line ${n}`, ts: { t_mono: n, t_wall: n } };
}

describe("LogRowStore", () => {
  it("dedups and keeps rows oldest first", () => {
    const store = new LogRowStore(10);
    expect(store.append([entry(1), entry(2)])).toHaveLength(2);
    expect(store.append([entry(2), entry(3)])).toEqual([entry(3)]);
    expect(store.getSnapshot().map((row) => row.message)).toEqual([
      "line 1",
      "line 2",
      "line 3",
    ]);
  });

  it("trims to the cap and forgets only the evicted keys", () => {
    const store = new LogRowStore(3);
    store.append([entry(1), entry(2), entry(3)]);
    store.append([entry(4), entry(5)]);
    expect(store.getSnapshot().map((row) => row.ts?.t_mono)).toEqual([3, 4, 5]);
    // Evicted rows are accepted again; retained rows are still deduped.
    expect(store.has(entry(1))).toBe(false);
    expect(store.has(entry(3))).toBe(true);
    expect(store.append([entry(5)])).toEqual([]);
    expect(store.size).toBe(3);
  });

  it("keeps the newest rows of an oversized batch", () => {
    const store = new LogRowStore(2);
    store.append([entry(1), entry(2), entry(3)]);
    expect(store.getSnapshot().map((row) => row.ts?.t_mono)).toEqual([2, 3]);
    expect(store.has(entry(1))).toBe(false);
  });

  it("notifies once per accepted batch and keeps the snapshot stable", () => {
    const store = new LogRowStore(10);
    const listener = vi.fn();
    store.subscribe(listener);
    store.append([entry(1), entry(2)]);
    expect(listener).toHaveBeenCalledTimes(1);
    const first = store.getSnapshot();
    expect(store.getSnapshot()).toBe(first);
    store.append([entry(1)]);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot()).toBe(first);
    store.append([entry(3)]);
    expect(store.getSnapshot()).not.toBe(first);
  });

  it("replace and clear reset the dedup set", () => {
    const store = new LogRowStore(10);
    store.append([entry(1)]);
    store.replace([entry(2), entry(3)]);
    expect(store.getSnapshot().map((row) => row.ts?.t_mono)).toEqual([2, 3]);
    expect(store.has(entry(1))).toBe(false);
    store.clear();
    expect(store.getSnapshot()).toEqual([]);
    expect(store.append([entry(2)])).toHaveLength(1);
  });
});
