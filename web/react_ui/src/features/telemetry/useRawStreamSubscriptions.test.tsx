// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RawStreamSubscription } from "../stream/types";

const fetchRawStreamSnapshot = vi.fn();
vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  fetchRawStreamSnapshot: () => fetchRawStreamSnapshot(),
}));

import { useRawStreamSubscriptions } from "./useRawStreamSubscriptions";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  binaryType = "blob";
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

  emit(data: unknown): void {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }
}

const subscription: RawStreamSubscription = {
  deviceId: "dig",
  stream: "trace",
  channelIndex: 0,
  traceDecimator: "stride",
  traceMaxPoints: 100,
  traceMaxFps: 10,
  rollingWindow: 1,
  averageMode: "block",
};

const frameMessage = (seq: number) => ({
  topic: "manager.stream_frame",
  payload: { device_id: "dig", stream: "trace", seq, shape: [2], values: [1, 2] },
});

describe("useRawStreamSubscriptions reconnect", () => {
  const originalWebSocket = globalThis.WebSocket;
  let root: Root;
  let appliedSeqs: number[];

  const flush = async () => {
    await act(async () => {
      for (let i = 0; i < 5; i += 1) {
        await Promise.resolve();
      }
    });
  };

  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;
    appliedSeqs = [];
    fetchRawStreamSnapshot.mockReset();
    root = createRoot(document.createElement("div"));
  });

  afterEach(() => {
    act(() => root.unmount());
    globalThis.WebSocket = originalWebSocket;
    vi.useRealTimers();
  });

  const renderHook = () => {
    function Probe() {
      useRawStreamSubscriptions({
        activeSubscriptions: [subscription],
        applyFrame: (_subscription, frame) => {
          appliedSeqs.push(frame.seq);
          return new Set();
        },
      });
      return null;
    }
    act(() => root.render(createElement(Probe)));
  };

  const reconnect = async () => {
    const first = FakeWebSocket.instances[0];
    act(() => first.close());
    act(() => vi.advanceTimersByTime(2000));
    const second = FakeWebSocket.instances[1];
    act(() => second.onopen?.());
    return second;
  };

  it("drops the live repeat of a frame the reconnect snapshot applied", async () => {
    fetchRawStreamSnapshot.mockResolvedValue(null);
    renderHook();
    await flush();
    act(() => FakeWebSocket.instances[0].onopen?.());
    act(() => FakeWebSocket.instances[0].emit(frameMessage(4)));

    fetchRawStreamSnapshot.mockResolvedValue(frameMessage(5));
    const second = await reconnect();
    await flush();
    act(() => second.emit(frameMessage(5)));
    act(() => second.emit(frameMessage(6)));

    expect(appliedSeqs).toEqual([4, 5, 6]);
  });

  it("skips the reconnect snapshot once live frames have resumed", async () => {
    fetchRawStreamSnapshot.mockResolvedValue(null);
    renderHook();
    await flush();
    act(() => FakeWebSocket.instances[0].onopen?.());

    let resolveSnapshot: (value: unknown) => void = () => undefined;
    fetchRawStreamSnapshot.mockReturnValue(
      new Promise((resolve) => {
        resolveSnapshot = resolve;
      })
    );
    const second = await reconnect();
    act(() => second.emit(frameMessage(7)));
    resolveSnapshot(frameMessage(7));
    await flush();

    expect(appliedSeqs).toEqual([7]);
  });
});
