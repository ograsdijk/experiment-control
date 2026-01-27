export type SequencerStatus = {
  state: string | null;
  currentStep: string | null;
  error: string | null;
  loaded: boolean | null;
  contextColumns: Record<string, string> | null;
  loadedSource: string | null;
  autoloadError: string | null;
  progress: SequencerProgress | null;
};

export type SequencerProgress = {
  elapsedS: number | null;
  completedSteps: number | null;
  totalSteps: number | null;
  percent: number | null;
  etaS: number | null;
  stepEwmaS: number | null;
  currentStepElapsedS: number | null;
};

export type SequencerDiagnostic = {
  severity: "error" | "warning" | "info";
  message: string;
  line: number | null;
  column: number | null;
  source: string | null;
};
