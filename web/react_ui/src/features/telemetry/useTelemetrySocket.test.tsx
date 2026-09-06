// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
});
