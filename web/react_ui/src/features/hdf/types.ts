export type HdfWriterStatus = {
  filePath: string | null;
  fileName: string | null;
  pending: number | null;
  dropped: number | null;
  droppedEvents: number | null;
  disabledDevices: string[];
  knownDevices: string[];
  enabledKnownDevices: string[];
  measurementId: string | null;
  measurementType: string | null;
  measurementSchemaVersion: number | null;
  measurementStartedWallNs: number | null;
  measurementEndedWallNs: number | null;
  measurementNotesRows: number;
  measurementSchemaConfigured: boolean;
  measurementSchemaAvailable: boolean;
  measurementSchemaPath: string | null;
  measurementSchemaError: string | null;
  error: string | null;
};

export type MeasurementFieldType = "string" | "number" | "integer" | "boolean";

export type MeasurementFieldSchema = {
  key: string;
  label: string;
  type: MeasurementFieldType;
  required: boolean;
  allowCustom: boolean;
  options: string[];
  defaultValue: unknown;
  hasDefault: boolean;
  placeholder: string | null;
  description: string | null;
  multiline: boolean;
};

export type MeasurementProfileSchema = {
  id: string;
  label: string;
  description: string | null;
  fields: MeasurementFieldSchema[];
};

export type MeasurementNoteSchema = {
  fields: MeasurementFieldSchema[];
};

export type MeasurementSchema = {
  version: number;
  profiles: MeasurementProfileSchema[];
  notes: MeasurementNoteSchema;
};

export type HdfMeasurementSchemaState = {
  schema: MeasurementSchema | null;
  path: string | null;
  error: string | null;
};
