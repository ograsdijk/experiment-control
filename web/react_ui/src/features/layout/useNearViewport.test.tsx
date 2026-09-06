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
