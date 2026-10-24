/**
 * `sequencer.plan` (how often each step of the loaded sequence runs) and
 * `sequencer.status.loops` (the loops a run is inside), for the Timeline tab.
 */

import { asRecord, stringList } from "./records";

export type SequencerCertainty = "exact" | "inferred" | "approximate" | "unknown";

const CERTAINTIES: readonly SequencerCertainty[] = ["exact", "inferred", "approximate", "unknown"];

export function worseCertainty(a: SequencerCertainty, b: SequencerCertainty): SequencerCertainty {
  return CERTAINTIES.indexOf(a) >= CERTAINTIES.indexOf(b) ? a : b;
}

export type SequencerPlanStep = {
  path: string;
  line: number | null;
  source: string | null;
  /** `use:` sub-sequence the step comes from; null for the loaded sequence. */
  useId: string | null;
  kind: string;
  summary: string | null;
  disabled: boolean;
  /** Executions from start to finish (live: runs so far + to come once exact). */
  count: number;
  certainty: SequencerCertainty;
  note: string | null;
  runsDone: number;
  meanS: number | null;
  /** Loop steps: total body passes; null when unknown. */
  iterations?: number | null;
  iterationsCertainty?: SequencerCertainty;
  iterationsNote?: string | null;
  /** sleep: seconds per run. */
  seconds?: number | null;
  /** wait_until: timeout in seconds. */
  timeoutS?: number | null;
};

export type SequencerPlan = {
  runId: number | null;
  state: string | null;
  live: boolean;
  loadedRevision: number | null;
  totalRuns: number | null;
  certainty: SequencerCertainty;
  unknownReason: string | null;
  approximateReason: string | null;
  sleepS: number | null;
  waitTimeoutS: number | null;
  estimatedS: number | null;
  steps: SequencerPlanStep[];
};

export type SequencerLoopStatus = {
  kind: string;
  path: string | null;
  line: number | null;
  summary: string | null;
  /** Pass in progress, 1-based (0 before the first). */
  iteration: number;
  /** Number of passes; null for while/adaptive. */
  count: number | null;
  /** for loops: bound names and, up to 256 items, values in visit order. */
  vars: string[];
  values: Record<string, unknown>[] | null;
};

function finite(raw: unknown): number | null {
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

function int(raw: unknown): number | null {
  const value = finite(raw);
  return value === null ? null : Math.trunc(value);
}

function text(raw: unknown): string | null {
  return typeof raw === "string" && raw.trim() ? raw : null;
}

function certainty(raw: unknown): SequencerCertainty {
  return CERTAINTIES.includes(raw as SequencerCertainty) ? (raw as SequencerCertainty) : "exact";
}

function normalizePlanStep(raw: unknown): SequencerPlanStep | null {
  const rec = asRecord(raw);
  const path = text(rec?.path);
  if (!rec || !path) {
    return null;
  }
  const step: SequencerPlanStep = {
    path,
    line: int(rec.line),
    source: text(rec.source),
    useId: text(rec.use_id),
    kind: text(rec.kind) ?? "step",
    summary: text(rec.summary),
    disabled: rec.disabled === true,
    count: Math.max(0, int(rec.count) ?? 0),
    certainty: certainty(rec.certainty),
    note: text(rec.note),
    runsDone: Math.max(0, int(rec.runs_done) ?? 0),
    meanS: finite(rec.mean_s),
  };
  if ("iterations" in rec) {
    step.iterations = int(rec.iterations);
    step.iterationsCertainty = certainty(rec.iterations_certainty);
    step.iterationsNote = text(rec.iterations_note);
  }
  if ("seconds" in rec) {
    step.seconds = finite(rec.seconds);
  }
  if ("timeout_s" in rec) {
    step.timeoutS = finite(rec.timeout_s);
  }
  return step;
}

export function normalizeSequencerPlan(raw: unknown): SequencerPlan | null {
  const rec = asRecord(raw);
  if (!rec || !Array.isArray(rec.steps)) {
    return null;
  }
  return {
    runId: int(rec.run_id),
    state: text(rec.state),
    live: rec.live === true,
    loadedRevision: int(rec.loaded_revision),
    totalRuns: int(rec.total_runs),
    certainty: certainty(rec.certainty),
    unknownReason: text(rec.unknown_reason),
    approximateReason: text(rec.approximate_reason),
    sleepS: finite(rec.sleep_s),
    waitTimeoutS: finite(rec.wait_timeout_s),
    estimatedS: finite(rec.estimated_s),
    steps: rec.steps
      .map(normalizePlanStep)
      .filter((step): step is SequencerPlanStep => step !== null),
  };
}

export function normalizeSequencerLoops(raw: unknown): SequencerLoopStatus[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const out: SequencerLoopStatus[] = [];
  for (const item of raw) {
    const rec = asRecord(item);
    if (!rec) {
      continue;
    }
    const values = Array.isArray(rec.values)
      ? rec.values.map((value) => asRecord(value) ?? {})
      : null;
    out.push({
      kind: text(rec.kind) ?? "loop",
      path: text(rec.path),
      line: int(rec.line),
      summary: text(rec.summary),
      iteration: Math.max(0, int(rec.iteration) ?? 0),
      count: int(rec.count),
      vars: stringList(rec.vars),
      values,
    });
  }
  return out;
}

/**
 * Plan entries of the loaded sequence by line. A line can hold more than one
 * entry (steps the parser adds for `requires:` share its line): the caller
 * picks by kind.
 */
export function planStepsByLine(plan: SequencerPlan | null): Map<number, SequencerPlanStep[]> {
  const out = new Map<number, SequencerPlanStep[]>();
  for (const step of plan?.steps ?? []) {
    if (step.useId !== null || step.line === null) {
      continue;
    }
    const list = out.get(step.line);
    if (list) {
      list.push(step);
    } else {
      out.set(step.line, [step]);
    }
  }
  return out;
}

/** Passes of a loop per run of the loop step (`iterations / count`). */
export function passesPerRun(step: SequencerPlanStep | null | undefined): number | null {
  if (!step || step.iterations === undefined || step.iterations === null) {
    return null;
  }
  if (step.count <= 0) {
    return step.iterations;
  }
  return step.iterations / step.count;
}
