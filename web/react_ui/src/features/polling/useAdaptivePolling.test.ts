// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAdaptivePolling, type AdaptivePollingOptions } from "./useAdaptivePolling";

function Harness<T>(props: AdaptivePollingOptions<T>) {
  useAdaptivePolling(props);
  return null;
}

describe("useAdaptivePolling", () => {
  let container: HTMLDivElement;
  let root: Root;
  let pageHidden = false;

  const render = <T,>(props: AdaptivePollingOptions<T>) => {
    act(() => root.render(createElement(Harness<T>, props)));
  };
  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  beforeEach(() => {
    vi.useFakeTimers();
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    pageHidden = false;
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => pageHidden,
    });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  it("schedules after completion and never overlaps a slow request", async () => {
    const deferred: { resolve?: (value: number) => void } = {};
    const poll = vi
      .fn<(signal: AbortSignal) => Promise<number>>()
      .mockImplementationOnce(() => new Promise((resolve) => (deferred.resolve = resolve)))
      .mockResolvedValue(2);
    render({ enabled: true, intervalMs: 10, poll, onValue: vi.fn() });
    await flush();
    await act(async () => vi.advanceTimersByTime(100));
    expect(poll).toHaveBeenCalledTimes(1);
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(poll).toHaveBeenCalledTimes(1);
    deferred.resolve?.(1);
    await flush();
    expect(poll).toHaveBeenCalledTimes(2);
    await flush();
    await act(async () => vi.advanceTimersByTime(10));
    await flush();
    expect(poll).toHaveBeenCalledTimes(3);
  });

  it("does not poll while disabled or hidden and refreshes when visible", async () => {
    const poll = vi.fn().mockResolvedValue(1);
    render({ enabled: false, intervalMs: 10, poll, onValue: vi.fn() });
    await flush();
    expect(poll).not.toHaveBeenCalled();
    pageHidden = true;
    render({ enabled: true, intervalMs: 10, poll, onValue: vi.fn() });
    await flush();
    expect(poll).not.toHaveBeenCalled();
    pageHidden = false;
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await flush();
    expect(poll).toHaveBeenCalledTimes(1);
  });

  it("polls once without scheduling when the interval is non-positive", async () => {
    const poll = vi.fn().mockResolvedValue(1);
    render({ enabled: true, intervalMs: 0, poll, onValue: vi.fn() });
    await flush();
    await act(async () => vi.advanceTimersByTime(10_000));
    expect(poll).toHaveBeenCalledTimes(1);
  });

  it("suppresses equal values at the state-update boundary", async () => {
    const onValue = vi.fn();
    render({
      enabled: true,
      intervalMs: 10,
      poll: vi.fn().mockResolvedValue({ revision: 1 }),
      onValue,
      equality: (a, b) => a.revision === b.revision,
    });
    await flush();
    await act(async () => vi.advanceTimersByTime(10));
    await flush();
    expect(onValue).toHaveBeenCalledTimes(1);
  });

  it("aborts an in-flight request on unmount", async () => {
    const observed: { signal?: AbortSignal } = {};
    render({
      enabled: true,
      intervalMs: 10,
      poll: (signal) => {
        observed.signal = signal;
        return new Promise<number>(() => undefined);
      },
      onValue: vi.fn(),
    });
    await flush();
    act(() => root.unmount());
    expect(observed.signal?.aborted).toBe(true);
    root = createRoot(container);
  });
});
