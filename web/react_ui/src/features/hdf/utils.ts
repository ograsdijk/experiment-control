import { sameStringArray } from "../common/compare";
import { normalizeStringList } from "../common/normalize";
import type {
  HdfFileOpInFlight,
  HdfFileOpOutcome,
  HdfFileState,
  HdfWriterStatus,
  MeasurementFieldSchema,
  MeasurementFieldType,
  MeasurementProfileSchema,
  MeasurementSchema,
} from "./types";

export function normalizeMeasurementFieldType(value: unknown): MeasurementFieldType {
  if (value === "number" || value === "integer" || value === "boolean") {
    return value;
  }
  return "string";
}

export function normalizeMeasurementField(raw: unknown): MeasurementFieldSchema | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  const key = typeof obj.key === "string" ? obj.key.trim() : "";
  if (!key) {
    return null;
  }
  const label =
    typeof obj.label === "string" && obj.label.trim().length > 0
      ? obj.label.trim()
      : key;
  const options = normalizeStringList(obj.options);
  const hasDefault = Object.prototype.hasOwnProperty.call(obj, "default");
  return {
    key,
    label,
    type: normalizeMeasurementFieldType(obj.type),
    required: obj.required === true,
    allowCustom: obj.allow_custom === true,
    options,
    defaultValue: hasDefault ? obj.default : null,
    hasDefault,
    placeholder:
      typeof obj.placeholder === "string" && obj.placeholder.trim().length > 0
        ? obj.placeholder
        : null,
    description:
      typeof obj.description === "string" && obj.description.trim().length > 0
        ? obj.description
        : null,
    multiline: obj.multiline === true,
  };
}

export function normalizeMeasurementSchema(raw: unknown): MeasurementSchema | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  const versionRaw =
    typeof obj.version === "number" && Number.isFinite(obj.version) ? obj.version : 1;
  const version = Math.max(1, Math.trunc(versionRaw));

  const profilesRaw = Array.isArray(obj.profiles) ? obj.profiles : [];
  const profiles: MeasurementProfileSchema[] = [];
  for (const profileRaw of profilesRaw) {
    if (!profileRaw || typeof profileRaw !== "object") {
      continue;
    }
    const profileObj = profileRaw as Record<string, unknown>;
    const id = typeof profileObj.id === "string" ? profileObj.id.trim() : "";
    if (!id) {
      continue;
    }
    const label =
      typeof profileObj.label === "string" && profileObj.label.trim().length > 0
        ? profileObj.label.trim()
        : id;
    const description =
      typeof profileObj.description === "string" &&
      profileObj.description.trim().length > 0
        ? profileObj.description
        : null;
    const fieldsRaw = Array.isArray(profileObj.fields) ? profileObj.fields : [];
    const fields = fieldsRaw
      .map((fieldRaw) => normalizeMeasurementField(fieldRaw))
      .filter((field): field is MeasurementFieldSchema => field !== null);
    profiles.push({ id, label, description, fields });
  }

  const notesObj =
    obj.notes && typeof obj.notes === "object" ? (obj.notes as Record<string, unknown>) : {};
  const notesFieldsRaw = Array.isArray(notesObj.fields) ? notesObj.fields : [];
  const notesFields = notesFieldsRaw
    .map((fieldRaw) => normalizeMeasurementField(fieldRaw))
    .filter((field): field is MeasurementFieldSchema => field !== null);

  return {
    version,
    profiles,
    notes: {
      fields: notesFields,
    },
  };
}

export function formatFieldDefaultValue(value: unknown): string {
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  if (typeof value === "string") {
    return value;
  }
  return "";
}

export function coerceMeasurementFieldValue(
  field: MeasurementFieldSchema,
  raw: unknown
): unknown | undefined {
  if (raw === null || raw === undefined) {
    return undefined;
  }
  if (field.type === "boolean") {
    if (typeof raw === "boolean") {
      return raw;
    }
    const text = String(raw).trim().toLowerCase();
    if (!text) {
      return undefined;
    }
    if (["true", "1", "yes", "on"].includes(text)) {
      return true;
    }
    if (["false", "0", "no", "off"].includes(text)) {
      return false;
    }
    throw new Error(`${field.label} must be true/false`);
  }
  const text = String(raw).trim();
  if (!text) {
    return undefined;
  }
  if (field.type === "number") {
    const value = Number(text);
    if (!Number.isFinite(value)) {
      throw new Error(`${field.label} must be a number`);
    }
    return value;
  }
  if (field.type === "integer") {
    const value = Number(text);
    if (!Number.isFinite(value) || !Number.isInteger(value)) {
      throw new Error(`${field.label} must be an integer`);
    }
    return value;
  }
  return text;
}

function finiteOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function nonEmptyStringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

export function normalizeHdfFileState(raw: unknown, writingActive: boolean): HdfFileState {
  if (raw === "idle" || raw === "writing" || raw === "closing" || raw === "rotating") {
    return raw;
  }
  // Writers that predate async file ops don't report file_state.
  return writingActive ? "writing" : "idle";
}

export function normalizeHdfFileOp(raw: unknown): HdfFileOpInFlight | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  const op = nonEmptyStringOrNull(obj.op);
  if (!op) {
    return null;
  }
  return {
    op,
    file: nonEmptyStringOrNull(obj.file),
    newFile: nonEmptyStringOrNull(obj.new_file),
    elapsedS: finiteOrNull(obj.elapsed_s),
  };
}

export function normalizeHdfFileOpOutcome(raw: unknown): HdfFileOpOutcome | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  const op = nonEmptyStringOrNull(obj.op);
  if (!op) {
    return null;
  }
  const error =
    obj.error && typeof obj.error === "object"
      ? (obj.error as Record<string, unknown>)
      : null;
  const phaseTimingsS: Record<string, number> = {};
  if (obj.phase_timings_s && typeof obj.phase_timings_s === "object") {
    for (const [phase, seconds] of Object.entries(
      obj.phase_timings_s as Record<string, unknown>
    )) {
      const value = finiteOrNull(seconds);
      if (value !== null) {
        phaseTimingsS[phase] = value;
      }
    }
  }
  return {
    op,
    ok: obj.ok === true,
    file: nonEmptyStringOrNull(obj.file),
    newFile: nonEmptyStringOrNull(obj.new_file),
    startedWall: finiteOrNull(obj.started_wall),
    durationS: finiteOrNull(obj.duration_s),
    errorMessage:
      nonEmptyStringOrNull(error?.message) ?? nonEmptyStringOrNull(error?.code),
    phaseTimingsS,
    heldDropped: Math.max(0, Math.trunc(finiteOrNull(obj.held_dropped) ?? 0)),
  };
}

/** Identity of a completed op, used to notice when a new one has finished. */
export function hdfFileOpOutcomeKey(outcome: HdfFileOpOutcome | null): string | null {
  if (!outcome) {
    return null;
  }
  return `${outcome.op}:${outcome.startedWall ?? ""}:${outcome.file ?? ""}`;
}

/** e.g. "close 11.8 s, drain 2.1 s" — the slowest phases first. */
export function formatHdfPhaseTimings(
  phaseTimingsS: Record<string, number>,
  limit = 3
): string {
  return Object.entries(phaseTimingsS)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([phase, seconds]) => `${phase} ${seconds.toFixed(1)} s`)
    .join(", ");
}

function sameFileOp(
  a: HdfFileOpInFlight | null,
  b: HdfFileOpInFlight | null
): boolean {
  if (a === null || b === null) {
    return a === b;
  }
  return (
    a.op === b.op &&
    a.file === b.file &&
    a.newFile === b.newFile &&
    a.elapsedS === b.elapsedS
  );
}

export function sameHdfWriterStatus(
  current: HdfWriterStatus | undefined,
  nextStatus: HdfWriterStatus
): boolean {
  if (!current) {
    return false;
  }
  return (
    current.writingActive === nextStatus.writingActive &&
    current.fileState === nextStatus.fileState &&
    sameFileOp(current.fileOp, nextStatus.fileOp) &&
    hdfFileOpOutcomeKey(current.lastFileOp) ===
      hdfFileOpOutcomeKey(nextStatus.lastFileOp) &&
    current.autostartWriting === nextStatus.autostartWriting &&
    current.filePath === nextStatus.filePath &&
    current.fileName === nextStatus.fileName &&
    current.pending === nextStatus.pending &&
    current.dropped === nextStatus.dropped &&
    current.droppedEvents === nextStatus.droppedEvents &&
    current.measurementId === nextStatus.measurementId &&
    current.measurementType === nextStatus.measurementType &&
    current.measurementSchemaVersion === nextStatus.measurementSchemaVersion &&
    current.measurementStartedWallNs === nextStatus.measurementStartedWallNs &&
    current.measurementEndedWallNs === nextStatus.measurementEndedWallNs &&
    current.measurementNotesRows === nextStatus.measurementNotesRows &&
    current.measurementSchemaConfigured === nextStatus.measurementSchemaConfigured &&
    current.measurementSchemaAvailable === nextStatus.measurementSchemaAvailable &&
    current.measurementSchemaPath === nextStatus.measurementSchemaPath &&
    current.measurementSchemaError === nextStatus.measurementSchemaError &&
    current.error === nextStatus.error &&
    sameStringArray(current.disabledDevices, nextStatus.disabledDevices) &&
    sameStringArray(current.knownDevices, nextStatus.knownDevices) &&
    sameStringArray(current.enabledKnownDevices, nextStatus.enabledKnownDevices) &&
    sameStringArray(current.disabledProcesses, nextStatus.disabledProcesses) &&
    sameStringArray(current.knownProcesses, nextStatus.knownProcesses) &&
    sameStringArray(
      current.enabledKnownProcesses,
      nextStatus.enabledKnownProcesses
    )
  );
}
