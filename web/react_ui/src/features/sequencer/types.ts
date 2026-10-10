export type SequencerAdaptiveStudyStatus = {
  controllerKind: string | null;
  trialCount: number;
  lastMode: string | null;
};

export type SequencerYamlEditorHandle = {
  focus: () => void;
  focusAtOffset: (offset: number) => void;
};

export type SequencerStatus = {
  runId: number | null;
  state: string | null;
  currentStep: string | null;
  currentStepDetail: SequencerStepDetail | null;
  loopMode: string | null;
  loopsCompleted: number | null;
  loopsTarget: number | null;
  error: string | null;
  errorDetail: SequencerErrorDetail | null;
  cleanupActive: boolean | null;
  loaded: boolean | null;
  activeSequenceId: string | null;
  contextColumns: Record<string, string> | null;
  loadedSource: string | null;
  /** Backend load counter; null/absent from an older backend. */
  loadedRevision?: number | null;
  autoloadError: string | null;
  progress: SequencerProgress | null;
  loadedAdaptiveIds: string[];
  adaptiveStudies: Record<string, SequencerAdaptiveStudyStatus>;
  /** Who paused the run and why, while PAUSED. */
  pause?: SequencerPauseInfo | null;
  /** Summary of sequencer.run_events; refetch the list when `seq` changes. */
  runEvents?: SequencerRunEventsSummary | null;
};

export type SequencerPauseTrigger = {
  watchdogId: string | null;
  rule: string | null;
  severity: string | null;
  tripId: string | null;
};

export type SequencerPauseInfo = {
  reason: string | null;
  source: string | null;
  trigger: SequencerPauseTrigger | null;
  elapsedS: number | null;
};

export type SequencerRunEventsSummary = {
  seq: number;
  count: number;
  dropped: number;
  errors: number;
  warnings: number;
};

export type SequencerRunEvent = {
  severity: "error" | "warning" | "info";
  /** pause | resume | stop | step_failed | cleanup_failed | external_fault | log */
  kind: string;
  message: string;
  source: string | null;
  step: {
    kind: string | null;
    summary: string | null;
    path: string | null;
    line: number | null;
    branch: string | null;
  } | null;
  elapsedS: number;
  count: number;
  trigger: SequencerPauseTrigger | null;
};

export type SequencerProgress = {
  runId: number | null;
  elapsedS: number | null;
  completedSteps: number | null;
  totalSteps: number | null;
  totalStepsKnown: boolean | null;
  estimateReason: string | null;
  approximate: boolean;
  percent: number | null;
  timePercent: number | null;
  etaS: number | null;
  etaWallTs: number | null;
  scope: "run" | "loop";
  phase: "run" | "cleanup";
  cleanupCompletedSteps: number | null;
  cleanupTotalSteps: number | null;
  currentStepElapsedS: number | null;
  loopMode: string | null;
  loopsCompleted: number | null;
  loopsTarget: number | null;
};

export type SequencerStepDetail = {
  kind: string | null;
  summary: string | null;
  path: string | null;
  line: number | null;
  column: number | null;
  source: string | null;
  branch: "then" | "else" | "finally" | null;
  targetKind?: "device" | "process" | null;
  device?: string | null;
  process?: string | null;
  action?: string | null;
  name?: string | null;
};

export type SequencerErrorDetail = {
  message: string;
  formatted: string;
  step: SequencerStepDetail | null;
  cleanupErrors: SequencerErrorDetail[];
};

export type SequencerDiagnostic = {
  severity: "error" | "warning" | "info";
  message: string;
  line: number | null;
  column: number | null;
  source: string | null;
  /** The YAML changed since this was computed; its line may be off. */
  stale?: boolean;
  /** "run": an event from the current/last run rather than a validation result. */
  origin?: "validation" | "run";
};

export type SequencerStepOutlineNode = {
  id: string;
  path: string | null;
  kind: string;
  line: number;
  endLine: number;
  indent: number;
  branchLabel: "then" | "else" | "finally" | null;
  summary: string | null;
  snippet: string;
  disabled: boolean;
  children: SequencerStepOutlineNode[];
  callDetail: SequencerCallDetail | null;
  sleepDetail: SequencerSleepDetail | null;
  setDetail: SequencerSetDetail | null;
  assignDetail: SequencerAssignDetail | null;
  waitUntilDetail: SequencerWaitUntilDetail | null;
  setContextDetail: SequencerSetContextDetail | null;
  ifDetail: SequencerIfDetail | null;
  whileDetail: SequencerWhileDetail | null;
  atomicDetail: SequencerAtomicDetail | null;
  pauseDetail: SequencerPauseDetail | null;
  parallelDetail: SequencerParallelDetail | null;
  forDetail: SequencerForDetail | null;
  repeatDetail: SequencerRepeatDetail | null;
  adaptiveDetail: SequencerAdaptiveDetail | null;
};

export type SequencerOutlineMetadataEntry = {
  name: string;
  value: string | null;
};

export type SequencerAdaptiveFieldGroup = {
  name: string;
  entries: SequencerOutlineMetadataEntry[];
};

export type SequencerAdaptiveMetricDetail = {
  name: string;
  sourceKind: string | null;
  config: SequencerOutlineMetadataEntry[];
};

export type SequencerCallDetail = {
  targetKind: "device" | "process";
  device: string | null;
  process: string | null;
  action: string | null;
  params: SequencerOutlineMetadataEntry[];
};

export type SequencerSleepDetail = {
  duration: string | null;
};

export type SequencerSetDetail = {
  device: string | null;
  name: string | null;
  value: string | null;
};

export type SequencerAssignDetail = {
  entries: SequencerOutlineMetadataEntry[];
};

export type SequencerWaitUntilDetail = {
  timeoutS: string | null;
  everyS: string | null;
  sample: SequencerOutlineMetadataEntry[];
  condition: SequencerOutlineMetadataEntry[];
};

export type SequencerSetContextStreamDetail = {
  device: string | null;
  stream: string | null;
};

export type SequencerSetContextDetail = {
  streams: SequencerSetContextStreamDetail[];
  fields: SequencerOutlineMetadataEntry[];
};

export type SequencerIfDetail = {
  condition: SequencerOutlineMetadataEntry[];
  thenCount: number;
  elseCount: number;
};

export type SequencerWhileDetail = {
  condition: SequencerOutlineMetadataEntry[];
};

export type SequencerAtomicDetail = {
  name: string | null;
};

export type SequencerPauseDetail = {
  reason: string | null;
};

export type SequencerParallelDetail = {
  branchCount: number;
};

export type SequencerForDetail = {
  bind: SequencerOutlineMetadataEntry[];
  sourceMode: "generator" | "direct";
  generatorKind: string | null;
  directValue: string | null;
  generatorModifiers: SequencerOutlineMetadataEntry[];
  iterableConfig: SequencerOutlineMetadataEntry[];
};

export type SequencerRepeatDetail = {
  times: string | null;
};

export type SequencerAdaptiveDetail = {
  id: string | null;
  controllerKind: string | null;
  controllerConfig: SequencerOutlineMetadataEntry[];
  space: SequencerAdaptiveFieldGroup[];
  bind: SequencerOutlineMetadataEntry[];
  observeRepeats: string | null;
  metrics: SequencerAdaptiveMetricDetail[];
  aggregate: SequencerOutlineMetadataEntry[];
  score: string | null;
  stopping: SequencerOutlineMetadataEntry[];
};

export type SequencerOutlineMetadata = {
  version: string | null;
  vars: SequencerOutlineMetadataEntry[];
  contextColumns: SequencerOutlineMetadataEntry[];
};
