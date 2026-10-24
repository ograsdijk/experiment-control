// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchLogTail = vi.fn();
vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  fetchLogTail: (...args: unknown[]) => fetchLogTail(...args),
}));

import type { LogEntry } from "../../types";
import {
  LOG_BATCH_FLUSH_MS,
  logsReconnectDelayMs,
  useLogsStream,
  type UseLogsStreamOptions,
  type UseLogsStreamResult,
} from "./useLogsStream";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  close(): void {
    this.onclose?.();
  }

  emit(message: string): void {
    this.onmessage?.({
      data: JSON.stringify({
        topic: "manager.log",
        payload: { severity: "info", message, ts: { t_mono: Number(message) } },
      }),
    });
  }
}

describe("useLogsStream", () => {
  const originalWebSocket = globalThis.WebSocket;
  let root: ReturnType<typeof createRoot>;
  let result: UseLogsStreamResult | null = null;
  let renders = 0;

  function Probe(props: UseLogsStreamOptions) {
    result = useLogsStream(props);
    renders += 1;
    return null;
  }

  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;
    fetchLogTail.mockReset();
    root = createRoot(document.createElement("div"));
    result = null;
    renders = 0;
  });

  afterEach(() => {
    act(() => root.unmount());
    globalThis.WebSocket = originalWebSocket;
    vi.useRealTimers();
  });

  it("coalesces a burst of lines into one batch", () => {
    const onEntries = vi.fn<(entries: LogEntry[]) => void>();
    const onEntry = vi.fn();
    act(() => root.render(createElement(Probe, { onEntries, onEntry, maxEntries: 3 })));
    act(() => FakeWebSocket.instances[0].onopen?.());
    const before = renders;
    act(() => {
      for (const n of ["1", "2", "3", "4"]) FakeWebSocket.instances[0].emit(n);
    });
    expect(onEntries).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(LOG_BATCH_FLUSH_MS));
    expect(onEntries).toHaveBeenCalledTimes(1);
    expect(onEntries.mock.calls[0][0].map((entry) => entry.message)).toEqual([
      "1",
      "2",
      "3",
      "4",
    ]);
    expect(onEntry).toHaveBeenCalledTimes(4);
    // Newest first, capped at maxEntries, in a single render.
    expect(result?.entries.map((entry) => entry.message)).toEqual(["4", "3", "2"]);
    expect(renders - before).toBe(1);
  });

  it("delivers lines still in the batch window when it unmounts", () => {
    const onEntries = vi.fn<(entries: LogEntry[]) => void>();
    act(() => root.render(createElement(Probe, { onEntries, trackEntries: false })));
    act(() => FakeWebSocket.instances[0].onopen?.());
    act(() => FakeWebSocket.instances[0].emit("9"));
    expect(onEntries).not.toHaveBeenCalled();
    act(() => root.unmount());
    root = createRoot(document.createElement("div"));
    expect(onEntries).toHaveBeenCalledTimes(1);
    expect(onEntries.mock.calls[0][0].map((entry) => entry.message)).toEqual(["9"]);
  });

  it("does not re-render per line when trackEntries is false", () => {
    const onEntries = vi.fn();
    act(() => root.render(createElement(Probe, { onEntries, trackEntries: false })));
    act(() => FakeWebSocket.instances[0].onopen?.());
    const before = renders;
    act(() => {
      FakeWebSocket.instances[0].emit("1");
      vi.advanceTimersByTime(LOG_BATCH_FLUSH_MS);
    });
    expect(onEntries).toHaveBeenCalledTimes(1);
    expect(renders).toBe(before);
    expect(result?.entries).toEqual([]);
  });

  it("delivers the reconnect catch-up as one batch", async () => {
    fetchLogTail.mockResolvedValue({
      ok: true,
      result: {
        entries: [
          { severity: "info", message: "a", ts: { t_mono: 1 } },
          { severity: "info", message: "b", ts: { t_mono: 2 } },
        ],
      },
    });
    const onEntries = vi.fn<(entries: LogEntry[]) => void>();
    act(() => root.render(createElement(Probe, { onEntries, trackEntries: false })));
    act(() => FakeWebSocket.instances[0].onopen?.());
    act(() => FakeWebSocket.instances[0].onclose?.());
    act(() => vi.advanceTimersByTime(logsReconnectDelayMs(0)));
    expect(FakeWebSocket.instances).toHaveLength(2);
    await act(async () => {
      FakeWebSocket.instances[1].onopen?.();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(fetchLogTail).toHaveBeenCalledTimes(1);
    expect(onEntries).toHaveBeenCalledTimes(1);
    expect(onEntries.mock.calls[0][0].map((entry) => entry.message)).toEqual(["a", "b"]);
  });
});
