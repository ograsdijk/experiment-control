import {
  createContext,
  useContext,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type ReactNode,
  type SetStateAction,
} from "react";

import type { DeviceStatus } from "../../types";

/**
 * Shared device-list + device-ordering + per-device UI state container.
 *
 * App.tsx historically held the device roster, the user-managed display
 * order, the per-device telemetry-section collapse state, and the
 * device-grid DOM ref inline. All of those moved here so future
 * extractions (device-card render loop, command deck, pinned commands)
 * have a stable seam to read device data from.
 *
 * **Scope choices** (mirrors round-8 TelemetryContext and round-9
 * StreamAnalysisContext):
 *
 * - The Provider owns the **state container only**. Network-side
 *   handlers (device polling, connect/disconnect/restart commands,
 *   capability fetching) stay in App.tsx for now — they belong to
 *   feature controllers that already exist
 *   (`useDeviceLifecycleController`, `useDeviceCapabilitiesController`,
 *   etc.) and aren't bundled here.
 * - The single device-scoped `useMemo` (`orderedDevices`) lives in the
 *   Provider so consumers don't recompute the sort independently.
 * - Pinned commands and command-deck state are **not** included — they
 *   are their own concern that a follow-up PR will pull out together.
 *
 * **Downstream compatibility**: the existing standalone hooks under
 * `features/devices/` (`useDevices`, `useDeviceCapabilitiesController`,
 * `useDeviceCommandController`, etc.) are unchanged. Centrex instance
 * UIs import those directly via `@ec-ui/features/devices/...`; this
 * Context is purely additive on top.
 */

export interface DevicesContextValue {
  // -----------------------------------------------------------------
  // Roster — source of truth for the device list
  // -----------------------------------------------------------------
  devices: DeviceStatus[];
  setDevices: Dispatch<SetStateAction<DeviceStatus[]>>;
  /** Devices sorted by `deviceOrder` (user-managed) with stable
   *  fallback to alphabetical device_id. */
  orderedDevices: DeviceStatus[];

  // -----------------------------------------------------------------
  // User-managed display order (localStorage-persisted)
  // -----------------------------------------------------------------
  deviceOrder: string[];
  setDeviceOrder: Dispatch<SetStateAction<string[]>>;

  // -----------------------------------------------------------------
  // Per-device UI collapse state for the telemetry section
  // (localStorage-persisted)
  // -----------------------------------------------------------------
  telemetryCollapsedByDevice: Record<string, boolean>;
  setTelemetryCollapsedByDevice: Dispatch<
    SetStateAction<Record<string, boolean>>
  >;

  // -----------------------------------------------------------------
  // DOM ref — used by the device-grid drag/drop machinery to detect
  // column count at render time. Lives here so any future extraction
  // of the device-card render loop can pick it up via context.
  // -----------------------------------------------------------------
  deviceGridRef: MutableRefObject<HTMLDivElement | null>;
}

const DevicesContext = createContext<DevicesContextValue | null>(null);

function loadDeviceOrder(): string[] {
  try {
    const raw = localStorage.getItem("ecui.deviceOrder");
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((value) => (typeof value === "string" ? value : ""))
      .filter((value) => value.length > 0);
  } catch {
    return [];
  }
}

function loadTelemetryCollapsed(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem("ecui.telemetryCollapsedByDevice");
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    const next: Record<string, boolean> = {};
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof key === "string" && typeof value === "boolean") {
        next[key] = value;
      }
    }
    return next;
  } catch {
    return {};
  }
}

export function DevicesProvider({ children }: { children: ReactNode }) {
  const [devices, setDevices] = useState<DeviceStatus[]>([]);
  const [deviceOrder, setDeviceOrder] = useState<string[]>(loadDeviceOrder);
  const [telemetryCollapsedByDevice, setTelemetryCollapsedByDevice] = useState<
    Record<string, boolean>
  >(loadTelemetryCollapsed);
  const deviceGridRef = useRef<HTMLDivElement | null>(null);

  // Stable order applied to the device list. Re-runs only when either
  // the roster or the user-managed order changes.
  const orderedDevices = useMemo(() => {
    const rank = new Map(deviceOrder.map((deviceId, idx) => [deviceId, idx]));
    return [...devices].sort((a, b) => {
      const aRank = rank.get(a.device_id);
      const bRank = rank.get(b.device_id);
      if (aRank != null && bRank != null && aRank !== bRank) {
        return aRank - bRank;
      }
      if (aRank != null) {
        return -1;
      }
      if (bRank != null) {
        return 1;
      }
      return a.device_id.localeCompare(b.device_id);
    });
  }, [devices, deviceOrder]);

  const value = useMemo<DevicesContextValue>(
    () => ({
      devices,
      setDevices,
      orderedDevices,
      deviceOrder,
      setDeviceOrder,
      telemetryCollapsedByDevice,
      setTelemetryCollapsedByDevice,
      deviceGridRef,
    }),
    [
      devices,
      orderedDevices,
      deviceOrder,
      telemetryCollapsedByDevice,
    ]
  );

  return (
    <DevicesContext.Provider value={value}>{children}</DevicesContext.Provider>
  );
}

export function useDevicesContext(): DevicesContextValue {
  const ctx = useContext(DevicesContext);
  if (ctx === null) {
    throw new Error(
      "useDevicesContext must be called inside a <DevicesProvider>"
    );
  }
  return ctx;
}
