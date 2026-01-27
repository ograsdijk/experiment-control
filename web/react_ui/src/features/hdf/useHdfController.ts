import { notifications } from "@mantine/notifications";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ApiResponse } from "../../api";
import type { CapabilityMember, DeviceStatus, ProcessStatus } from "../../types";
import { formatApiErrorToastMessage } from "../common/api_error";
import { normalizeStringList } from "../common/normalize";
import {
  fileNameFromPath,
  isProcessRpcStateAvailable,
  processStateColor,
  supportsProcessCapability,
} from "../runtime/helpers";
import type {
  HdfMeasurementSchemaState,
  HdfWriterStatus,
  MeasurementSchema,
} from "./types";
import {
  coerceMeasurementFieldValue,
  formatFieldDefaultValue,
  normalizeMeasurementSchema,
  sameHdfWriterStatus,
} from "./utils";

type UseHdfControllerArgs = {
  hdfWriterProcess: ProcessStatus | null;
  capabilitiesByProcess: Record<string, CapabilityMember[]>;
  processCapabilitiesErrorById: Record<string, string>;
  latestByDevice: Record<string, unknown>;
  deviceOrder: string[];
  devices: DeviceStatus[];
  orderedDevices: DeviceStatus[];
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
  refreshProcesses: () => Promise<ProcessStatus[]>;
  refreshDevices: () => Promise<DeviceStatus[]>;
  ensureProcessCapabilitiesLoaded: (processId: string) => Promise<CapabilityMember[]>;
};

export function useHdfController({
  hdfWriterProcess,
  capabilitiesByProcess,
  processCapabilitiesErrorById,
  latestByDevice,
  deviceOrder,
  devices,
  orderedDevices,
  callProcessFn,
  sendProcessCommand,
  refreshProcesses,
  refreshDevices,
  ensureProcessCapabilitiesLoaded,
}: UseHdfControllerArgs) {
  const [hdfModalOpen, setHdfModalOpen] = useState(false);
  const [hdfNoteModalOpen, setHdfNoteModalOpen] = useState(false);
  const [hdfRotateFilenameDraft, setHdfRotateFilenameDraft] = useState("");
  const [hdfRotateDisabledDevicesDraft, setHdfRotateDisabledDevicesDraft] =
    useState<string[]>([]);
  const [hdfEnableDevicesDraft, setHdfEnableDevicesDraft] = useState<string[]>([]);
  const [hdfDisableDevicesDraft, setHdfDisableDevicesDraft] = useState<string[]>([]);
  const [hdfMeasurementSchemaByProcessId, setHdfMeasurementSchemaByProcessId] =
    useState<Record<string, HdfMeasurementSchemaState>>({});
  const [
    hdfMeasurementSchemaLoadingByProcessId,
    setHdfMeasurementSchemaLoadingByProcessId,
  ] = useState<Record<string, boolean>>({});
  const [hdfRotateMeasurementProfileDraft, setHdfRotateMeasurementProfileDraft] =
    useState<string | null>(null);
  const [hdfRotateMeasurementValuesDraft, setHdfRotateMeasurementValuesDraft] =
    useState<Record<string, string>>({});
  const [hdfRotateMeasurementCustomByField, setHdfRotateMeasurementCustomByField] =
    useState<Record<string, boolean>>({});
  const [hdfNoteValuesDraft, setHdfNoteValuesDraft] = useState<
    Record<string, string>
  >({});
  const [hdfNoteCustomByField, setHdfNoteCustomByField] = useState<
    Record<string, boolean>
  >({});
  const [hdfLastNoteAuthor, setHdfLastNoteAuthor] = useState("");
  const [hdfCommandBusyByAction, setHdfCommandBusyByAction] = useState<
    Record<string, boolean>
  >({});
  const [hdfStatusByProcessId, setHdfStatusByProcessId] = useState<
    Record<string, HdfWriterStatus>
  >({});
  const [hdfStatusLoadingByProcessId, setHdfStatusLoadingByProcessId] =
    useState<Record<string, boolean>>({});
  const hdfMeasurementIdRef = useRef<string | null>(null);
  const hdfStatusByProcessIdRef = useRef(hdfStatusByProcessId);

  useEffect(() => {
    hdfStatusByProcessIdRef.current = hdfStatusByProcessId;
  }, [hdfStatusByProcessId]);

  const hdfWriterProcessId = hdfWriterProcess?.process_id ?? null;
  const hdfCapabilities = hdfWriterProcessId
    ? capabilitiesByProcess[hdfWriterProcessId] ?? []
    : [];
  const hdfWriterStatus = hdfWriterProcess
    ? hdfStatusByProcessId[hdfWriterProcess.process_id]
    : undefined;
  const hdfWriterLoading = hdfWriterProcess
    ? Boolean(hdfStatusLoadingByProcessId[hdfWriterProcess.process_id])
    : false;
  const hdfWriterState = String(hdfWriterProcess?.state ?? "UNKNOWN").toUpperCase();
  const hdfRpcAvailable = hdfWriterProcess
    ? isProcessRpcStateAvailable(hdfWriterProcess)
    : false;
  const hdfProcessCapabilitiesError = hdfWriterProcessId
    ? processCapabilitiesErrorById[hdfWriterProcessId]
    : undefined;
  const hdfSupportsStatus = supportsProcessCapability(hdfCapabilities, "hdf.status");
  const hdfSupportsDevicesGet = supportsProcessCapability(
    hdfCapabilities,
    "hdf.devices.get"
  );
  const hdfSupportsDevicesEnable = supportsProcessCapability(
    hdfCapabilities,
    "hdf.devices.enable"
  );
  const hdfSupportsDevicesDisable = supportsProcessCapability(
    hdfCapabilities,
    "hdf.devices.disable"
  );
  const hdfSupportsRotate = supportsProcessCapability(hdfCapabilities, "hdf.rotate");
  const hdfSupportsMeasurementSchemaGet = supportsProcessCapability(
    hdfCapabilities,
    "hdf.measurement.schema.get"
  );
  const hdfSupportsMeasurementNote = supportsProcessCapability(
    hdfCapabilities,
    "hdf.measurement.note"
  );
  const hdfStatusBusy = Boolean(hdfCommandBusyByAction["hdf.status"]);
  const hdfDevicesGetBusy = Boolean(hdfCommandBusyByAction["hdf.devices.get"]);
  const hdfDevicesEnableBusy = Boolean(hdfCommandBusyByAction["hdf.devices.enable"]);
  const hdfDevicesDisableBusy = Boolean(hdfCommandBusyByAction["hdf.devices.disable"]);
  const hdfRotateBusy = Boolean(hdfCommandBusyByAction["hdf.rotate"]);
  const hdfMeasurementNoteBusy = Boolean(hdfCommandBusyByAction["hdf.measurement.note"]);
  const hdfAnyCommandBusy = Object.values(hdfCommandBusyByAction).some(Boolean);
  const hdfCommandsBlocked = !hdfWriterProcessId || !hdfRpcAvailable;
  const hdfMeasurementSchemaState = hdfWriterProcessId
    ? hdfMeasurementSchemaByProcessId[hdfWriterProcessId]
    : undefined;
  const hdfMeasurementSchemaLoading = hdfWriterProcessId
    ? Boolean(hdfMeasurementSchemaLoadingByProcessId[hdfWriterProcessId])
    : false;
  const hdfMeasurementSchema = hdfMeasurementSchemaState?.schema ?? null;
  const hdfMeasurementSchemaConfigured = Boolean(
    hdfWriterStatus?.measurementSchemaConfigured
  );
  const hdfMeasurementSchemaAvailable = Boolean(
    hdfWriterStatus?.measurementSchemaAvailable
  );
  const hdfShowMeasurementUi =
    hdfMeasurementSchemaConfigured &&
    hdfMeasurementSchemaAvailable &&
    hdfMeasurementSchema !== null;
  const hdfMeasurementSchemaDisplayPath =
    hdfMeasurementSchemaState?.path ?? hdfWriterStatus?.measurementSchemaPath ?? null;
  const hdfMeasurementSchemaDisplayError =
    hdfMeasurementSchemaState?.error ?? hdfWriterStatus?.measurementSchemaError ?? null;
  const hdfRotateSelectedProfile =
    hdfMeasurementSchema && hdfRotateMeasurementProfileDraft
      ? hdfMeasurementSchema.profiles.find(
          (profile) => profile.id === hdfRotateMeasurementProfileDraft
        ) ?? null
      : null;
  const hdfRotateProfileOptions =
    hdfMeasurementSchema?.profiles.map((profile) => ({
      value: profile.id,
      label: profile.label || profile.id,
    })) ?? [];
  const hdfShowNoteChiplet =
    Boolean(hdfWriterProcess) &&
    hdfSupportsMeasurementNote &&
    hdfShowMeasurementUi &&
    (hdfMeasurementSchema?.notes.fields.length ?? 0) > 0;
  const hdfWriterFileLabel =
    hdfWriterStatus?.fileName ?? (hdfWriterStatus?.error ? "status unavailable" : "no file");
  const hdfWriterChipColor = processStateColor(hdfWriterState);

  const setHdfStatusLoading = useCallback((processId: string, loading: boolean) => {
    setHdfStatusLoadingByProcessId((prev) => {
      if (prev[processId] === loading) {
        return prev;
      }
      return { ...prev, [processId]: loading };
    });
  }, []);

  const setHdfMeasurementSchemaLoading = useCallback(
    (processId: string, loading: boolean) => {
      setHdfMeasurementSchemaLoadingByProcessId((prev) => {
        if (prev[processId] === loading) {
          return prev;
        }
        return { ...prev, [processId]: loading };
      });
    },
    []
  );

  const refreshHdfWriterStatus = useCallback(
    async (processId: string) => {
      const hasExistingStatus = Boolean(hdfStatusByProcessIdRef.current[processId]);
      if (!hasExistingStatus) {
        setHdfStatusLoading(processId, true);
      }
      try {
        const resp = await callProcessFn(processId, "hdf.status", {});
        if (!resp.ok || !resp.result || typeof resp.result !== "object") {
          const code = resp.error?.code ?? null;
          const message = resp.error?.message ?? null;
          setHdfStatusByProcessId((prev) => {
            const current = prev[processId];
            const nextStatus: HdfWriterStatus = {
              filePath: current?.filePath ?? null,
              fileName: current?.fileName ?? null,
              pending: current?.pending ?? null,
              dropped: current?.dropped ?? null,
              droppedEvents: current?.droppedEvents ?? null,
              disabledDevices: current?.disabledDevices ?? [],
              knownDevices: current?.knownDevices ?? [],
              enabledKnownDevices: current?.enabledKnownDevices ?? [],
              measurementId: current?.measurementId ?? null,
              measurementType: current?.measurementType ?? null,
              measurementSchemaVersion: current?.measurementSchemaVersion ?? null,
              measurementStartedWallNs: current?.measurementStartedWallNs ?? null,
              measurementEndedWallNs: current?.measurementEndedWallNs ?? null,
              measurementNotesRows: current?.measurementNotesRows ?? 0,
              measurementSchemaConfigured: current?.measurementSchemaConfigured ?? false,
              measurementSchemaAvailable: current?.measurementSchemaAvailable ?? false,
              measurementSchemaPath: current?.measurementSchemaPath ?? null,
              measurementSchemaError: current?.measurementSchemaError ?? null,
              error: message ?? code ?? "hdf.status failed",
            };
            if (sameHdfWriterStatus(current, nextStatus)) {
              return prev;
            }
            return { ...prev, [processId]: nextStatus };
          });
          return;
        }
        const result = resp.result as {
          file?: unknown;
          pending?: unknown;
          dropped?: unknown;
          dropped_events?: unknown;
          disabled_devices?: unknown;
          known_devices?: unknown;
          enabled_known_devices?: unknown;
          measurement_id?: unknown;
          measurement_type?: unknown;
          measurement_schema_version?: unknown;
          measurement_started_wall_ns?: unknown;
          measurement_ended_wall_ns?: unknown;
          measurement_notes_rows?: unknown;
          measurement_schema_configured?: unknown;
          measurement_schema_available?: unknown;
          measurement_schema_path?: unknown;
          measurement_schema_error?: unknown;
        };
        const filePath = typeof result.file === "string" ? result.file : null;
        const pending =
          typeof result.pending === "number" && Number.isFinite(result.pending)
            ? Math.trunc(result.pending)
            : null;
        const dropped =
          typeof result.dropped === "number" && Number.isFinite(result.dropped)
            ? Math.trunc(result.dropped)
            : null;
        const droppedEvents =
          typeof result.dropped_events === "number" &&
          Number.isFinite(result.dropped_events)
            ? Math.trunc(result.dropped_events)
            : null;
        const disabledDevices = normalizeStringList(result.disabled_devices);
        const knownDevices = normalizeStringList(result.known_devices);
        const enabledKnownDevices = normalizeStringList(result.enabled_known_devices);
        const measurementId =
          typeof result.measurement_id === "string" &&
          result.measurement_id.trim().length > 0
            ? result.measurement_id
            : null;
        const measurementType =
          typeof result.measurement_type === "string" &&
          result.measurement_type.trim().length > 0
            ? result.measurement_type
            : null;
        const measurementSchemaVersion =
          typeof result.measurement_schema_version === "number" &&
          Number.isFinite(result.measurement_schema_version)
            ? Math.trunc(result.measurement_schema_version)
            : null;
        const measurementStartedWallNs =
          typeof result.measurement_started_wall_ns === "number" &&
          Number.isFinite(result.measurement_started_wall_ns)
            ? Math.trunc(result.measurement_started_wall_ns)
            : null;
        const measurementEndedWallNs =
          typeof result.measurement_ended_wall_ns === "number" &&
          Number.isFinite(result.measurement_ended_wall_ns)
            ? Math.trunc(result.measurement_ended_wall_ns)
            : null;
        const measurementNotesRows =
          typeof result.measurement_notes_rows === "number" &&
          Number.isFinite(result.measurement_notes_rows)
            ? Math.max(0, Math.trunc(result.measurement_notes_rows))
            : 0;
        const measurementSchemaConfigured = result.measurement_schema_configured === true;
        const measurementSchemaAvailable = result.measurement_schema_available === true;
        const measurementSchemaPath =
          typeof result.measurement_schema_path === "string" &&
          result.measurement_schema_path.trim().length > 0
            ? result.measurement_schema_path
            : null;
        const measurementSchemaError =
          typeof result.measurement_schema_error === "string" &&
          result.measurement_schema_error.trim().length > 0
            ? result.measurement_schema_error
            : null;
        setHdfStatusByProcessId((prev) => {
          const current = prev[processId];
          const nextStatus: HdfWriterStatus = {
            filePath,
            fileName: fileNameFromPath(filePath),
            pending,
            dropped,
            droppedEvents,
            disabledDevices,
            knownDevices,
            enabledKnownDevices,
            measurementId,
            measurementType,
            measurementSchemaVersion,
            measurementStartedWallNs,
            measurementEndedWallNs,
            measurementNotesRows,
            measurementSchemaConfigured,
            measurementSchemaAvailable,
            measurementSchemaPath,
            measurementSchemaError,
            error: null,
          };
          if (sameHdfWriterStatus(current, nextStatus)) {
            return prev;
          }
          return { ...prev, [processId]: nextStatus };
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setHdfStatusByProcessId((prev) => {
          const current = prev[processId];
          const nextStatus: HdfWriterStatus = {
            filePath: current?.filePath ?? null,
            fileName: current?.fileName ?? null,
            pending: current?.pending ?? null,
            dropped: current?.dropped ?? null,
            droppedEvents: current?.droppedEvents ?? null,
            disabledDevices: current?.disabledDevices ?? [],
            knownDevices: current?.knownDevices ?? [],
            enabledKnownDevices: current?.enabledKnownDevices ?? [],
            measurementId: current?.measurementId ?? null,
            measurementType: current?.measurementType ?? null,
            measurementSchemaVersion: current?.measurementSchemaVersion ?? null,
            measurementStartedWallNs: current?.measurementStartedWallNs ?? null,
            measurementEndedWallNs: current?.measurementEndedWallNs ?? null,
            measurementNotesRows: current?.measurementNotesRows ?? 0,
            measurementSchemaConfigured: current?.measurementSchemaConfigured ?? false,
            measurementSchemaAvailable: current?.measurementSchemaAvailable ?? false,
            measurementSchemaPath: current?.measurementSchemaPath ?? null,
            measurementSchemaError: current?.measurementSchemaError ?? null,
            error: message,
          };
          if (sameHdfWriterStatus(current, nextStatus)) {
            return prev;
          }
          return { ...prev, [processId]: nextStatus };
        });
      } finally {
        if (!hasExistingStatus) {
          setHdfStatusLoading(processId, false);
        }
      }
    },
    [callProcessFn, setHdfStatusLoading]
  );

  const applyRotateProfileDraft = useCallback(
    (schema: MeasurementSchema, profileId: string, options?: { preserveValues?: boolean }) => {
      const profile = schema.profiles.find((item) => item.id === profileId) ?? null;
      if (!profile) {
        setHdfRotateMeasurementProfileDraft(null);
        setHdfRotateMeasurementValuesDraft({});
        setHdfRotateMeasurementCustomByField({});
        return;
      }
      const nextValues: Record<string, string> = {};
      const nextCustom: Record<string, boolean> = {};
      const currentValues = hdfRotateMeasurementValuesDraft;
      const currentCustom = hdfRotateMeasurementCustomByField;
      for (const field of profile.fields) {
        const preserved =
          options?.preserveValues === true ? currentValues[field.key] : undefined;
        let value = typeof preserved === "string" ? preserved : "";
        if (!value && field.hasDefault) {
          value = formatFieldDefaultValue(field.defaultValue);
        }
        if (!value && field.options.length > 0 && !field.allowCustom) {
          value = field.options[0] ?? "";
        }
        nextValues[field.key] = value;
        if (field.options.length > 0 && field.allowCustom) {
          const preservedCustom =
            options?.preserveValues === true ? currentCustom[field.key] === true : false;
          nextCustom[field.key] =
            preservedCustom || (value.length > 0 && !field.options.includes(value));
        } else {
          nextCustom[field.key] = false;
        }
      }
      setHdfRotateMeasurementProfileDraft(profile.id);
      setHdfRotateMeasurementValuesDraft(nextValues);
      setHdfRotateMeasurementCustomByField(nextCustom);
    },
    [hdfRotateMeasurementCustomByField, hdfRotateMeasurementValuesDraft]
  );

  const applyMeasurementNoteDraft = useCallback(
    (schema: MeasurementSchema, options?: { preserveValues?: boolean }) => {
      const fields = schema.notes.fields;
      const nextValues: Record<string, string> = {};
      const nextCustom: Record<string, boolean> = {};
      const currentValues = hdfNoteValuesDraft;
      const currentCustom = hdfNoteCustomByField;
      for (const field of fields) {
        const preserved =
          options?.preserveValues === true ? currentValues[field.key] : undefined;
        let value = typeof preserved === "string" ? preserved : "";
        if (!value && field.key === "author" && hdfLastNoteAuthor) {
          value = hdfLastNoteAuthor;
        }
        if (!value && field.hasDefault) {
          value = formatFieldDefaultValue(field.defaultValue);
        }
        if (!value && field.options.length > 0 && !field.allowCustom) {
          value = field.options[0] ?? "";
        }
        nextValues[field.key] = value;
        if (field.options.length > 0 && field.allowCustom) {
          const preservedCustom =
            options?.preserveValues === true ? currentCustom[field.key] === true : false;
          nextCustom[field.key] =
            preservedCustom || (value.length > 0 && !field.options.includes(value));
        } else {
          nextCustom[field.key] = false;
        }
      }
      setHdfNoteValuesDraft(nextValues);
      setHdfNoteCustomByField(nextCustom);
    },
    [hdfLastNoteAuthor, hdfNoteCustomByField, hdfNoteValuesDraft]
  );

  const fetchHdfMeasurementSchema = useCallback(
    async (processId: string, options?: { silent?: boolean }) => {
      setHdfMeasurementSchemaLoading(processId, true);
      try {
        const resp = await callProcessFn(processId, "hdf.measurement.schema.get", {});
        if (!resp.ok) {
          const code = resp.error?.code ?? "";
          if (code === "measurement_schema_not_configured") {
            setHdfMeasurementSchemaByProcessId((prev) => ({
              ...prev,
              [processId]: { schema: null, path: null, error: null },
            }));
            return;
          }
          if (code === "measurement_schema_unavailable") {
            setHdfMeasurementSchemaByProcessId((prev) => ({
              ...prev,
              [processId]: {
                schema: null,
                path: null,
                error:
                  resp.error?.message ??
                  resp.error?.code ??
                  "measurement schema unavailable",
              },
            }));
            return;
          }
          const message = resp.error?.message ?? resp.error?.code ?? "Unknown error";
          if (!options?.silent) {
            notifications.show({
              color: "red",
              title: "Measurement schema fetch failed",
              message,
            });
          }
          setHdfMeasurementSchemaByProcessId((prev) => ({
            ...prev,
            [processId]: { schema: null, path: null, error: message },
          }));
          return;
        }
        const result =
          resp.result && typeof resp.result === "object" ? resp.result : null;
        const schemaRaw =
          result && typeof result === "object"
            ? (result as { schema?: unknown }).schema
            : null;
        const normalized = normalizeMeasurementSchema(schemaRaw);
        if (!normalized) {
          const message = "Invalid schema payload";
          if (!options?.silent) {
            notifications.show({
              color: "red",
              title: "Measurement schema fetch failed",
              message,
            });
          }
          setHdfMeasurementSchemaByProcessId((prev) => ({
            ...prev,
            [processId]: { schema: null, path: null, error: message },
          }));
          return;
        }
        const pathRaw =
          result && typeof result === "object"
            ? (result as { path?: unknown }).path
            : null;
        const path =
          typeof pathRaw === "string" && pathRaw.trim().length > 0
            ? pathRaw.trim()
            : null;
        setHdfMeasurementSchemaByProcessId((prev) => ({
          ...prev,
          [processId]: { schema: normalized, path, error: null },
        }));
        if (normalized.profiles.length > 0) {
          const selected =
            hdfRotateMeasurementProfileDraft &&
            normalized.profiles.some(
              (profile) => profile.id === hdfRotateMeasurementProfileDraft
            )
              ? hdfRotateMeasurementProfileDraft
              : normalized.profiles[0]?.id ?? null;
          if (selected) {
            applyRotateProfileDraft(normalized, selected, { preserveValues: true });
          }
        } else {
          setHdfRotateMeasurementProfileDraft(null);
          setHdfRotateMeasurementValuesDraft({});
          setHdfRotateMeasurementCustomByField({});
        }
        applyMeasurementNoteDraft(normalized, { preserveValues: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (!options?.silent) {
          notifications.show({
            color: "red",
            title: "Measurement schema fetch failed",
            message,
          });
        }
        setHdfMeasurementSchemaByProcessId((prev) => ({
          ...prev,
          [processId]: { schema: null, path: null, error: message },
        }));
      } finally {
        setHdfMeasurementSchemaLoading(processId, false);
      }
    },
    [
      applyMeasurementNoteDraft,
      applyRotateProfileDraft,
      callProcessFn,
      hdfRotateMeasurementProfileDraft,
      setHdfMeasurementSchemaLoading,
    ]
  );

  const openHdfWriterCommands = useCallback(async () => {
    if (!hdfWriterProcess) {
      return;
    }
    const processId = hdfWriterProcess.process_id;
    setHdfModalOpen(true);
    const [members] = await Promise.all([
      ensureProcessCapabilitiesLoaded(processId),
      refreshHdfWriterStatus(processId),
      refreshDevices(),
    ]);
    if (supportsProcessCapability(members, "hdf.measurement.schema.get")) {
      await fetchHdfMeasurementSchema(processId, { silent: true });
    } else {
      setHdfMeasurementSchemaByProcessId((prev) => ({
        ...prev,
        [processId]: { schema: null, path: null, error: null },
      }));
    }
    if (members.length === 0) {
      notifications.show({
        color: "red",
        title: "HDF commands unavailable",
        message:
          processCapabilitiesErrorById[processId] ??
          "Process RPC endpoint is not ready.",
      });
    }
  }, [
    ensureProcessCapabilitiesLoaded,
    fetchHdfMeasurementSchema,
    hdfWriterProcess,
    processCapabilitiesErrorById,
    refreshDevices,
    refreshHdfWriterStatus,
  ]);

  const openHdfMeasurementNoteModal = useCallback(async () => {
    if (!hdfWriterProcess) {
      return;
    }
    const processId = hdfWriterProcess.process_id;
    setHdfNoteModalOpen(true);
    const [members] = await Promise.all([
      ensureProcessCapabilitiesLoaded(processId),
      refreshHdfWriterStatus(processId),
    ]);
    if (supportsProcessCapability(members, "hdf.measurement.schema.get")) {
      await fetchHdfMeasurementSchema(processId, { silent: true });
    }
  }, [
    ensureProcessCapabilitiesLoaded,
    fetchHdfMeasurementSchema,
    hdfWriterProcess,
    refreshHdfWriterStatus,
  ]);

  const selectHdfRotateMeasurementProfile = useCallback(
    (profileId: string | null) => {
      if (!hdfMeasurementSchema || !profileId) {
        setHdfRotateMeasurementProfileDraft(null);
        setHdfRotateMeasurementValuesDraft({});
        setHdfRotateMeasurementCustomByField({});
        return;
      }
      applyRotateProfileDraft(hdfMeasurementSchema, profileId, { preserveValues: false });
    },
    [applyRotateProfileDraft, hdfMeasurementSchema]
  );

  const setHdfRotateFieldValue = useCallback((fieldKey: string, value: string) => {
    setHdfRotateMeasurementValuesDraft((prev) => ({ ...prev, [fieldKey]: value }));
  }, []);

  const setHdfRotateFieldUseCustom = useCallback(
    (fieldKey: string, useCustom: boolean) => {
      setHdfRotateMeasurementCustomByField((prev) => ({ ...prev, [fieldKey]: useCustom }));
    },
    []
  );

  const setHdfNoteFieldValue = useCallback((fieldKey: string, value: string) => {
    setHdfNoteValuesDraft((prev) => ({ ...prev, [fieldKey]: value }));
  }, []);

  const setHdfNoteFieldUseCustom = useCallback((fieldKey: string, useCustom: boolean) => {
    setHdfNoteCustomByField((prev) => ({ ...prev, [fieldKey]: useCustom }));
  }, []);

  const setHdfCommandBusy = useCallback((action: string, busy: boolean) => {
    setHdfCommandBusyByAction((prev) => {
      if (prev[action] === busy) {
        return prev;
      }
      return { ...prev, [action]: busy };
    });
  }, []);

  const runHdfCommand = useCallback(
    async (action: string, params: Record<string, unknown>, successTitle: string) => {
      if (!hdfWriterProcess) {
        return false;
      }
      const processId = hdfWriterProcess.process_id;
      if (hdfCommandBusyByAction[action]) {
        return false;
      }
      setHdfCommandBusy(action, true);
      try {
        const resp = await sendProcessCommand(
          processId,
          action,
          params,
          `hdf-modal:${action}`
        );
        if (!resp.ok) {
          notifications.show({
            color: "red",
            title: `HDF command failed (${action})`,
            message: formatApiErrorToastMessage(resp.error, {
              targetKind: "process",
              targetId: processId,
              action,
            }),
          });
          return false;
        }
        notifications.show({
          color: "teal",
          title: successTitle,
          message: `${processId}.${action}`,
        });
        await refreshHdfWriterStatus(processId);
        if (action === "hdf.rotate") {
          await refreshProcesses();
          setHdfLastNoteAuthor("");
          setHdfNoteValuesDraft({});
          setHdfNoteCustomByField({});
        }
        return true;
      } finally {
        setHdfCommandBusy(action, false);
      }
    },
    [
      hdfWriterProcess,
      hdfCommandBusyByAction,
      setHdfCommandBusy,
      sendProcessCommand,
      refreshHdfWriterStatus,
      refreshProcesses,
    ]
  );

  const executeHdfRotate = useCallback(async () => {
    const params: Record<string, unknown> = {};
    const filename = hdfRotateFilenameDraft.trim();
    if (filename) {
      params.filename = filename;
    }
    if (hdfRotateDisabledDevicesDraft.length > 0) {
      params.disabled_devices = normalizeStringList(hdfRotateDisabledDevicesDraft);
    }
    const schemaState = hdfWriterProcess
      ? hdfMeasurementSchemaByProcessId[hdfWriterProcess.process_id]
      : undefined;
    const schema = schemaState?.schema ?? null;
    const selectedProfile =
      schema && hdfRotateMeasurementProfileDraft
        ? schema.profiles.find((profile) => profile.id === hdfRotateMeasurementProfileDraft) ??
          null
        : null;
    if (schema && schema.profiles.length > 0) {
      if (!selectedProfile) {
        notifications.show({
          color: "red",
          title: "Missing measurement profile",
          message: "Select a measurement profile before rotating the file.",
        });
        return;
      }
      params.measurement_profile = selectedProfile.id;
      const measurementValues: Record<string, unknown> = {};
      for (const field of selectedProfile.fields) {
        try {
          const raw = hdfRotateMeasurementValuesDraft[field.key] ?? "";
          const value = coerceMeasurementFieldValue(field, raw);
          if (value === undefined) {
            if (field.required) {
              notifications.show({
                color: "red",
                title: "Missing parameter",
                message: `${field.label} is required.`,
              });
              return;
            }
            continue;
          }
          measurementValues[field.key] = value;
        } catch (error) {
          notifications.show({
            color: "red",
            title: "Invalid measurement value",
            message: error instanceof Error ? error.message : String(error),
          });
          return;
        }
      }
      params.measurement_values = measurementValues;
    }
    const ok = await runHdfCommand("hdf.rotate", params, "HDF file rotated");
    if (ok) {
      setHdfRotateFilenameDraft("");
      if (schema && schema.notes.fields.length > 0) {
        applyMeasurementNoteDraft(schema, { preserveValues: false });
      }
    }
  }, [
    hdfRotateFilenameDraft,
    hdfRotateDisabledDevicesDraft,
    hdfWriterProcess,
    hdfMeasurementSchemaByProcessId,
    hdfRotateMeasurementProfileDraft,
    hdfRotateMeasurementValuesDraft,
    runHdfCommand,
    applyMeasurementNoteDraft,
  ]);

  const executeHdfMeasurementNote = useCallback(async () => {
    if (!hdfWriterProcess) {
      return;
    }
    const schema = hdfMeasurementSchemaByProcessId[hdfWriterProcess.process_id]?.schema ?? null;
    if (!schema) {
      return;
    }
    const payload: Record<string, unknown> = {};
    for (const field of schema.notes.fields) {
      try {
        const raw = hdfNoteValuesDraft[field.key] ?? "";
        const value = coerceMeasurementFieldValue(field, raw);
        if (value === undefined) {
          if (field.required) {
            notifications.show({
              color: "red",
              title: "Missing note field",
              message: `${field.label} is required.`,
            });
            return;
          }
          continue;
        }
        payload[field.key] = value;
      } catch (error) {
        notifications.show({
          color: "red",
          title: "Invalid note field",
          message: error instanceof Error ? error.message : String(error),
        });
        return;
      }
    }
    const ok = await runHdfCommand(
      "hdf.measurement.note",
      payload,
      "Measurement note added"
    );
    if (ok) {
      const authorRaw = payload.author;
      if (typeof authorRaw === "string" && authorRaw.trim().length > 0) {
        setHdfLastNoteAuthor(authorRaw.trim());
      }
      setHdfNoteValuesDraft((prev) => {
        const next = { ...prev };
        if ("message" in next) {
          next.message = "";
        }
        return next;
      });
    }
  }, [hdfWriterProcess, hdfMeasurementSchemaByProcessId, hdfNoteValuesDraft, runHdfCommand]);

  const executeHdfStatus = useCallback(async () => {
    await runHdfCommand("hdf.status", {}, "HDF status refreshed");
  }, [runHdfCommand]);

  const executeHdfDevicesGet = useCallback(async () => {
    await runHdfCommand("hdf.devices.get", {}, "HDF device filter refreshed");
  }, [runHdfCommand]);

  const executeHdfDevicesEnable = useCallback(async () => {
    const deviceIds = normalizeStringList(hdfEnableDevicesDraft);
    if (deviceIds.length === 0) {
      notifications.show({
        color: "red",
        title: "Missing parameter",
        message: "Select one or more device IDs for hdf.devices.enable.",
      });
      return;
    }
    const ok = await runHdfCommand(
      "hdf.devices.enable",
      { device_ids: deviceIds },
      "HDF devices enabled"
    );
    if (ok) {
      setHdfEnableDevicesDraft([]);
    }
  }, [hdfEnableDevicesDraft, runHdfCommand]);

  const executeHdfDevicesDisable = useCallback(async () => {
    const deviceIds = normalizeStringList(hdfDisableDevicesDraft);
    if (deviceIds.length === 0) {
      notifications.show({
        color: "red",
        title: "Missing parameter",
        message: "Select one or more device IDs for hdf.devices.disable.",
      });
      return;
    }
    const ok = await runHdfCommand(
      "hdf.devices.disable",
      { device_ids: deviceIds },
      "HDF devices disabled"
    );
    if (ok) {
      setHdfDisableDevicesDraft([]);
    }
  }, [hdfDisableDevicesDraft, runHdfCommand]);

  const hdfSelectableDeviceIds = useMemo(() => {
    const out = new Set<string>();
    for (const deviceId of hdfWriterStatus?.knownDevices ?? []) {
      if (deviceId) {
        out.add(deviceId);
      }
    }
    for (const deviceId of hdfWriterStatus?.enabledKnownDevices ?? []) {
      if (deviceId) {
        out.add(deviceId);
      }
    }
    for (const deviceId of hdfWriterStatus?.disabledDevices ?? []) {
      if (deviceId) {
        out.add(deviceId);
      }
    }
    for (const deviceId of Object.keys(latestByDevice)) {
      if (deviceId) {
        out.add(deviceId);
      }
    }
    for (const deviceId of deviceOrder) {
      if (deviceId) {
        out.add(deviceId);
      }
    }
    for (const device of devices) {
      if (device.device_id) {
        out.add(device.device_id);
      }
    }
    for (const device of orderedDevices) {
      if (device.device_id) {
        out.add(device.device_id);
      }
    }
    for (const deviceId of hdfRotateDisabledDevicesDraft) {
      if (deviceId) {
        out.add(deviceId);
      }
    }
    for (const deviceId of hdfEnableDevicesDraft) {
      if (deviceId) {
        out.add(deviceId);
      }
    }
    for (const deviceId of hdfDisableDevicesDraft) {
      if (deviceId) {
        out.add(deviceId);
      }
    }
    return [...out].sort((a, b) => a.localeCompare(b));
  }, [
    hdfWriterStatus?.knownDevices,
    hdfWriterStatus?.enabledKnownDevices,
    hdfWriterStatus?.disabledDevices,
    latestByDevice,
    deviceOrder,
    devices,
    orderedDevices,
    hdfRotateDisabledDevicesDraft,
    hdfEnableDevicesDraft,
    hdfDisableDevicesDraft,
  ]);

  const hdfSelectableDeviceOptions = useMemo(
    () => hdfSelectableDeviceIds.map((deviceId) => ({ value: deviceId, label: deviceId })),
    [hdfSelectableDeviceIds]
  );

  useEffect(() => {
    if (!hdfWriterProcess) {
      return;
    }
    const processId = hdfWriterProcess.process_id;
    const state = String(hdfWriterProcess.state ?? "").toUpperCase();
    if (!["RUNNING", "STARTING", "STOPPING"].includes(state)) {
      return;
    }
    let alive = true;
    const load = async () => {
      if (!alive) {
        return;
      }
      await refreshHdfWriterStatus(processId);
    };
    void load();
    const interval = setInterval(() => {
      void load();
    }, 5000);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [hdfWriterProcess, refreshHdfWriterStatus]);

  useEffect(() => {
    if (!hdfWriterProcess && hdfModalOpen) {
      setHdfModalOpen(false);
    }
  }, [hdfWriterProcess, hdfModalOpen]);

  useEffect(() => {
    if (!hdfWriterProcess && hdfNoteModalOpen) {
      setHdfNoteModalOpen(false);
    }
  }, [hdfWriterProcess, hdfNoteModalOpen]);

  useEffect(() => {
    const processId = hdfWriterProcess?.process_id ?? null;
    if (!processId) {
      hdfMeasurementIdRef.current = null;
      return;
    }
    const measurementId = hdfStatusByProcessId[processId]?.measurementId ?? null;
    if (!measurementId) {
      hdfMeasurementIdRef.current = null;
      return;
    }
    const previousId = hdfMeasurementIdRef.current;
    if (previousId && previousId !== measurementId) {
      setHdfLastNoteAuthor("");
      const schema = hdfMeasurementSchemaByProcessId[processId]?.schema ?? null;
      if (schema) {
        applyMeasurementNoteDraft(schema, { preserveValues: false });
      } else {
        setHdfNoteValuesDraft({});
        setHdfNoteCustomByField({});
      }
    }
    hdfMeasurementIdRef.current = measurementId;
  }, [
    applyMeasurementNoteDraft,
    hdfMeasurementSchemaByProcessId,
    hdfStatusByProcessId,
    hdfWriterProcess,
  ]);

  useEffect(() => {
    if (!hdfModalOpen || !hdfWriterProcessId || !hdfSupportsMeasurementSchemaGet) {
      return;
    }
    void fetchHdfMeasurementSchema(hdfWriterProcessId, { silent: true });
  }, [
    fetchHdfMeasurementSchema,
    hdfModalOpen,
    hdfSupportsMeasurementSchemaGet,
    hdfWriterProcessId,
  ]);

  return {
    hdfModalOpen,
    setHdfModalOpen,
    hdfNoteModalOpen,
    setHdfNoteModalOpen,
    hdfRotateFilenameDraft,
    setHdfRotateFilenameDraft,
    hdfRotateDisabledDevicesDraft,
    setHdfRotateDisabledDevicesDraft,
    hdfEnableDevicesDraft,
    setHdfEnableDevicesDraft,
    hdfDisableDevicesDraft,
    setHdfDisableDevicesDraft,
    hdfMeasurementSchemaByProcessId,
    setHdfMeasurementSchemaByProcessId,
    hdfRotateMeasurementProfileDraft,
    setHdfRotateMeasurementProfileDraft,
    hdfRotateMeasurementValuesDraft,
    setHdfRotateMeasurementValuesDraft,
    hdfRotateMeasurementCustomByField,
    setHdfRotateMeasurementCustomByField,
    hdfNoteValuesDraft,
    setHdfNoteValuesDraft,
    hdfNoteCustomByField,
    setHdfNoteCustomByField,
    hdfLastNoteAuthor,
    setHdfLastNoteAuthor,
    hdfWriterProcessId,
    hdfWriterStatus,
    hdfWriterLoading,
    hdfWriterState,
    hdfProcessCapabilitiesError: hdfProcessCapabilitiesError ?? null,
    hdfSupportsStatus,
    hdfSupportsDevicesGet,
    hdfSupportsDevicesEnable,
    hdfSupportsDevicesDisable,
    hdfSupportsRotate,
    hdfSupportsMeasurementSchemaGet,
    hdfSupportsMeasurementNote,
    hdfStatusBusy,
    hdfDevicesGetBusy,
    hdfDevicesEnableBusy,
    hdfDevicesDisableBusy,
    hdfRotateBusy,
    hdfMeasurementNoteBusy,
    hdfAnyCommandBusy,
    hdfCommandsBlocked,
    hdfMeasurementSchemaLoading,
    hdfMeasurementSchema,
    hdfMeasurementSchemaConfigured,
    hdfMeasurementSchemaAvailable,
    hdfShowMeasurementUi,
    hdfMeasurementSchemaDisplayPath,
    hdfMeasurementSchemaDisplayError,
    hdfRotateSelectedProfile,
    hdfRotateProfileOptions,
    hdfShowNoteChiplet,
    hdfSelectableDeviceIds,
    hdfSelectableDeviceOptions,
    hdfWriterFileLabel,
    hdfWriterChipColor,
    refreshHdfWriterStatus,
    fetchHdfMeasurementSchema,
    openHdfWriterCommands,
    openHdfMeasurementNoteModal,
    applyMeasurementNoteDraft,
    selectHdfRotateMeasurementProfile,
    setHdfRotateFieldValue,
    setHdfRotateFieldUseCustom,
    setHdfNoteFieldValue,
    setHdfNoteFieldUseCustom,
    executeHdfStatus,
    executeHdfRotate,
    executeHdfMeasurementNote,
    executeHdfDevicesGet,
    executeHdfDevicesEnable,
    executeHdfDevicesDisable,
  };
}
