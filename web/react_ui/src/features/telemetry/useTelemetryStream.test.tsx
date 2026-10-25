// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchTelemetrySnapshot = vi.fn();
vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  fetchTelemetrySnapshot: () => fetchTelemetrySnapshot(),
}));

import {
  telemetrySocketPath,
  useTelemetryStream,
  type UseTelemetryStreamResult,
} from "./useTelemetryStream";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

  emit(deviceId: string, signals: Record<string, unknown>): void {
    const data = JSON.stringify({
      topic: "manager.telemetry",
      payload: { device_id: deviceId, signals },
    });
    this.onmessage?.({ data } as MessageEvent);
  }
}

function signal(value: number) {
  return { value, t: 0 };
}

describe("useTelemetryStream", () => {
  const originalWebSocket = globalThis.WebSocket;
  let root: Root;
  let renders = 0;
  let latest: UseTelemetryStreamResult | null = null;
  const onMessage = vi.fn();

  let probeOptions: Parameters<typeof useTelemetryStream>[0] = {};
  function Probe() {
    renders += 1;
    latest = useTelemetryStream({ onMessage, ...probeOptions });
    return null;
  }
  function mount(options: Parameters<typeof useTelemetryStream>[0]) {
    probeOptions = options;
    act(() => root.render(createElement(Probe)));
  }

  const socket = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  const frame = () => act(() => vi.advanceTimersByTime(20));

  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;
    fetchTelemetrySnapshot.mockReset();
    fetchTelemetrySnapshot.mockResolvedValue({});
    onMessage.mockReset();
    renders = 0;
    latest = null;
    root = createRoot(document.createElement("div"));
  });

  afterEach(() => {
    act(() => root.unmount());
    globalThis.WebSocket = originalWebSocket;
    vi.useRealTimers();
  });

  it("commits a burst of messages in one render and still reports each one", () => {
    mount({ hydrate: false });
    act(() => socket().onopen?.());
    const before = renders;
    act(() => {
      socket().emit("a", { x: signal(1) });
      socket().emit("a", { x: signal(2), y: signal(5) });
      socket().emit("b", { z: signal(3) });
    });
    expect(renders).toBe(before);
    frame();
    expect(renders).toBe(before + 1);
    expect(latest?.latestByDevice).toEqual({
      a: { x: signal(2), y: signal(5) },
      b: { z: signal(3) },
    });
    expect(latest?.telemetryActive).toBe(true);
    expect(latest?.lastMessageAt).not.toBeNull();
    expect(onMessage).toHaveBeenCalledTimes(3);
  });

  it("filters devices in the browser and asks the gateway to filter too", () => {
    mount({ hydrate: false, deviceIds: ["b", "a"] });
    expect(socket().url).toContain(telemetrySocketPath(["a", "b"]));
    act(() => {
      socket().emit("a", { x: signal(1) });
      socket().emit("other", { x: signal(9) });
    });
    frame();
    expect(Object.keys(latest?.latestByDevice ?? {})).toEqual(["a"]);
    expect(onMessage).toHaveBeenCalledTimes(1);
  });

  it("keeps one socket when re-rendered with an equal device list", () => {
    mount({ hydrate: false, deviceIds: ["a", "b"] });
    mount({ hydrate: false, deviceIds: ["b", "a"] });
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("reconnects after a drop and re-hydrates from the snapshot", async () => {
    fetchTelemetrySnapshot.mockResolvedValue({ a: { x: signal(1) } });
    mount({ hydrate: true });
    await act(async () => {
      await Promise.resolve();
    });
    act(() => socket().onopen?.());
    expect(latest?.wsConnected).toBe(true);

    fetchTelemetrySnapshot.mockResolvedValue({ a: { x: signal(7) } });
    act(() => socket().close());
    expect(latest?.wsConnected).toBe(false);
    act(() => vi.advanceTimersByTime(2000));
    expect(FakeWebSocket.instances).toHaveLength(2);
    await act(async () => {
      socket().onopen?.();
      await Promise.resolve();
    });
    expect(latest?.wsConnected).toBe(true);
    expect(fetchTelemetrySnapshot).toHaveBeenCalledTimes(2);
    expect(latest?.latestByDevice.a.x).toEqual(signal(7));
  });

  it("never lets a reconnect snapshot overwrite newer socket data", async () => {
    mount({ hydrate: true });
    await act(async () => {
      await Promise.resolve();
    });
    act(() => socket().onopen?.());
    act(() => socket().close());
    act(() => vi.advanceTimersByTime(2000));

    let resolveSnapshot: (value: unknown) => void = () => undefined;
    fetchTelemetrySnapshot.mockReturnValue(new Promise((resolve) => (resolveSnapshot = resolve)));
    act(() => socket().onopen?.());
    // The reopened socket delivers x=8 before the snapshot (holding an older
    // x=7, plus y which the socket has not sent) comes back.
    act(() => socket().emit("a", { x: signal(8) }));
    frame();
    await act(async () => {
      resolveSnapshot({ a: { x: signal(7), y: signal(3) } });
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(latest?.latestByDevice.a).toEqual({ x: signal(8), y: signal(3) });
  });

  it("stops reconnecting and flushing after unmount", () => {
    mount({ hydrate: false });
    act(() => socket().emit("a", { x: signal(1) }));
    act(() => root.unmount());
    root = createRoot(document.createElement("div"));
    act(() => vi.advanceTimersByTime(60_000));
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});
