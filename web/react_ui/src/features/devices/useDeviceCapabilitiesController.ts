import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import { fetchCapabilities } from "../../api";
import type { CapabilityMember, DeviceStatus } from "../../types";
import { mapWithConcurrency } from "../common/concurrency";
import { abortable } from "../polling/useAdaptivePolling";
import { isDeviceDisconnected, shouldPreloadCapabilities } from "../runtime/helpers";

const PRELOAD_CONCURRENCY = 4;
const PRELOAD_RETRY_BASE_MS = 2000;
const PRELOAD_RETRY_MAX_MS = 60000;
const PRELOAD_FETCH_TIMEOUT_MS = 10000;

type PreloadFailure = { attempts: number; retryAt: number };

/** Backoff before retrying a device whose capabilities came back empty. */
export function capabilitiesRetryDelayMs(attempts: number): number {
  return Math.min(
    PRELOAD_RETRY_BASE_MS * 2 ** Math.max(0, attempts - 1),
    PRELOAD_RETRY_MAX_MS
  );
}

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
  const preloadFailuresRef = useRef<Map<string, PreloadFailure>>(new Map());
  const [retryTick, setRetryTick] = useState(0);

  const invalidateDeviceCapabilities = (deviceId: string) => {
    preloadFailuresRef.current.delete(deviceId);
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
        preloadFailuresRef.current.delete(deviceId);
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

  // Stable key of the preloadable devices still missing capabilities. The
  // devices array changes on every poll; keying on this string means the
  // preload only re-runs when the set of devices to fetch actually changes.
  const missingCapabilitiesKey = useMemo(
    () =>
      devices
        .filter(
          (device) =>
            shouldPreloadCapabilities(device) &&
            (capabilitiesByDevice[device.device_id] ?? []).length === 0
        )
        .map((device) => device.device_id)
        .sort()
        .join(","),
    [devices, capabilitiesByDevice]
  );

  useEffect(() => {
    if (!missingCapabilitiesKey) {
      return;
    }
    const now = Date.now();
    const failures = preloadFailuresRef.current;
    const missing = missingCapabilitiesKey.split(",");
    const due = missing.filter((deviceId) => {
      const failure = failures.get(deviceId);
      return !failure || failure.retryAt <= now;
    });
    const controller = new AbortController();
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    // Failed/empty fetches are retried with backoff rather than on every poll.
    const scheduleRetry = () => {
      let earliest = Number.POSITIVE_INFINITY;
      for (const deviceId of missing) {
        const failure = failures.get(deviceId);
        if (failure) {
          earliest = Math.min(earliest, failure.retryAt);
        }
      }
      if (Number.isFinite(earliest)) {
        retryTimer = setTimeout(
          () => setRetryTick((tick) => tick + 1),
          Math.max(0, earliest - Date.now())
        );
      }
    };
    if (due.length === 0) {
      scheduleRetry();
      return () => {
        if (retryTimer !== null) {
          clearTimeout(retryTimer);
        }
      };
    }
    const preload = async () => {
      const next: Record<string, CapabilityMember[]> = {};
      await mapWithConcurrency(due, PRELOAD_CONCURRENCY, async (deviceId) => {
        let caps: CapabilityMember[] = [];
        // Per-fetch deadline: results are committed once the whole batch
        // settles, and the effect does not re-run while the missing set is
        // unchanged, so one hung request must not hold the batch open.
        const fetchController = new AbortController();
        const abortFetch = () => fetchController.abort();
        controller.signal.addEventListener("abort", abortFetch, { once: true });
        const deadline = setTimeout(abortFetch, PRELOAD_FETCH_TIMEOUT_MS);
        try {
          caps = await abortable(
            fetchCapabilities(deviceId, fetchController.signal),
            fetchController.signal
          );
        } catch {
          if (controller.signal.aborted) {
            return;
          }
        } finally {
          clearTimeout(deadline);
          controller.signal.removeEventListener("abort", abortFetch);
        }
        if (controller.signal.aborted) {
          return;
        }
        if (caps.length > 0) {
          failures.delete(deviceId);
          next[deviceId] = caps;
          return;
        }
        const attempts = (failures.get(deviceId)?.attempts ?? 0) + 1;
        failures.set(deviceId, {
          attempts,
          retryAt: Date.now() + capabilitiesRetryDelayMs(attempts),
        });
      });
      if (controller.signal.aborted) {
        return;
      }
      if (Object.keys(next).length > 0) {
        setCapabilitiesByDevice((prev) => ({ ...prev, ...next }));
      }
      scheduleRetry();
    };
    void preload();
    return () => {
      controller.abort();
      if (retryTimer !== null) {
        clearTimeout(retryTimer);
      }
    };
  }, [missingCapabilitiesKey, retryTick]);

  return {
    capabilitiesByDevice,
    setCapabilitiesByDevice,
    invalidateDeviceCapabilities,
  };
}
