import { notifications } from "@mantine/notifications";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiResponse } from "../../api";
import type { CapabilityMember, ProcessStatus } from "../../types";
import { formatApiErrorToastMessage } from "../common/api_error";
import { normalizeStringList } from "../common/normalize";
import {
  isProcessRpcStateAvailable,
  processStateColor,
  supportsProcessCapability,
} from "../runtime/helpers";
import type {
  InfluxDestinationInfo,
  InfluxMeasurementResolutionRow,
  InfluxWriterStatus,
} from "./types";

type UseInfluxControllerArgs = {
  influxWriterProcess: ProcessStatus | null;
  capabilitiesByProcess: Record<string, CapabilityMember[]>;
  processCapabilitiesErrorById: Record<string, string>;
  callProcessFn: (
    processId: string,
    action: string,
    params: Record<string, unknown>
  ) => Promise<ApiResponse<unknown>>;
  sendProcessCommand: (
    processId: string,
    action: string,
    params: Record<string, unknown>,
    source: string
  ) => Promise<ApiResponse<unknown>>;
  ensureProcessCapabilitiesLoaded: (processId: string) => Promise<CapabilityMember[]>;
};

function parseNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function parseNullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function parseString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function parseStringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function parseStringMap(value: unknown): Record<string, string> {
  if (!value || typeof value !== "object") {
    return {};
  }
  const out: Record<string, string> = {};
  for (const [key, raw] of Object.entries(value as Record<string, unknown>)) {
    const text = String(raw ?? "").trim();
    if (text.length > 0) {
      out[key] = text;
    }
  }
  return out;
}

function normalizeDestinationInfo(raw: unknown): InfluxDestinationInfo | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  const name = parseString(obj.name).trim();
  if (!name) {
    return null;
  }
  const portRaw = parseNullableNumber(obj.port);
  const port =
    portRaw !== null && Number.isFinite(portRaw) ? Math.trunc(portRaw) : null;
  const timeoutRaw = parseNullableNumber(obj.request_timeout_s);
  return {
    name,
    url: parseString(obj.url),
    scheme: parseString(obj.scheme),
    host: parseString(obj.host),
    port,
    org: parseString(obj.org),
    bucket: parseString(obj.bucket),
    precision: parseString(obj.precision),
    measurement: parseString(obj.measurement),
    requestTimeoutS: timeoutRaw,
    staticTags: parseStringMap(obj.static_tags),
    tokenPresent: obj.token_present === true,
  };
}

function normalizeMeasurementResolutionRow(
  raw: unknown
): InfluxMeasurementResolutionRow | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  const deviceId = parseString(obj.device_id).trim();
  const destination = parseString(obj.destination).trim();
  const measurement = parseString(obj.measurement).trim();
  if (!deviceId || !destination || !measurement) {
    return null;
  }
  return {
    deviceId,
    deviceType: parseStringOrNull(obj.device_type),
    destination,
    measurement,
    routeMeasurement: parseStringOrNull(obj.route_measurement),
    routeDeviceType: parseStringOrNull(obj.route_device_type),
  };
}

function normalizeInfluxStatus(result: Record<string, unknown>): InfluxWriterStatus {
  const countersRaw =
    result.counters && typeof result.counters === "object"
      ? (result.counters as Record<string, unknown>)
      : {};
  const destinationsInfoRaw = Array.isArray(result.destinations_info)
    ? result.destinations_info
    : [];
  const destinationsInfo = destinationsInfoRaw
    .map((item) => normalizeDestinationInfo(item))
    .filter((item): item is InfluxDestinationInfo => item !== null);
  const measurementResolutionRaw = Array.isArray(result.measurement_resolution)
    ? result.measurement_resolution
    : [];
  const measurementResolution = measurementResolutionRaw
    .map((item) => normalizeMeasurementResolutionRow(item))
    .filter((item): item is InfluxMeasurementResolutionRow => item !== null);
  return {
    enabled: result.enabled === true,
    instanceId: parseStringOrNull(result.instance_id),
    defaultDestination: parseStringOrNull(result.default_destination),
    destinations: normalizeStringList(result.destinations),
    destinationsInfo,
    measurementResolution,
    routesCount: Math.max(0, Math.trunc(parseNumber(result.routes_count, 0))),
    disabledDevices: normalizeStringList(result.disabled_devices),
    queueDepth: Math.max(0, Math.trunc(parseNumber(result.queue_depth, 0))),
    queueCapacity: Math.max(0, Math.trunc(parseNumber(result.queue_capacity, 0))),
    overflowPolicy: parseString(result.overflow_policy),
    batchMaxPoints: Math.max(0, Math.trunc(parseNumber(result.batch_max_points, 0))),
    flushIntervalS: Math.max(0, parseNumber(result.flush_interval_s, 0)),
    includeQualityFields: result.include_quality_fields === true,
    includeUnitFields: result.include_unit_fields === true,
    deviceTagKeys: normalizeStringList(result.device_tag_keys),
    counters: {
      pointsReceived: Math.max(
        0,
        Math.trunc(parseNumber(countersRaw.points_received, 0))
      ),
      pointsQueued: Math.max(
        0,
        Math.trunc(parseNumber(countersRaw.points_queued, 0))
      ),
      pointsWritten: Math.max(
        0,
        Math.trunc(parseNumber(countersRaw.points_written, 0))
      ),
      pointsSkippedInvalid: Math.max(
        0,
        Math.trunc(parseNumber(countersRaw.points_skipped_invalid, 0))
      ),
      pointsSkippedRemote: Math.max(
        0,
        Math.trunc(parseNumber(countersRaw.points_skipped_remote, 0))
      ),
      pointsDroppedOverflow: Math.max(
        0,
        Math.trunc(parseNumber(countersRaw.points_dropped_overflow, 0))
      ),
      writeErrors: Math.max(0, Math.trunc(parseNumber(countersRaw.write_errors, 0))),
      batchesWritten: Math.max(
        0,
        Math.trunc(parseNumber(countersRaw.batches_written, 0))
      ),
    },
    lastError: parseStringOrNull(result.last_error),
    lastFlushWallS: parseNullableNumber(
      (result.last_flush as Record<string, unknown> | undefined)?.t_wall
    ),
    lastFlushMonoS: parseNullableNumber(
      (result.last_flush as Record<string, unknown> | undefined)?.t_mono
    ),
    deviceTypeKnownCount: Math.max(
      0,
      Math.trunc(parseNumber(result.device_type_known_count, 0))
    ),
    remoteDeviceKnownCount: Math.max(
      0,
      Math.trunc(parseNumber(result.remote_device_known_count, 0))
    ),
    error: null,
  };
}

export function useInfluxController({
  influxWriterProcess,
  capabilitiesByProcess,
  processCapabilitiesErrorById,
  callProcessFn,
  sendProcessCommand,
  ensureProcessCapabilitiesLoaded,
}: UseInfluxControllerArgs) {
  const [influxModalOpen, setInfluxModalOpen] = useState(false);
  const [influxStatusByProcessId, setInfluxStatusByProcessId] = useState<
    Record<string, InfluxWriterStatus>
  >({});
  const [influxStatusLoadingByProcessId, setInfluxStatusLoadingByProcessId] =
    useState<Record<string, boolean>>({});
  const [influxCommandBusyByAction, setInfluxCommandBusyByAction] = useState<
    Record<string, boolean>
  >({});
  const influxStatusByProcessIdRef = useRef(influxStatusByProcessId);

  useEffect(() => {
    influxStatusByProcessIdRef.current = influxStatusByProcessId;
  }, [influxStatusByProcessId]);

  const influxWriterProcessId = influxWriterProcess?.process_id ?? null;
  const influxCapabilities = influxWriterProcessId
    ? capabilitiesByProcess[influxWriterProcessId] ?? []
    : [];
  const influxWriterStatus = influxWriterProcessId
    ? influxStatusByProcessId[influxWriterProcessId] ?? null
    : null;
  const influxWriterLoading = influxWriterProcessId
    ? Boolean(influxStatusLoadingByProcessId[influxWriterProcessId])
    : false;
  const influxWriterState = String(influxWriterProcess?.state ?? "UNKNOWN").toUpperCase();
  const influxRpcAvailable = influxWriterProcess
    ? isProcessRpcStateAvailable(influxWriterProcess)
    : false;
  const influxCommandsBlocked = !influxWriterProcessId || !influxRpcAvailable;
  const influxProcessCapabilitiesError = influxWriterProcessId
    ? processCapabilitiesErrorById[influxWriterProcessId]
    : null;
  const influxSupportsStatus = supportsProcessCapability(
    influxCapabilities,
    "influx.status"
  );
  const influxSupportsEnable = supportsProcessCapability(
    influxCapabilities,
    "influx.enable"
  );
  const influxSupportsDisable = supportsProcessCapability(
    influxCapabilities,
    "influx.disable"
  );
  const influxSupportsFlush = supportsProcessCapability(
    influxCapabilities,
    "influx.flush"
  );
  const influxAnyCommandBusy = Object.values(influxCommandBusyByAction).some(Boolean);
  const influxStatusBusy = Boolean(influxCommandBusyByAction["influx.status"]);
  const influxEnableBusy = Boolean(influxCommandBusyByAction["influx.enable"]);
  const influxDisableBusy = Boolean(influxCommandBusyByAction["influx.disable"]);
  const influxFlushBusy = Boolean(influxCommandBusyByAction["influx.flush"]);
  const influxWriterChipColor = influxWriterStatus?.enabled === true ? "teal" : "gray";
  const influxChipLabel = "Influx";

  const setInfluxStatusLoading = useCallback((processId: string, loading: boolean) => {
    setInfluxStatusLoadingByProcessId((prev) => {
      if (prev[processId] === loading) {
        return prev;
      }
      return { ...prev, [processId]: loading };
    });
  }, []);

  const refreshInfluxStatus = useCallback(
    async (processId: string) => {
      const hasStatus = Boolean(influxStatusByProcessIdRef.current[processId]);
      if (!hasStatus) {
        setInfluxStatusLoading(processId, true);
      }
      try {
        const resp = await callProcessFn(processId, "influx.status", {});
        if (!resp.ok || !resp.result || typeof resp.result !== "object") {
          const previous = influxStatusByProcessIdRef.current[processId];
          const fallback: InfluxWriterStatus = previous ?? {
            enabled: false,
            instanceId: null,
            defaultDestination: null,
            destinations: [],
            destinationsInfo: [],
            measurementResolution: [],
            routesCount: 0,
            disabledDevices: [],
            queueDepth: 0,
            queueCapacity: 0,
            overflowPolicy: "",
            batchMaxPoints: 0,
            flushIntervalS: 0,
            includeQualityFields: false,
            includeUnitFields: false,
            deviceTagKeys: [],
            counters: {
              pointsReceived: 0,
              pointsQueued: 0,
              pointsWritten: 0,
              pointsSkippedInvalid: 0,
              pointsSkippedRemote: 0,
              pointsDroppedOverflow: 0,
              writeErrors: 0,
              batchesWritten: 0,
            },
            lastError: null,
            lastFlushWallS: null,
            lastFlushMonoS: null,
            deviceTypeKnownCount: 0,
            remoteDeviceKnownCount: 0,
            error: null,
          };
          const message = resp.error?.message ?? resp.error?.code ?? "influx.status failed";
          setInfluxStatusByProcessId((prev) => ({
            ...prev,
            [processId]: { ...fallback, error: message },
          }));
          return;
        }
        const normalized = normalizeInfluxStatus(resp.result as Record<string, unknown>);
        setInfluxStatusByProcessId((prev) => ({
          ...prev,
          [processId]: normalized,
        }));
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        const previous = influxStatusByProcessIdRef.current[processId];
        if (previous) {
          setInfluxStatusByProcessId((prev) => ({
            ...prev,
            [processId]: { ...previous, error: message },
          }));
        }
      } finally {
        if (!hasStatus) {
          setInfluxStatusLoading(processId, false);
        }
      }
    },
    [callProcessFn, setInfluxStatusLoading]
  );

  const setInfluxCommandBusy = useCallback((action: string, busy: boolean) => {
    setInfluxCommandBusyByAction((prev) => {
      if (prev[action] === busy) {
        return prev;
      }
      return { ...prev, [action]: busy };
    });
  }, []);

  const runInfluxCommand = useCallback(
    async (action: string, params: Record<string, unknown>, successTitle: string) => {
      if (!influxWriterProcessId) {
        return false;
      }
      if (influxCommandBusyByAction[action]) {
        return false;
      }
      setInfluxCommandBusy(action, true);
      try {
        const resp = await sendProcessCommand(
          influxWriterProcessId,
          action,
          params,
          `influx-modal:${action}`
        );
        if (!resp.ok) {
          notifications.show({
            color: "red",
            title: `Influx command failed (${action})`,
            message: formatApiErrorToastMessage(resp.error, {
              targetKind: "process",
              targetId: influxWriterProcessId,
              action,
            }),
            autoClose: 15000,
          });
          return false;
        }
        notifications.show({
          color: "teal",
          title: successTitle,
          message: `${influxWriterProcessId}.${action}`,
        });
        await refreshInfluxStatus(influxWriterProcessId);
        return true;
      } finally {
        setInfluxCommandBusy(action, false);
      }
    },
    [
      influxWriterProcessId,
      influxCommandBusyByAction,
      setInfluxCommandBusy,
      sendProcessCommand,
      refreshInfluxStatus,
    ]
  );

  const executeInfluxStatus = useCallback(async () => {
    if (!influxWriterProcessId) {
      return;
    }
    await refreshInfluxStatus(influxWriterProcessId);
  }, [influxWriterProcessId, refreshInfluxStatus]);

  const executeInfluxEnable = useCallback(async () => {
    await runInfluxCommand("influx.enable", {}, "Influx writer enabled");
  }, [runInfluxCommand]);

  const executeInfluxDisable = useCallback(async () => {
    await runInfluxCommand("influx.disable", {}, "Influx writer disabled");
  }, [runInfluxCommand]);

  const executeInfluxFlush = useCallback(async () => {
    await runInfluxCommand("influx.flush", {}, "Influx queue flushed");
  }, [runInfluxCommand]);

  const openInfluxWriterCommands = useCallback(async () => {
    if (!influxWriterProcessId) {
      return;
    }
    setInfluxModalOpen(true);
    const members = await ensureProcessCapabilitiesLoaded(influxWriterProcessId);
    await refreshInfluxStatus(influxWriterProcessId);
    if (members.length === 0) {
      notifications.show({
        color: "red",
        title: "Influx commands unavailable",
        message:
          processCapabilitiesErrorById[influxWriterProcessId] ??
          "Process RPC endpoint is not ready.",
      });
    }
  }, [
    influxWriterProcessId,
    ensureProcessCapabilitiesLoaded,
    refreshInfluxStatus,
    processCapabilitiesErrorById,
  ]);

  useEffect(() => {
    if (!influxWriterProcessId) {
      return;
    }
    const state = String(influxWriterProcess?.state ?? "").toUpperCase();
    if (!["RUNNING", "STARTING", "STOPPING"].includes(state)) {
      return;
    }
    let alive = true;
    const load = async () => {
      if (!alive) {
        return;
      }
      await refreshInfluxStatus(influxWriterProcessId);
    };
    void load();
    const interval = setInterval(() => {
      void load();
    }, 5000);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [influxWriterProcessId, influxWriterProcess?.state, refreshInfluxStatus]);

  useEffect(() => {
    if (!influxWriterProcess && influxModalOpen) {
      setInfluxModalOpen(false);
    }
  }, [influxWriterProcess, influxModalOpen]);

  return {
    influxModalOpen,
    setInfluxModalOpen,
    influxWriterProcessId,
    influxWriterStatus,
    influxWriterLoading,
    influxWriterState,
    influxWriterChipColor,
    influxChipLabel,
    influxCommandsBlocked,
    influxProcessCapabilitiesError,
    influxSupportsStatus,
    influxSupportsEnable,
    influxSupportsDisable,
    influxSupportsFlush,
    influxAnyCommandBusy,
    influxStatusBusy,
    influxEnableBusy,
    influxDisableBusy,
    influxFlushBusy,
    refreshInfluxStatus,
    openInfluxWriterCommands,
    executeInfluxStatus,
    executeInfluxEnable,
    executeInfluxDisable,
    executeInfluxFlush,
  };
}
