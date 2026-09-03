import { describe, expect, it, vi } from "vitest";

import type { TelemetryMessage, TelemetrySignal } from "../../types";
import { TelemetryLatestStore } from "./TelemetryLatestStore";

const signal = (value: number): TelemetrySignal => ({ value });
const message = (deviceId: string, signals: Record<string, TelemetrySignal>): TelemetryMessage => ({
  topic: "manager.telemetry_update",
  payload: { device_id: deviceId, signals },
});

describe("TelemetryLatestStore", () => {
  it("notifies only the changed device and exposes the new signal immediately", () => {
    const store = new TelemetryLatestStore();
    const listenerA = vi.fn();
    const listenerB = vi.fn();
    store.subscribeDevice("A", listenerA);
    store.subscribeDevice("B", listenerB);

    store.applyMessage(message("A", { temp: signal(12) }));

    expect(listenerA).toHaveBeenCalledOnce();
    expect(listenerB).not.toHaveBeenCalled();
    expect(store.getSignal("A", "temp")?.value).toBe(12);
  });

  it("stops callbacks after unsubscribe", () => {
    const store = new TelemetryLatestStore();
    const listener = vi.fn();
    const unsubscribe = store.subscribeDevice("A", listener);
    unsubscribe();
    store.applyMessage(message("A", { temp: signal(1) }));
    expect(listener).not.toHaveBeenCalled();
  });

  it("hydrates and then merges websocket updates", () => {
    const store = new TelemetryLatestStore();
    store.hydrate({ A: { temp: signal(1), pressure: signal(2) } });
    store.applyMessage(message("A", { temp: signal(3) }));
    expect(store.getDevice("A")).toEqual({ temp: signal(3), pressure: signal(2) });
  });

  it("does not notify connection subscribers for every active message", () => {
    const store = new TelemetryLatestStore();
    const listener = vi.fn();
    store.subscribeConnection(listener);
    for (let index = 0; index < 100; index += 1) {
      store.applyMessage(message("A", { temp: signal(index) }), index);
    }
    expect(listener).toHaveBeenCalledOnce();
    expect(store.getLastReceiptAt()).toBe(99);
  });
});
