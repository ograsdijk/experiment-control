import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { openReconnectingSocket, reconnectDelayMs } from "./reconnectingSocket";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  binaryType = "blob";
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  close(): void {
    this.closed = true;
    this.onclose?.();
  }
}

describe("reconnectDelayMs", () => {
  it("backs off exponentially within the jitter band and caps", () => {
    const low = () => 0;
    const high = () => 1;
    expect(reconnectDelayMs(0, { random: high })).toBe(1000);
    expect(reconnectDelayMs(0, { random: low })).toBe(750);
    expect(reconnectDelayMs(3, { random: high })).toBe(8000);
    expect(reconnectDelayMs(20, { random: high })).toBe(30000);
    expect(reconnectDelayMs(20, { random: low })).toBe(22500);
    expect(reconnectDelayMs(2, { baseMs: 100, jitterRatio: 0, random: low })).toBe(400);
  });
});

describe("openReconnectingSocket", () => {
  const originalWebSocket = globalThis.WebSocket;

  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    vi.useRealTimers();
  });

  it("reconnects with growing delays and flags reopens", () => {
    const onOpen = vi.fn();
    const onClose = vi.fn();
    const socket = openReconnectingSocket({
      url: "ws://x/ws",
      binaryType: "arraybuffer",
      onOpen,
      onClose,
      onMessage: () => undefined,
      backoff: { baseMs: 100, jitterRatio: 0 },
    });
    expect(FakeWebSocket.instances[0].binaryType).toBe("arraybuffer");
    FakeWebSocket.instances[0].onopen?.();
    expect(socket.isOpen).toBe(true);
    expect(onOpen).toHaveBeenLastCalledWith({ reconnect: false });

    FakeWebSocket.instances[0].onclose?.();
    expect(socket.isOpen).toBe(false);
    expect(onClose).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(99);
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeWebSocket.instances).toHaveLength(2);

    // A failed attempt doubles the next delay.
    FakeWebSocket.instances[1].onclose?.();
    vi.advanceTimersByTime(199);
    expect(FakeWebSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeWebSocket.instances).toHaveLength(3);

    FakeWebSocket.instances[2].onopen?.();
    expect(onOpen).toHaveBeenLastCalledWith({ reconnect: true });
    // A successful open resets the backoff.
    FakeWebSocket.instances[2].onclose?.();
    vi.advanceTimersByTime(100);
    expect(FakeWebSocket.instances).toHaveLength(4);
    socket.close();
  });

  it("close cancels a pending reconnect and ignores late events", () => {
    const onMessage = vi.fn();
    const socket = openReconnectingSocket({
      url: "ws://x/ws",
      onMessage,
      backoff: { baseMs: 100, jitterRatio: 0 },
    });
    FakeWebSocket.instances[0].onclose?.();
    socket.close();
    vi.advanceTimersByTime(10_000);
    expect(FakeWebSocket.instances).toHaveLength(1);

    const second = openReconnectingSocket({ url: "ws://x/ws", onMessage });
    const ws = FakeWebSocket.instances[1];
    second.close();
    expect(ws.closed).toBe(true);
    ws.onmessage?.({ data: "{}" } as MessageEvent);
    expect(onMessage).not.toHaveBeenCalled();
    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });
});
