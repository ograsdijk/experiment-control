import { afterEach, describe, expect, it, vi } from "vitest";

import type { TelemetryMessage, TelemetrySignal } from "../../types";
import { TelemetryLatestStore } from "./TelemetryLatestStore";
import { DeviceTelemetryPresentationStore } from "./DeviceTelemetryPresentationStore";

const signal = (value: number): TelemetrySignal => ({ value });
const message = (
  deviceId: string,
  signals: Record<string, TelemetrySignal>
): TelemetryMessage => ({
  topic: "manager.telemetry_update",
  payload: { device_id: deviceId, signals },
});

function makeStore(options: {
  document?: Pick<Document, "hidden" | "addEventListener" | "removeEventListener">;
} = {}) {
  const latestStore = new TelemetryLatestStore();
  const presentationStore = new DeviceTelemetryPresentationStore({
    latestStore,
    document: options.document,
    requestAnimationFrame: (callback) => {
      callback(Date.now());
      return 0;
    },
    cancelAnimationFrame: () => {},
  });
  return { latestStore, presentationStore };
}

function createVisibilityDocument() {
  let hidden = false;
  const listeners = new Set<() => void>();
  const visibilityDocument = {
    get hidden() {
      return hidden;
    },
    addEventListener: vi.fn((_type: string, listener: () => void) => {
      listeners.add(listener);
    }),
    removeEventListener: vi.fn((_type: string, listener: () => void) => {
      listeners.delete(listener);
    }),
  } as unknown as Pick<
    Document,
    "hidden" | "addEventListener" | "removeEventListener"
  >;
  return {
    visibilityDocument,
    setHidden(value: boolean) {
      hidden = value;
      for (const listener of listeners) listener();
    },
  };
}

describe("DeviceTelemetryPresentationStore", () => {
  afterEach(() => vi.useRealTimers());

  it("coalesces repeated values into one latest per-signal visual update", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(0));
    const { latestStore, presentationStore } = makeStore();
    const listener = vi.fn();
    const unsubscribe = presentationStore.subscribeSignal("A", "temperature", listener);
    listener.mockClear();

    for (let value = 1; value <= 100; value += 1) {
      latestStore.applyMessage(message("A", { temperature: signal(value) }));
    }

    expect(listener).not.toHaveBeenCalled();
    vi.advanceTimersByTime(249);
    expect(listener).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);

    expect(listener).toHaveBeenCalledOnce();
    expect(presentationStore.getDisplayedSignal("A", "temperature")?.value).toBe(100);
    listener.mockClear();
    latestStore.applyMessage(message("A", { temperature: signal(101) }));
    vi.advanceTimersByTime(249);
    expect(listener).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(listener).toHaveBeenCalledOnce();
    unsubscribe();
    presentationStore.dispose();
  });

  it("notifies only the dirty signal and device", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(0));
    const { latestStore, presentationStore } = makeStore();
    const tempA = vi.fn();
    const pressureA = vi.fn();
    const tempB = vi.fn();
    presentationStore.subscribeSignal("A", "temperature", tempA);
    presentationStore.subscribeSignal("A", "pressure", pressureA);
    presentationStore.subscribeSignal("B", "temperature", tempB);
    tempA.mockClear();
    pressureA.mockClear();
    tempB.mockClear();

    latestStore.applyMessage(message("A", { temperature: signal(10) }));
    vi.advanceTimersByTime(250);

    expect(tempA).toHaveBeenCalledOnce();
    expect(pressureA).not.toHaveBeenCalled();
    expect(tempB).not.toHaveBeenCalled();
    presentationStore.dispose();
  });

  it("uses one shared scheduled flush for simultaneous device updates", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(0));
    const latestStore = new TelemetryLatestStore();
    const scheduleTimeout = vi.fn((callback: () => void, delay: number) =>
      globalThis.setTimeout(callback, delay)
    );
    const presentationStore = new DeviceTelemetryPresentationStore({
      latestStore,
      setTimeout: scheduleTimeout,
      clearTimeout: globalThis.clearTimeout,
      requestAnimationFrame: (callback) => {
        callback(Date.now());
        return 0;
      },
      cancelAnimationFrame: () => {},
    });
    presentationStore.subscribeSignal("A", "temperature", vi.fn());
    presentationStore.subscribeSignal("B", "temperature", vi.fn());
    scheduleTimeout.mockClear();

    latestStore.applyMessage(message("A", { temperature: signal(1) }));
    latestStore.applyMessage(message("B", { temperature: signal(2) }));

    expect(scheduleTimeout).toHaveBeenCalledOnce();
    vi.advanceTimersByTime(250);
    expect(presentationStore.getDebugState().pendingSchedulerWork).toBe(false);
    presentationStore.dispose();
  });

  it("invokes host scheduler functions without an instance receiver", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(0));
    const latestStore = new TelemetryLatestStore();
    function receiverSensitiveSetTimeout(
      this: unknown,
      callback: () => void,
      delay: number
    ) {
      if (this !== undefined) throw new TypeError("Illegal invocation");
      return globalThis.setTimeout(callback, delay);
    }
    const presentationStore = new DeviceTelemetryPresentationStore({
      latestStore,
      setTimeout: receiverSensitiveSetTimeout,
      clearTimeout: globalThis.clearTimeout,
      requestAnimationFrame: (callback) => {
        callback(Date.now());
        return 0;
      },
      cancelAnimationFrame: () => {},
    });
    const listener = vi.fn();
    presentationStore.subscribeSignal("A", "temperature", listener);
    listener.mockClear();

    latestStore.applyMessage(message("A", { temperature: signal(1) }));
    vi.advanceTimersByTime(250);

    expect(listener).toHaveBeenCalledOnce();
    presentationStore.dispose();
  });

  it("stops source work after the last listener and releases unretained snapshots", () => {
    const { latestStore, presentationStore } = makeStore();
    const listener = vi.fn();
    const unsubscribe = presentationStore.subscribeSignal("A", "temperature", listener);
    listener.mockClear();

    expect(presentationStore.getDebugState()).toMatchObject({
      activeDeviceSubscriptions: 1,
      activeSignalSubscriptions: 1,
    });
    unsubscribe();
    latestStore.applyMessage(message("A", { temperature: signal(10) }));

    expect(listener).not.toHaveBeenCalled();
    expect(presentationStore.getDebugState()).toEqual({
      activeDeviceSubscriptions: 0,
      activeSignalSubscriptions: 0,
      retainedSignals: 0,
      pendingSchedulerWork: false,
    });
    presentationStore.dispose();
  });

  it("keeps offscreen snapshots quiet and catches them up on reactivation", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(0));
    const { latestStore, presentationStore } = makeStore();
    latestStore.applyMessage(message("A", { temperature: signal(1) }));
    const release = presentationStore.retainSignal("A", "temperature");
    const initialListener = vi.fn();
    const unsubscribe = presentationStore.subscribeSignal(
      "A",
      "temperature",
      initialListener
    );
    initialListener.mockClear();
    unsubscribe();

    latestStore.applyMessage(message("A", { temperature: signal(2) }));
    vi.advanceTimersByTime(1000);
    expect(initialListener).not.toHaveBeenCalled();
    expect(presentationStore.getDisplayedSignal("A", "temperature")?.value).toBe(1);

    const reactivated = vi.fn();
    const stopReactivated = presentationStore.subscribeSignal(
      "A",
      "temperature",
      reactivated
    );
    expect(reactivated).toHaveBeenCalledOnce();
    expect(presentationStore.getDisplayedSignal("A", "temperature")?.value).toBe(2);
    stopReactivated();
    release();
    presentationStore.dispose();
  });

  it("pauses presentation while hidden and publishes one latest catch-up", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(0));
    const visibility = createVisibilityDocument();
    const { latestStore, presentationStore } = makeStore({
      document: visibility.visibilityDocument,
    });
    const listener = vi.fn();
    presentationStore.subscribeSignal("A", "temperature", listener);
    listener.mockClear();
    latestStore.applyMessage(message("A", { temperature: signal(1) }));
    vi.advanceTimersByTime(250);
    listener.mockClear();

    latestStore.applyMessage(message("A", { temperature: signal(2) }));
    expect(presentationStore.getDebugState().pendingSchedulerWork).toBe(true);
    visibility.setHidden(true);
    expect(presentationStore.getDebugState().pendingSchedulerWork).toBe(false);
    latestStore.applyMessage(message("A", { temperature: signal(3) }));
    vi.advanceTimersByTime(1000);

    expect(listener).not.toHaveBeenCalled();
    expect(latestStore.getSignal("A", "temperature")?.value).toBe(3);
    expect(presentationStore.getDisplayedSignal("A", "temperature")?.value).toBe(1);

    visibility.setHidden(false);

    expect(listener).toHaveBeenCalledOnce();
    expect(presentationStore.getDisplayedSignal("A", "temperature")?.value).toBe(3);
    presentationStore.dispose();
  });
});
