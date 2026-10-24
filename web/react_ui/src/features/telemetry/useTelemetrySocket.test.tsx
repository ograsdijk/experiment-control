// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchTelemetrySnapshot = vi.fn();
vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  fetchTelemetrySnapshot: () => fetchTelemetrySnapshot(),
}));

import { TelemetryLatestStore } from "./TelemetryLatestStore";
import {
  TELEMETRY_RECONNECT_DELAY_MS,
  useTelemetrySocket,
} from "./useTelemetrySocket";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  close(): void {
    this.onclose?.();
  }

  emitClose(): void {
    this.onclose?.();
  }
}

describe("useTelemetrySocket", () => {
  const originalWebSocket = globalThis.WebSocket;
  const roots: Array<ReturnType<typeof createRoot>> = [];

  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;
  });

  afterEach(() => {
    for (const root of roots.splice(0)) act(() => root.unmount());
    globalThis.WebSocket = originalWebSocket;
    vi.useRealTimers();
  });

  it("reconnects after close without leaving a timer after unmount", () => {
    const store = new TelemetryLatestStore();
    function SocketProbe() {
      useTelemetrySocket({ hydrate: false, store });
      return null;
    }

    const root = createRoot(document.createElement("div"));
    roots.push(root);
    act(() => root.render(createElement(SocketProbe)));
    expect(FakeWebSocket.instances).toHaveLength(1);

    act(() => FakeWebSocket.instances[0].emitClose());
    act(() => vi.advanceTimersByTime(TELEMETRY_RECONNECT_DELAY_MS));
    expect(FakeWebSocket.instances).toHaveLength(2);

    act(() => root.unmount());
    roots.splice(roots.indexOf(root), 1);
    act(() => vi.advanceTimersByTime(TELEMETRY_RECONNECT_DELAY_MS * 2));
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("refetches the snapshot on reconnect without clobbering newer values", async () => {
    const store = new TelemetryLatestStore();
    fetchTelemetrySnapshot.mockReset();
    fetchTelemetrySnapshot
      .mockResolvedValueOnce({ dev: { v: { value: 1, ts: { t_wall: 10 } } } })
      .mockResolvedValueOnce({
        dev: {
          v: { value: 2, ts: { t_wall: 20 } },
          w: { value: 5, ts: { t_wall: 20 } },
        },
      });
    function SocketProbe() {
      useTelemetrySocket({ store });
      return null;
    }
    const root = createRoot(document.createElement("div"));
    roots.push(root);
    await act(async () => root.render(createElement(SocketProbe)));
    expect(fetchTelemetrySnapshot).toHaveBeenCalledTimes(1);
    act(() => FakeWebSocket.instances[0].onopen?.());
    // First open is not a reconnect: the mount snapshot covers it.
    expect(fetchTelemetrySnapshot).toHaveBeenCalledTimes(1);

    act(() => FakeWebSocket.instances[0].emitClose());
    act(() => vi.advanceTimersByTime(TELEMETRY_RECONNECT_DELAY_MS));
    // A live value newer than the snapshot arrives first.
    store.applyMessage({
      topic: "manager.telemetry_update",
      payload: { device_id: "dev", signals: { v: { value: 3, ts: { t_wall: 30 } } } },
    } as never);
    await act(async () => {
      FakeWebSocket.instances[1].onopen?.();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(fetchTelemetrySnapshot).toHaveBeenCalledTimes(2);
    expect(store.getDevice("dev").v?.value).toBe(3);
    expect(store.getDevice("dev").w?.value).toBe(5);
  });
});
