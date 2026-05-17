import { notifications } from "@mantine/notifications";

import {
  cleanupInstanceOrphans,
  fetchDevices,
  fetchGatewaySettings,
  fetchInstanceRuntimeStatus,
  fetchStreams,
  type GatewaySettingsInfo,
  type InstanceRuntimeStatus,
} from "../../api";
import type { DeviceStatus, StreamCatalogEntry } from "../../types";
import { useDevicesContext } from "../devices/DevicesContext";
import { useSettings } from "./SettingsContext";

/**
 * Runtime / settings / device-list refresh handlers.
 *
 * Five thin async wrappers around the corresponding API endpoints,
 * plus the orphan-cleanup action that consumes
 * `refreshInstanceRuntime`. Together they own the App's "tell me
 * the latest runtime state" surface.
 *
 * **Handlers**:
 *
 * - `refreshDevices()` — re-fetches the device list and writes it
 *   into DevicesContext.
 * - `refreshStreams()` — re-fetches the stream catalog from the
 *   gateway. (App-side consumers wire the result into their own
 *   state; the hook just exposes the fetch.)
 * - `loadGatewayRuntimeSettings()` — refreshes the gateway settings
 *   blob; manages settings-loading + settings-error state.
 * - `refreshInstanceRuntime()` — refreshes the instance runtime
 *   status; manages runtime-loading + runtime-error state.
 * - `runInstanceCleanup(dryRun)` — runs the orphan-process cleanup
 *   RPC, then triggers a runtime-status refresh regardless of
 *   outcome.
 */
export function useRuntimeRefreshers() {
  const { setDevices } = useDevicesContext();
  const {
    setSettingsLoading,
    setSettingsError,
    setGatewaySettings,
    setInstanceRuntimeLoading,
    setInstanceRuntimeError,
    setInstanceRuntimeStatus,
    setInstanceCleanupBusy,
  } = useSettings();

  const refreshDevices = async (): Promise<DeviceStatus[]> => {
    const next = await fetchDevices();
    setDevices(next);
    return next;
  };

  const refreshStreams = async (): Promise<StreamCatalogEntry[]> => {
    return fetchStreams();
  };

  const loadGatewayRuntimeSettings =
    async (): Promise<GatewaySettingsInfo | null> => {
      setSettingsLoading(true);
      setSettingsError(null);
      try {
        const next = await fetchGatewaySettings();
        if (next === null) {
          setSettingsError("Could not fetch gateway settings.");
          return null;
        }
        setGatewaySettings(next);
        return next;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setSettingsError(message);
        return null;
      } finally {
        setSettingsLoading(false);
      }
    };

  const refreshInstanceRuntime =
    async (): Promise<InstanceRuntimeStatus | null> => {
      setInstanceRuntimeLoading(true);
      setInstanceRuntimeError(null);
      try {
        const next = await fetchInstanceRuntimeStatus();
        if (next === null) {
          setInstanceRuntimeError("Could not fetch instance runtime status.");
          return null;
        }
        setInstanceRuntimeStatus(next);
        return next;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setInstanceRuntimeError(message);
        return null;
      } finally {
        setInstanceRuntimeLoading(false);
      }
    };

  const runInstanceCleanup = async (
    dryRun: boolean
  ): Promise<Record<string, unknown> | null> => {
    setInstanceCleanupBusy(true);
    try {
      const resp = await cleanupInstanceOrphans({
        dry_run: dryRun,
        stale_only: true,
        timeout_s: 2.0,
      });
      if (!resp.ok) {
        const message =
          typeof resp.error?.message === "string" &&
          resp.error.message.trim().length > 0
            ? resp.error.message
            : "Cleanup request failed.";
        notifications.show({
          color: "red",
          title: "Instance cleanup failed",
          message,
        });
        await refreshInstanceRuntime();
        return null;
      }
      const result =
        resp.result && typeof resp.result === "object"
          ? (resp.result as Record<string, unknown>)
          : null;
      const matchedRaw = result?.matched;
      const matched =
        typeof matchedRaw === "number" && Number.isFinite(matchedRaw)
          ? Math.trunc(matchedRaw)
          : 0;
      const terminated = Array.isArray(result?.terminated)
        ? result.terminated.length
        : 0;
      const failed = Array.isArray(result?.failed) ? result.failed.length : 0;
      notifications.show({
        color: dryRun ? "blue" : failed > 0 ? "yellow" : "teal",
        title: dryRun ? "Orphan cleanup dry-run" : "Orphan cleanup executed",
        message: `matched=${matched} terminated=${terminated} failed=${failed}`,
      });
      await refreshInstanceRuntime();
      return result;
    } catch (error) {
      notifications.show({
        color: "red",
        title: "Instance cleanup failed",
        message: error instanceof Error ? error.message : String(error),
      });
      await refreshInstanceRuntime();
      return null;
    } finally {
      setInstanceCleanupBusy(false);
    }
  };

  return {
    refreshDevices,
    refreshStreams,
    loadGatewayRuntimeSettings,
    refreshInstanceRuntime,
    runInstanceCleanup,
  };
}
