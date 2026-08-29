// @vitest-environment jsdom

import { act, createElement, type MutableRefObject } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  distanceToFollowEdge,
  isNearFollowEdge,
  useFollowEdgeScroll,
  type ScrollMetrics,
} from "./useFollowEdgeScroll";

function metrics(
  scrollTop: number,
  scrollHeight = 1000,
  clientHeight = 200
): ScrollMetrics {
  return { scrollTop, scrollHeight, clientHeight };
}

type HarnessProps = {
  viewportRef: MutableRefObject<HTMLDivElement | null>;
  opened: boolean;
  enabled: boolean;
  newestFirst: boolean;
  itemKeys: readonly string[];
  resetKey?: string;
};

function Harness(props: HarnessProps) {
  useFollowEdgeScroll(props);
  return null;
}

function domRect(top: number, height: number): DOMRect {
  return {
    x: 0,
    y: top,
    top,
    right: 400,
    bottom: top + height,
    left: 0,
    width: 400,
    height,
    toJSON: () => ({}),
  };
}

describe("follow-edge scrolling", () => {
  let root: Root;
  let container: HTMLDivElement;
  let host: HTMLDivElement;
  let anchorRow: HTMLDivElement;
  let viewportRef: MutableRefObject<HTMLDivElement | null>;
  let scrollTop: number;
  let rowTop: number;
  let nextFrameId: number;
  let frames: Map<number, FrameRequestCallback>;

  const renderHarness = (props: Omit<HarnessProps, "viewportRef">) => {
    act(() => {
      root.render(createElement(Harness, { ...props, viewportRef }));
    });
  };

  const flushFrames = () => {
    const pending = [...frames.values()];
    frames.clear();
    act(() => {
      pending.forEach((callback) => callback(0));
    });
  };

  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
      true;
    container = document.createElement("div");
    host = document.createElement("div");
    anchorRow = document.createElement("div");
    anchorRow.dataset.virtualRowKey = "old";
    host.append(anchorRow);
    document.body.append(container, host);
    viewportRef = { current: host };
    scrollTop = 0;
    rowTop = 0;
    nextFrameId = 1;
    frames = new Map();

    Object.defineProperties(host, {
      clientHeight: { configurable: true, get: () => 200 },
      scrollHeight: { configurable: true, get: () => 1000 },
      scrollTop: {
        configurable: true,
        get: () => scrollTop,
        set: (value: number) => {
          scrollTop = Math.max(0, Math.min(800, value));
        },
      },
    });
    host.getBoundingClientRect = () => domRect(0, 200);
    anchorRow.getBoundingClientRect = () => domRect(rowTop, 40);
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      const id = nextFrameId;
      nextFrameId += 1;
      frames.set(id, callback);
      return id;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => {
      frames.delete(id);
    });
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    host.remove();
    vi.unstubAllGlobals();
  });

  it("measures both follow edges and applies the configured threshold", () => {
    expect(distanceToFollowEdge(metrics(37), true)).toBe(37);
    expect(distanceToFollowEdge(metrics(760), false)).toBe(40);
    expect(isNearFollowEdge(metrics(24), true)).toBe(true);
    expect(isNearFollowEdge(metrics(25), true)).toBe(false);
    expect(isNearFollowEdge(metrics(776), false)).toBe(true);
    expect(isNearFollowEdge(metrics(775), false)).toBe(false);
    expect(isNearFollowEdge(metrics(8), true, 8)).toBe(true);
    expect(isNearFollowEdge(metrics(9), true, 8)).toBe(false);
  });

  it("preserves the visible row when auto-scroll is disabled", () => {
    scrollTop = 100;
    rowTop = 10;
    renderHarness({
      opened: true,
      enabled: false,
      newestFirst: true,
      itemKeys: ["old"],
    });
    flushFrames();

    rowTop = 42;
    renderHarness({
      opened: true,
      enabled: false,
      newestFirst: true,
      itemKeys: ["new", "old"],
    });
    flushFrames();

    expect(scrollTop).toBe(132);
  });

  it("pauses away from the edge and resumes after returning", () => {
    renderHarness({
      opened: true,
      enabled: true,
      newestFirst: true,
      itemKeys: ["old"],
    });
    flushFrames();

    scrollTop = 100;
    rowTop = 10;
    act(() => host.dispatchEvent(new Event("scroll")));
    rowTop = 35;
    renderHarness({
      opened: true,
      enabled: true,
      newestFirst: true,
      itemKeys: ["new", "old"],
    });
    flushFrames();
    expect(scrollTop).toBe(125);

    scrollTop = 0;
    rowTop = 0;
    act(() => host.dispatchEvent(new Event("scroll")));
    renderHarness({
      opened: true,
      enabled: true,
      newestFirst: true,
      itemKeys: ["newer", "new", "old"],
    });
    flushFrames();
    expect(scrollTop).toBe(0);
  });

  it("follows the bottom when an oldest-first viewer opens", () => {
    renderHarness({
      opened: true,
      enabled: true,
      newestFirst: false,
      itemKeys: ["old"],
      resetKey: "oldest",
    });

    // Passive observation sees the initial top position before this frame.
    // The captured reset decision must nevertheless follow the bottom.
    flushFrames();
    expect(scrollTop).toBe(800);
  });

  it("follows the new edge after the sort direction changes", () => {
    renderHarness({
      opened: true,
      enabled: true,
      newestFirst: true,
      itemKeys: ["old"],
      resetKey: "newest",
    });
    flushFrames();
    expect(scrollTop).toBe(0);

    renderHarness({
      opened: true,
      enabled: true,
      newestFirst: false,
      itemKeys: ["old"],
      resetKey: "oldest",
    });
    flushFrames();
    expect(scrollTop).toBe(800);
  });
});
