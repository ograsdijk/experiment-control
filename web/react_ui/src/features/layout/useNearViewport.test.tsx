// @vitest-environment jsdom

import { act, createElement, useRef } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useNearViewport } from "./useNearViewport";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

describe("useNearViewport", () => {
  const originalObserver = globalThis.IntersectionObserver;
  const mountedRoots: Array<ReturnType<typeof createRoot>> = [];

  afterEach(() => {
    for (const root of mountedRoots.splice(0)) act(() => root.unmount());
    globalThis.IntersectionObserver = originalObserver;
  });

  it("tracks intersection with the configured preload margin and disconnects", () => {
    let callback: IntersectionObserverCallback | null = null;
    const observe = vi.fn();
    const disconnect = vi.fn();
    const observer = vi.fn(function (
      this: IntersectionObserver,
      nextCallback: IntersectionObserverCallback,
      options?: IntersectionObserverInit
    ) {
      callback = nextCallback;
      expect(options?.rootMargin).toBe("300px 0px");
      return { observe, disconnect } as unknown as IntersectionObserver;
    });
    globalThis.IntersectionObserver = observer as unknown as typeof IntersectionObserver;

    function View() {
      const ref = useRef<HTMLDivElement | null>(null);
      const visible = useNearViewport(ref);
      return createElement("div", { ref }, String(visible));
    }

    const container = document.createElement("div");
    const root = createRoot(container);
    mountedRoots.push(root);
    act(() => root.render(createElement(View)));

    expect(observe).toHaveBeenCalledOnce();
    expect(container.textContent).toBe("false");
    act(() => callback?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(container.textContent).toBe("true");
    act(() => callback?.([{ isIntersecting: false } as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(container.textContent).toBe("false");

    act(() => root.unmount());
    mountedRoots.pop();
    expect(disconnect).toHaveBeenCalledOnce();
  });

  it("observes against the given scroll container", () => {
    const roots: Array<Element | Document | null | undefined> = [];
    const observer = vi.fn(function (
      this: IntersectionObserver,
      _callback: IntersectionObserverCallback,
      options?: IntersectionObserverInit
    ) {
      roots.push(options?.root);
      return { observe: vi.fn(), disconnect: vi.fn() } as unknown as IntersectionObserver;
    });
    globalThis.IntersectionObserver = observer as unknown as typeof IntersectionObserver;
    const scroller = document.createElement("div");

    function View() {
      const ref = useRef<HTMLDivElement | null>(null);
      const rootRef = useRef<Element | null>(scroller);
      return createElement("div", { ref }, String(useNearViewport(ref, "400px 0px", rootRef)));
    }

    const container = document.createElement("div");
    const root = createRoot(container);
    mountedRoots.push(root);
    act(() => root.render(createElement(View)));
    expect(roots).toEqual([scroller]);
  });

  it("measures before paint when asked, before the observer reports", () => {
    globalThis.IntersectionObserver = vi.fn(function () {
      return { observe: vi.fn(), disconnect: vi.fn() } as unknown as IntersectionObserver;
    }) as unknown as typeof IntersectionObserver;
    const scroller = document.createElement("div");
    scroller.getBoundingClientRect = () => ({ top: 0, bottom: 500, left: 0, right: 300 }) as DOMRect;
    const rect = (top: number, height: number) => () =>
      ({ top, bottom: top + height, left: 0, right: 300, width: height ? 300 : 0, height }) as DOMRect;

    function Row({ top, height }: { top: number; height: number }) {
      const ref = useRef<HTMLDivElement | null>(null);
      const rootRef = useRef<Element | null>(scroller);
      const callbackRef = (node: HTMLDivElement | null) => {
        if (node) node.getBoundingClientRect = rect(top, height);
        ref.current = node;
      };
      return createElement("span", { ref: callbackRef }, String(useNearViewport(ref, "400px 0px", rootRef, true)));
    }

    const container = document.createElement("div");
    const root = createRoot(container);
    mountedRoots.push(root);
    act(() =>
      root.render(
        createElement("div", null, [
          createElement(Row, { key: "in", top: 100, height: 40 }),
          createElement(Row, { key: "margin", top: 850, height: 40 }),
          createElement(Row, { key: "far", top: 2000, height: 40 }),
          createElement(Row, { key: "hidden", top: 0, height: 0 }),
        ])
      )
    );
    // The observer never reported: these come from the measurement alone.
    expect([...container.querySelectorAll("span")].map((span) => span.textContent)).toEqual([
      "true",
      "true",
      "false",
      "false",
    ]);
  });

  it("fails open when IntersectionObserver is unavailable", () => {
    globalThis.IntersectionObserver = undefined as unknown as typeof IntersectionObserver;

    function View() {
      const ref = useRef<HTMLDivElement | null>(null);
      return createElement("div", { ref }, String(useNearViewport(ref)));
    }

    const container = document.createElement("div");
    const root = createRoot(container);
    mountedRoots.push(root);
    act(() => root.render(createElement(View)));
    expect(container.textContent).toBe("true");
  });
});
