import { notifications } from "@mantine/notifications";
import { useCallback, useMemo, useState } from "react";
import {
  connectDevice,
  disconnectDevice,
  restartDevice,
  startDevice,
} from "../../api";
import type { DeviceStatus } from "../../types";
import { isDeviceDisconnected, isDeviceDriverStarted } from "../runtime/helpers";

type UseDeviceLifecycleControllerArgs = {
  orderedDevices: DeviceStatus[];
  refreshDevices: () => Promise<unknown>;
  invalidateDeviceCapabilities: (deviceId: string) => void;
};

export function useDeviceLifecycleController({
  orderedDevices,
  refreshDevices,
  invalidateDeviceCapabilities,
}: UseDeviceLifecycleControllerArgs) {
  const [deviceBusyById, setDeviceBusyById] = useState<Record<string, boolean>>(
    {}
  );
  const [deviceStartAllBusy, setDeviceStartAllBusy] = useState(false);
  const [deviceConnectAllBusy, setDeviceConnectAllBusy] = useState(false);

  const setDeviceBusy = useCallback((deviceId: string, busy: boolean) => {
    setDeviceBusyById((prev) => ({ ...prev, [deviceId]: busy }));
  }, []);

  const startAllTargets = useMemo(
    () =>
      orderedDevices
        .filter((device) => !isDeviceDriverStarted(device))
        .map((device) => device.device_id)
        .filter((deviceId) => !deviceBusyById[deviceId]),
    [deviceBusyById, orderedDevices]
  );
  const connectAllTargets = useMemo(
    () =>
      orderedDevices
        .filter((device) => isDeviceDriverStarted(device))
        .filter((device) => isDeviceDisconnected(device))
        .map((device) => device.device_id)
        .filter((deviceId) => !deviceBusyById[deviceId]),
    [deviceBusyById, orderedDevices]
  );

  const disableStartAllButton =
    deviceStartAllBusy || deviceConnectAllBusy || startAllTargets.length === 0;
  const disableConnectAllButton =
    deviceConnectAllBusy || deviceStartAllBusy || connectAllTargets.length === 0;

  const handleDeviceConnect = useCallback(
    async (deviceId: string) => {
      if (deviceBusyById[deviceId]) {
        return;
      }
      setDeviceBusy(deviceId, true);
      try {
        const resp = await connectDevice(deviceId);
        if (!resp.ok) {
          notifications.show({
            color: "red",
            title: "Connect failed",
            message: resp.error?.message ?? resp.error?.code ?? "Unknown error",
          });
          return;
        }
        notifications.show({
          color: "teal",
          title: "Device connect requested",
          message: deviceId,
        });
        invalidateDeviceCapabilities(deviceId);
        await refreshDevices();
      } finally {
        setDeviceBusy(deviceId, false);
      }
    },
    [
      deviceBusyById,
      invalidateDeviceCapabilities,
      refreshDevices,
      setDeviceBusy,
    ]
  );

  const handleDeviceDisconnect = useCallback(
    async (deviceId: string) => {
      if (deviceBusyById[deviceId]) {
        return;
      }
      setDeviceBusy(deviceId, true);
      try {
        const resp = await disconnectDevice(deviceId);
        if (!resp.ok) {
          notifications.show({
            color: "red",
            title: "Disconnect failed",
            message: resp.error?.message ?? resp.error?.code ?? "Unknown error",
          });
          return;
        }
        notifications.show({
          color: "teal",
          title: "Device disconnect requested",
          message: deviceId,
        });
        invalidateDeviceCapabilities(deviceId);
        await refreshDevices();
      } finally {
        setDeviceBusy(deviceId, false);
      }
    },
    [
      deviceBusyById,
      invalidateDeviceCapabilities,
      refreshDevices,
      setDeviceBusy,
    ]
  );

  const handleDeviceRestart = useCallback(
    async (deviceId: string) => {
      if (deviceBusyById[deviceId]) {
        return;
      }
      setDeviceBusy(deviceId, true);
      try {
        const resp = await restartDevice(deviceId);
        if (!resp.ok) {
          notifications.show({
            color: "red",
            title: "Restart failed",
            message: resp.error?.message ?? resp.error?.code ?? "Unknown error",
          });
          return;
        }
        notifications.show({
          color: "teal",
          title: "Device restart requested",
          message: deviceId,
        });
        invalidateDeviceCapabilities(deviceId);
        await refreshDevices();
      } finally {
        setDeviceBusy(deviceId, false);
      }
    },
    [
      deviceBusyById,
      invalidateDeviceCapabilities,
      refreshDevices,
      setDeviceBusy,
    ]
  );

  const handleStartAllDevices = useCallback(async () => {
    if (deviceStartAllBusy || deviceConnectAllBusy) {
      return;
    }
    const targets = startAllTargets;
    if (targets.length === 0) {
      notifications.show({
        color: "gray",
        title: "No devices to start",
        message: "All devices already look started or are currently busy.",
      });
      return;
    }
    setDeviceStartAllBusy(true);
    for (const deviceId of targets) {
      setDeviceBusy(deviceId, true);
    }
    try {
      const settled = await Promise.all(
        targets.map(async (deviceId) => ({
          deviceId,
          resp: await startDevice(deviceId),
        }))
      );
      let started = 0;
      let alreadyStarted = 0;
      const failed: string[] = [];
      for (const entry of settled) {
        if (entry.resp.ok) {
          started += 1;
          continue;
        }
        const msg = String(
          entry.resp.error?.message ?? entry.resp.error?.code ?? ""
        ).toLowerCase();
        if (msg.includes("already started")) {
          alreadyStarted += 1;
        } else {
          failed.push(entry.deviceId);
        }
      }
      if (failed.length > 0) {
        notifications.show({
          color: "red",
          title: "Start all completed with errors",
          message: `started ${started}, already started ${alreadyStarted}, failed ${failed.length}: ${failed.slice(0, 4).join(", ")}${failed.length > 4 ? ", ..." : ""}`,
        });
      } else {
        notifications.show({
          color: "teal",
          title: "Start all requested",
          message: `started ${started}, already started ${alreadyStarted}`,
        });
      }
      for (const deviceId of targets) {
        invalidateDeviceCapabilities(deviceId);
      }
      await refreshDevices();
    } finally {
      for (const deviceId of targets) {
        setDeviceBusy(deviceId, false);
      }
      setDeviceStartAllBusy(false);
    }
  }, [
    deviceConnectAllBusy,
    deviceStartAllBusy,
    invalidateDeviceCapabilities,
    refreshDevices,
    setDeviceBusy,
    startAllTargets,
  ]);

  const handleConnectAllDevices = useCallback(async () => {
    if (deviceConnectAllBusy || deviceStartAllBusy) {
      return;
    }
    const targets = connectAllTargets;
    if (targets.length === 0) {
      notifications.show({
        color: "gray",
        title: "No devices to connect",
        message: "All started devices appear connected or are currently busy.",
      });
      return;
    }
    setDeviceConnectAllBusy(true);
    for (const deviceId of targets) {
      setDeviceBusy(deviceId, true);
    }
    try {
      const settled = await Promise.all(
        targets.map(async (deviceId) => ({
          deviceId,
          resp: await connectDevice(deviceId),
        }))
      );
      let connected = 0;
      const failed: string[] = [];
      for (const entry of settled) {
        if (entry.resp.ok) {
          connected += 1;
        } else {
          failed.push(entry.deviceId);
        }
      }
      if (failed.length > 0) {
        notifications.show({
          color: "red",
          title: "Connect all completed with errors",
          message: `connected ${connected}, failed ${failed.length}: ${failed.slice(0, 4).join(", ")}${failed.length > 4 ? ", ..." : ""}`,
        });
      } else {
        notifications.show({
          color: "teal",
          title: "Connect all requested",
          message: `${connected} device${connected === 1 ? "" : "s"}`,
        });
      }
      for (const deviceId of targets) {
        invalidateDeviceCapabilities(deviceId);
      }
      await refreshDevices();
    } finally {
      for (const deviceId of targets) {
        setDeviceBusy(deviceId, false);
      }
      setDeviceConnectAllBusy(false);
    }
  }, [
    connectAllTargets,
    deviceConnectAllBusy,
    deviceStartAllBusy,
    invalidateDeviceCapabilities,
    refreshDevices,
    setDeviceBusy,
  ]);

  return {
    deviceBusyById,
    deviceStartAllBusy,
    deviceConnectAllBusy,
    disableStartAllButton,
    disableConnectAllButton,
    handleDeviceConnect,
    handleDeviceDisconnect,
    handleDeviceRestart,
    handleStartAllDevices,
    handleConnectAllDevices,
  };
}
