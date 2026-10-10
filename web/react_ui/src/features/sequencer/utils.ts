import type {
  SequencerDiagnostic,
  SequencerErrorDetail,
  SequencerProgress,
  SequencerStatus,
  SequencerStepDetail,
} from "./types";

function normalizeString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function normalizeInt(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  return Math.max(0, Math.trunc(value));
}

function normalizeBranch(value: unknown): SequencerStepDetail["branch"] {
  return value === "then" || value === "else" || value === "finally"
    ? value
    : null;
}

function normalizeTargetKind(
  value: unknown
): SequencerStepDetail["targetKind"] {
  return value === "device" || value === "process" ? value : null;
}

export function normalizeSequencerStepDetail(
  raw: unknown
): SequencerStepDetail | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  return {
    kind: normalizeString(obj.kind),
    summary: normalizeString(obj.summary),
    path: normalizeString(obj.path),
    line: normalizeInt(obj.line),
    column: normalizeInt(obj.column),
    source: normalizeString(obj.source),
    branch: normalizeBranch(obj.branch),
    targetKind: normalizeTargetKind(obj.target_kind ?? obj.targetKind),
    device: normalizeString(obj.device),
    process: normalizeString(obj.process),
    action: normalizeString(obj.action),
    name: normalizeString(obj.name),
  };
}

export function normalizeSequencerErrorDetail(
  raw: unknown
): SequencerErrorDetail | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  const message = normalizeString(obj.message) ?? "Sequencer error";
  const cleanupRaw = Array.isArray(obj.cleanup_errors)
    ? obj.cleanup_errors
    : Array.isArray(obj.cleanupErrors)
      ? obj.cleanupErrors
      : [];
  return {
    message,
    formatted: normalizeString(obj.formatted) ?? message,
    step: normalizeSequencerStepDetail(obj.step),
    cleanupErrors: cleanupRaw
      .map((item) => normalizeSequencerErrorDetail(item))
      .filter((item): item is SequencerErrorDetail => item !== null),
  };
}

export function normalizeSequencerProgress(raw: unknown): SequencerProgress | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  const normalizeFloat = (value: unknown): number | null => {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      return null;
    }
    return value;
  };
  const normalizePercent = (value: unknown): number | null => {
    const raw = normalizeFloat(value);
    return raw === null ? null : Math.max(0, Math.min(100, raw));
  };
  return {
    runId: normalizeInt(obj.run_id),
    elapsedS: normalizeFloat(obj.elapsed_s),
    completedSteps: normalizeInt(obj.completed_steps),
    totalSteps: normalizeInt(obj.total_steps),
    totalStepsKnown:
      typeof obj.total_steps_known === "boolean" ? obj.total_steps_known : null,
    estimateReason: normalizeString(obj.estimate_reason),
    approximate: obj.approximate === true,
    percent: normalizePercent(obj.percent),
    timePercent: normalizePercent(obj.time_percent),
    etaS: normalizeFloat(obj.eta_s),
    etaWallTs: normalizeFloat(obj.eta_wall_ts),
    scope: obj.scope === "loop" ? "loop" : "run",
    phase: obj.phase === "cleanup" ? "cleanup" : "run",
    cleanupCompletedSteps: normalizeInt(obj.cleanup_completed_steps),
    cleanupTotalSteps: normalizeInt(obj.cleanup_total_steps),
    currentStepElapsedS: normalizeFloat(obj.current_step_elapsed_s),
    loopMode:
      typeof obj.loop_mode === "string" && obj.loop_mode.trim().length > 0
        ? obj.loop_mode
        : null,
    loopsCompleted: normalizeInt(obj.loops_completed),
    loopsTarget: normalizeInt(obj.loops_target),
  };
}

export function sameSequencerStatus(
  current: SequencerStatus | undefined,
  next: SequencerStatus
): boolean {
  return Boolean(
    current &&
      current.state === next.state &&
      current.runId === next.runId &&
      current.currentStep === next.currentStep &&
      JSON.stringify(current.currentStepDetail) ===
        JSON.stringify(next.currentStepDetail) &&
      current.loopMode === next.loopMode &&
      current.loopsCompleted === next.loopsCompleted &&
      current.loopsTarget === next.loopsTarget &&
      current.error === next.error &&
      JSON.stringify(current.errorDetail) === JSON.stringify(next.errorDetail) &&
      current.cleanupActive === next.cleanupActive &&
      current.loaded === next.loaded &&
      current.activeSequenceId === next.activeSequenceId &&
      current.loadedSource === next.loadedSource &&
      current.autoloadError === next.autoloadError &&
      JSON.stringify(current.contextColumns) ===
        JSON.stringify(next.contextColumns) &&
      JSON.stringify(current.progress) === JSON.stringify(next.progress) &&
      JSON.stringify(current.loadedAdaptiveIds) ===
        JSON.stringify(next.loadedAdaptiveIds) &&
      JSON.stringify(current.adaptiveStudies) ===
        JSON.stringify(next.adaptiveStudies)
  );
}

export function formatDurationCompact(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    return "n/a";
  }
  const totalSeconds = Math.max(0, Math.trunc(value));
  const hours = Math.trunc(totalSeconds / 3600);
  const minutes = Math.trunc((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/** Bar value: time-based once the backend has an ETA, else step-based. */
export function sequencerDisplayPercent(
  progress: SequencerProgress | null
): number | null {
  if (!progress) {
    return null;
  }
  return progress.timePercent ?? progress.percent;
}

export type SequencerPercentHold = {
  key: string;
  value: number;
};

/**
 * What the displayed percent measures. The hold below restarts whenever this
 * changes: a new run, the cleanup phase, the next loop of a continuous run
 * (whose bar shows only the current loop), or the switch from step- to
 * time-based percent once the ETA appears.
 */
function sequencerPercentKey(progress: SequencerProgress): string {
  return JSON.stringify([
    progress.runId,
    progress.phase,
    progress.timePercent !== null ? "time" : "steps",
    progress.scope === "loop" ? progress.loopsCompleted : null,
  ]);
}

/**
 * Keep the bar from moving backwards while it measures the same thing: the
 * live total can grow (e.g. a while loop iterating again).
 */
export function holdSequencerPercent(
  previous: SequencerPercentHold | null,
  progress: SequencerProgress | null,
  value: number | null
): SequencerPercentHold | null {
  if (!progress || value === null) {
    return null;
  }
  const key = sequencerPercentKey(progress);
  if (previous && previous.key === key && value < previous.value) {
    return previous;
  }
  return { key, value };
}

/**
 * "~12:30 (≈14:52)": remaining time, "~" when the total is approximate. The
 * finish time is computed on this machine's clock (the sequencer host's
 * clock may differ); `eta_wall_ts` only says whether one applies (running).
 */
export function formatSequencerEta(
  progress: SequencerProgress | null,
  nowMs: number = Date.now()
): string | null {
  if (!progress || progress.etaS === null) {
    return null;
  }
  const prefix = progress.approximate ? "~" : "";
  let finish = "";
  if (progress.etaWallTs !== null && progress.etaS > 0) {
    const at = new Date(nowMs + progress.etaS * 1000).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    finish = ` (≈${at})`;
  }
  return `${prefix}${formatDurationCompact(progress.etaS)}${finish}`;
}

export function normalizeSequencerDiagnostics(raw: unknown): SequencerDiagnostic[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const out: SequencerDiagnostic[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const obj = item as Record<string, unknown>;
    const severityRaw = String(obj.severity ?? "error").toLowerCase();
    const severity: SequencerDiagnostic["severity"] =
      severityRaw === "warning" || severityRaw === "info" ? severityRaw : "error";
    out.push({
      severity,
      message: String(obj.message ?? "Validation error"),
      line:
        typeof obj.line === "number" && Number.isFinite(obj.line)
          ? Math.max(1, Math.trunc(obj.line))
          : null,
      column:
        typeof obj.column === "number" && Number.isFinite(obj.column)
          ? Math.max(1, Math.trunc(obj.column))
          : null,
      source: typeof obj.source === "string" && obj.source ? obj.source : null,
    });
  }
  return out;
}
