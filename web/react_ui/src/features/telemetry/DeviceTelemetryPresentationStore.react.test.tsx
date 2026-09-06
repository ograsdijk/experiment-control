// @vitest-environment jsdom

import { act, createElement, Fragment } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TelemetryMessage, TelemetrySignal } from "../../types";
import {
  DeviceTelemetryPresentationStore,
  useDisplayedTelemetrySignal,
} from "./DeviceTelemetryPresentationStore";
import { TelemetryLatestStore } from "./TelemetryLatestStore";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const signal = (value: number): TelemetrySignal => ({ value });
const message = (
  deviceId: string,
  signals: Record<string, TelemetrySignal>
): TelemetryMessage => ({
  topic: "manager.telemetry_update",
  payload: { device_id: deviceId, signals },
});

describe("useDisplayedTelemetrySignal", () => {
  const mountedRoots: Array<ReturnType<typeof createRoot>> = [];

  afterEach(() => {
    for (const root of mountedRoots.splice(0)) act(() => root.unmount());
    vi.useRealTimers();
  });

  it("rerenders only the paced value consumer whose signal changed", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(0));
    const latestStore = new TelemetryLatestStore();
    const presentationStore = new DeviceTelemetryPresentationStore({
      latestStore,
      requestAnimationFrame: (callback) => {
        callback(Date.now());
        return 0;
      },
      cancelAnimationFrame: () => {},
    });
    const temperatureRenders = vi.fn();
    const pressureRenders = vi.fn();

    function SignalView({
      signalName,
      onRender,
    }: {
      signalName: string;
      onRender: () => void;
    }) {
      useDisplayedTelemetrySignal("A", signalName, true, presentationStore);
      onRender();
      return null;
    }

    const root = createRoot(document.createElement("div"));
    mountedRoots.push(root);
    act(() => {
      root.render(
        createElement(
          Fragment,
          null,
          createElement(SignalView, {
            signalName: "temperature",
            onRender: temperatureRenders,
          }),
          createElement(SignalView, {
            signalName: "pressure",
            onRender: pressureRenders,
          })
        )
      );
    });
    expect(temperatureRenders).toHaveBeenCalledOnce();
    expect(pressureRenders).toHaveBeenCalledOnce();

    act(() => {
      latestStore.applyMessage(message("A", { temperature: signal(1) }));
      vi.advanceTimersByTime(249);
    });
    expect(temperatureRenders).toHaveBeenCalledOnce();
    expect(pressureRenders).toHaveBeenCalledOnce();

    act(() => vi.advanceTimersByTime(1));
    expect(temperatureRenders).toHaveBeenCalledTimes(2);
    expect(pressureRenders).toHaveBeenCalledOnce();

    presentationStore.dispose();
  });
});
