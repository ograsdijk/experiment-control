// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CapabilityMember, DeviceStatus } from "../../types";

const fetchCapabilities = vi.fn<(deviceId: string, signal?: AbortSignal) => Promise<CapabilityMember[]>>();
vi.mock("../../api", () => ({
  fetchCapabilities: (deviceId: string, signal?: AbortSignal) =>
    fetchCapabilities(deviceId, signal),
}));

const { useDeviceCapabilitiesController } = await import(
  "./useDeviceCapabilitiesController"
);

let latest: Record<string, CapabilityMember[]> = {};

function Harness({ devices }: { devices: DeviceStatus[] }) {
  latest = useDeviceCapabilitiesController(devices).capabilitiesByDevice;
  return null;
}

const device = (deviceId: string): DeviceStatus =>
  ({ device_id: deviceId, liveness: "ALIVE", registered: true }) as DeviceStatus;
const caps = (name: string): CapabilityMember[] =>
  [{ name, kind: "method" }] as unknown as CapabilityMember[];

describe("useDeviceCapabilitiesController preload", () => {
  let container: HTMLDivElement;
  let root: Root;

  const flush = async () => {
    await act(async () => {
      for (let i = 0; i < 5; i += 1) {
        await Promise.resolve();
      }
    });
  };

  beforeEach(() => {
    vi.useFakeTimers();
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    fetchCapabilities.mockReset();
    latest = {};
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  it("does not let one hung fetch hold back the rest of the batch", async () => {
    fetchCapabilities.mockImplementation((deviceId) =>
      deviceId === "hung"
        ? new Promise<CapabilityMember[]>(() => undefined)
        : Promise.resolve(caps(deviceId))
    );
    act(() =>
      root.render(createElement(Harness, { devices: [device("ok"), device("hung")] }))
    );
    await flush();
    expect(latest.ok).toBeUndefined();

    await act(async () => vi.advanceTimersByTime(10000));
    await flush();
    expect(latest.ok).toEqual(caps("ok"));
    expect(latest.hung).toBeUndefined();
  });
});
