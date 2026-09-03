import { describe, expect, it, vi } from "vitest";

import { PanelInvalidationStore } from "./PanelInvalidationStore";

function harness() {
  const frames: FrameRequestCallback[] = [];
  const store = new PanelInvalidationStore((callback) => {
    frames.push(callback);
    return frames.length;
  }, () => undefined);
  return { store, flush: (now = 1000) => frames.shift()?.(now) };
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
});
