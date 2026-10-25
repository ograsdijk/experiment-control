import { describe, expect, it, vi } from "vitest";

import {
  DEFAULT_PLOT_MAX_FPS,
  MIN_TIME_SERIES_FPS,
  PanelInvalidationStore,
  timeSeriesMaxFps,
} from "./PanelInvalidationStore";

function harness() {
  const frames: FrameRequestCallback[] = [];
  const timers: { callback: () => void; delayMs: number }[] = [];
  const store = new PanelInvalidationStore(
    (callback) => {
      frames.push(callback);
      return frames.length;
    },
    () => undefined,
    (callback, delayMs) => {
      timers.push({ callback, delayMs });
      return timers.length;
    },
    () => undefined
  );
  // A frame at `now`; a pending wake-up timer fires first (as it would before
  // the frame it was armed for).
  const flush = (now = 1000) => {
    timers.splice(0).forEach((timer) => timer.callback());
    frames.shift()?.(now);
  };
  return { store, flush, frames, timers };
}

describe("PanelInvalidationStore", () => {
  it("notifies only dirty panels and coalesces repeated requests", () => {
    const { store, flush } = harness();
    const a = vi.fn();
    const b = vi.fn();
    store.subscribe("A", a);
    store.subscribe("B", b);
    for (let index = 0; index < 50; index += 1) store.markPanelDirty("A");
    flush();
    expect(a).toHaveBeenCalledOnce();
    expect(b).not.toHaveBeenCalled();
  });

  it("deduplicates a batch and updates both regular and expanded listeners", () => {
    const { store, flush } = harness();
    const regularA = vi.fn();
    const expandedA = vi.fn();
    const c = vi.fn();
    const d = vi.fn();
    store.subscribe("A", regularA);
    store.subscribe("A", expandedA);
    store.subscribe("C", c);
    store.subscribe("D", d);
    store.markPanelsDirty(["A", "C", "C", "D"]);
    flush();
    expect([regularA.mock.calls.length, expandedA.mock.calls.length, c.mock.calls.length, d.mock.calls.length]).toEqual([1, 1, 1, 1]);
  });

  it("cleans panel state and defers notifications while hidden", () => {
    const { store, flush } = harness();
    const listener = vi.fn();
    store.subscribe("A", listener);
    store.setHidden(true);
    store.markPanelDirty("A");
    flush();
    expect(listener).not.toHaveBeenCalled();
    store.setHidden(false);
    flush(2000);
    expect(listener).toHaveBeenCalledOnce();
    store.removePanel("A");
    expect(store.getRevision("A")).toBe(0);
  });

  it("keeps offscreen data dirty and catches up once visible", () => {
    const { store, flush } = harness();
    const listener = vi.fn();
    store.subscribe("A", listener);
    store.setPanelVisible("A", "card", false);
    store.markPanelDirty("A");
    flush();
    expect(listener).not.toHaveBeenCalled();
    store.setPanelVisible("A", "card", true);
    flush(2000);
    expect(listener).toHaveBeenCalledOnce();
  });

  it("coalesces data while a panel waits for its FPS ceiling", () => {
    const { store, flush } = harness();
    const listener = vi.fn();
    store.subscribe("A", listener);
    store.setPanelMaxFps("A", 10);
    store.markPanelDirty("A");
    flush(1000);
    store.markPanelDirty("A");
    store.markPanelDirty("A");
    flush(1050);
    expect(listener).toHaveBeenCalledOnce();
    flush(1100);
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("waits on a timer, not every vsync, while a panel is rate limited", () => {
    const { store, frames, timers } = harness();
    const listener = vi.fn();
    store.subscribe("A", listener);
    store.setPanelMaxFps("A", 5);
    store.markPanelDirty("A");
    frames.shift()?.(1000);
    store.markPanelDirty("A");
    frames.shift()?.(1016);
    // Due at 1200: no frame is re-armed in between, one timer wakes it early.
    expect(frames).toHaveLength(0);
    expect(timers).toHaveLength(1);
    expect(timers[0].delayMs).toBeGreaterThan(170);
    expect(timers[0].delayMs).toBeLessThan(184);
    store.markPanelDirty("A");
    expect(frames).toHaveLength(0);
    timers.shift()?.callback();
    frames.shift()?.(1200);
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("still uses the next frame when a panel is due within one frame", () => {
    const { store, frames, timers } = harness();
    const listener = vi.fn();
    store.subscribe("A", listener);
    store.markPanelDirty("A");
    frames.shift()?.(1000);
    store.markPanelDirty("A");
    frames.shift()?.(1025);
    expect(timers).toHaveLength(0);
    expect(frames).toHaveLength(1);
    frames.shift()?.(1034);
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("cancels a pending wake-up when the page is hidden", () => {
    const cancelTimeout = vi.fn();
    const frames: FrameRequestCallback[] = [];
    const store = new PanelInvalidationStore(
      (callback) => frames.push(callback),
      () => undefined,
      () => 7,
      cancelTimeout
    );
    store.subscribe("A", vi.fn());
    store.setPanelMaxFps("A", 2);
    store.markPanelDirty("A");
    frames.shift()?.(1000);
    store.markPanelDirty("A");
    frames.shift()?.(1016);
    store.setHidden(true);
    expect(cancelTimeout).toHaveBeenCalledWith(7);
  });
});

describe("timeSeriesMaxFps", () => {
  it("redraws about once per pixel of x-axis advance", () => {
    expect(timeSeriesMaxFps(1200, 120)).toBe(10);
    expect(timeSeriesMaxFps(400, 30)).toBe(14);
  });

  it("clamps slow windows to the floor and fast windows to the default ceiling", () => {
    expect(timeSeriesMaxFps(400, 120)).toBe(MIN_TIME_SERIES_FPS);
    expect(timeSeriesMaxFps(800, 5)).toBe(DEFAULT_PLOT_MAX_FPS);
  });

  it("falls back to the default before the plot has been measured", () => {
    expect(timeSeriesMaxFps(0, 120)).toBe(DEFAULT_PLOT_MAX_FPS);
    expect(timeSeriesMaxFps(400, 0)).toBe(DEFAULT_PLOT_MAX_FPS);
  });
});
