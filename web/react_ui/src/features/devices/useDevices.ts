import { useEffect, useState } from "react";
import { fetchDevices } from "../../api";
import type { DeviceStatus } from "../../types";

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

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const next = await fetchDevices();
      if (alive) {
        setDevices(next);
      }
    };
    void load();
    if (intervalMs <= 0) {
      return () => {
        alive = false;
      };
    }
    const timer = window.setInterval(() => void load(), intervalMs);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [intervalMs]);

  return devices;
}
