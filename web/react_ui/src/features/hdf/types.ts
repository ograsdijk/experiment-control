/** `closing` / `rotating`: an async hdf.writing.stop / hdf.rotate is still
 * finishing on the writer's background thread. */
export type HdfFileState = "idle" | "writing" | "closing" | "rotating";

export type HdfFileOpInFlight = {
  op: string;
  file: string | null;
  newFile: string | null;
  elapsedS: number | null;
};

export type HdfFileOpOutcome = {
  op: string;
  ok: boolean;
  file: string | null;
  newFile: string | null;
  startedWall: number | null;
  durationS: number | null;
  errorMessage: string | null;
  phaseTimingsS: Record<string, number>;
  heldDropped: number;
};

export type HdfWriterStatus = {
  writingActive: boolean;
  fileState: HdfFileState;
  fileOp: HdfFileOpInFlight | null;
  lastFileOp: HdfFileOpOutcome | null;
  autostartWriting: boolean;
  filePath: string | null;
  fileName: string | null;
  pending: number | null;
  dropped: number | null;
  droppedEvents: number | null;
  disabledDevices: string[];
  knownDevices: string[];
  enabledKnownDevices: string[];
  disabledProcesses: string[];
  knownProcesses: string[];
  enabledKnownProcesses: string[];
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
