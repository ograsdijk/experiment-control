export type InfluxDestinationInfo = {
  name: string;
  url: string;
  scheme: string;
  host: string;
  port: number | null;
  org: string;
  bucket: string;
  precision: string;
  measurement: string;
  requestTimeoutS: number | null;
  staticTags: Record<string, string>;
  tokenPresent: boolean;
};

export type InfluxMeasurementResolutionRow = {
  deviceId: string;
  deviceType: string | null;
  destination: string;
  measurement: string;
  routeMeasurement: string | null;
  routeDeviceType: string | null;
};

export type InfluxWriterCounters = {
  pointsReceived: number;
  pointsQueued: number;
  pointsWritten: number;
  pointsSkippedInvalid: number;
  pointsSkippedRemote: number;
  pointsDroppedOverflow: number;
  writeErrors: number;
  batchesWritten: number;
};

export type InfluxWriterStatus = {
  enabled: boolean;
  instanceId: string | null;
  defaultDestination: string | null;
  destinations: string[];
  destinationsInfo: InfluxDestinationInfo[];
  measurementResolution: InfluxMeasurementResolutionRow[];
  routesCount: number;
  disabledDevices: string[];
  queueDepth: number;
  queueCapacity: number;
  overflowPolicy: string;
  batchMaxPoints: number;
  flushIntervalS: number;
  includeQualityFields: boolean;
  includeUnitFields: boolean;
  deviceTagKeys: string[];
  counters: InfluxWriterCounters;
  lastError: string | null;
  lastFlushWallS: number | null;
  lastFlushMonoS: number | null;
  deviceTypeKnownCount: number;
  remoteDeviceKnownCount: number;
  error: string | null;
};
