import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { fetchCapabilities } from "../../api";
import type { CapabilityMember, DeviceStatus } from "../../types";
import { isDeviceDisconnected, shouldPreloadCapabilities } from "../runtime/helpers";

type UseDeviceCapabilitiesControllerResult = {
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
  setCapabilitiesByDevice: Dispatch<
    SetStateAction<Record<string, CapabilityMember[]>>
  >;
  invalidateDeviceCapabilities: (deviceId: string) => void;
};

export function useDeviceCapabilitiesController(
  devices: DeviceStatus[]
): UseDeviceCapabilitiesControllerResult {
  const [capabilitiesByDevice, setCapabilitiesByDevice] = useState<
    Record<string, CapabilityMember[]>
  >({});
  const deviceConnectivityRef = useRef<Record<string, boolean>>({});

  const invalidateDeviceCapabilities = (deviceId: string) => {
    setCapabilitiesByDevice((prev) => {
      if (!(deviceId in prev)) {
        return prev;
      }
      const next = { ...prev };
      delete next[deviceId];
      return next;
    });
  };

  useEffect(() => {
    const nextConnectivity: Record<string, boolean> = {};
    const idsToInvalidate: string[] = [];
    for (const device of devices) {
      const deviceId = device.device_id;
      const connected = !isDeviceDisconnected(device);
      nextConnectivity[deviceId] = connected;
      const previouslyConnected = deviceConnectivityRef.current[deviceId];
      if (connected && previouslyConnected === false) {
        idsToInvalidate.push(deviceId);
      }
    }
    deviceConnectivityRef.current = nextConnectivity;
    if (idsToInvalidate.length === 0) {
      return;
    }
    setCapabilitiesByDevice((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const deviceId of idsToInvalidate) {
        if (deviceId in next) {
          delete next[deviceId];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [devices]);

  useEffect(() => {
    if (devices.length === 0) {
      return;
    }
    let cancelled = false;
    const preload = async () => {
      const next: Record<string, CapabilityMember[]> = {};
      for (const device of devices) {
        const existing = capabilitiesByDevice[device.device_id] ?? [];
        if (existing.length > 0) {
          continue;
        }
        if (!shouldPreloadCapabilities(device)) {
          continue;
        }
        const caps = await fetchCapabilities(device.device_id);
        if (cancelled) {
          return;
        }
        if (caps.length > 0) {
          next[device.device_id] = caps;
        }
      }
      if (!cancelled && Object.keys(next).length > 0) {
        setCapabilitiesByDevice((prev) => ({ ...prev, ...next }));
      }
    };
    void preload();
    return () => {
      cancelled = true;
    };
  }, [devices, capabilitiesByDevice]);

  return {
    capabilitiesByDevice,
    setCapabilitiesByDevice,
    invalidateDeviceCapabilities,
  };
}
