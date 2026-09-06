import { useState } from "react";
import { fetchDevices } from "../../api";
import type { DeviceStatus } from "../../types";
import { sameDeviceStatuses } from "../polling/pollEquality";
import { useAdaptivePolling } from "../polling/useAdaptivePolling";

export type UseDevicesOptions = {
  /** Polling period in milliseconds. Defaults to 5000. */
  intervalMs?: number;
};

/**
 * Periodically refreshes /api/devices. Devices change rarely (driver
 * connect/disconnect), so polling at 5 s is the default.
 */
export function useDevices(options: UseDevicesOptions = {}): DeviceStatus[] {
  const { intervalMs = 5000 } = options;
  const [devices, setDevices] = useState<DeviceStatus[]>([]);

  useAdaptivePolling({
    enabled: true,
    intervalMs,
    poll: (signal) => fetchDevices(signal),
    onValue: setDevices,
    equality: sameDeviceStatuses,
    refreshOnVisible: intervalMs > 0,
    endpoint: "/api/devices",
  });

  return devices;
}
