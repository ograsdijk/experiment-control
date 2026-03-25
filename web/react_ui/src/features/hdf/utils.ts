import { sameStringArray } from "../common/compare";
import { normalizeStringList } from "../common/normalize";
import type {
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

export function sameHdfWriterStatus(
  current: HdfWriterStatus | undefined,
  nextStatus: HdfWriterStatus
): boolean {
  if (!current) {
    return false;
  }
  return (
    current.writingActive === nextStatus.writingActive &&
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
    sameStringArray(current.enabledKnownDevices, nextStatus.enabledKnownDevices)
  );
}
