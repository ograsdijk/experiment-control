// @vitest-environment jsdom

import { act, createElement, Fragment } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TelemetryMessage, TelemetrySignal } from "../../types";
import {
  TelemetryLatestStore,
  useDeviceTelemetry,
} from "./TelemetryLatestStore";

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

describe("useDeviceTelemetry", () => {
  const mountedRoots: Array<ReturnType<typeof createRoot>> = [];

  afterEach(() => {
    for (const root of mountedRoots.splice(0)) act(() => root.unmount());
  });

  it("rerenders only the component subscribed to the changed device", () => {
    const store = new TelemetryLatestStore();
    const rendersA = vi.fn();
    const rendersB = vi.fn();

    function DeviceView({
      deviceId,
      onRender,
    }: {
      deviceId: string;
      onRender: () => void;
    }) {
      useDeviceTelemetry(deviceId, store);
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
          createElement(DeviceView, { deviceId: "A", onRender: rendersA }),
          createElement(DeviceView, { deviceId: "B", onRender: rendersB })
        )
      );
    });

    expect(rendersA).toHaveBeenCalledOnce();
    expect(rendersB).toHaveBeenCalledOnce();

    act(() => store.applyMessage(message("A", { temperature: signal(1) })));

    expect(rendersA).toHaveBeenCalledTimes(2);
    expect(rendersB).toHaveBeenCalledOnce();
  });
});
