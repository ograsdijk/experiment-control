import type { SequencerDiagnostic, SequencerStepOutlineNode } from "./types";

const SEVERITY_RANK: Record<SequencerDiagnostic["severity"], number> = {
  error: 2,
  warning: 1,
  info: 0,
};

export function worseSeverity(
  a: SequencerDiagnostic["severity"] | null,
  b: SequencerDiagnostic["severity"]
): SequencerDiagnostic["severity"] {
  return a !== null && SEVERITY_RANK[a] >= SEVERITY_RANK[b] ? a : b;
}

/** Innermost step whose lines contain `line`, with its ancestors (outermost first). */
export function stepPathAtLine(
  outline: ReadonlyArray<SequencerStepOutlineNode>,
  line: number
): SequencerStepOutlineNode[] {
  for (const node of outline) {
    if (line >= node.line && line <= node.endLine) {
      return [node, ...stepPathAtLine(node.children, line)];
    }
  }
  return [];
}

export type StepDiagnostics = {
  /** Diagnostics on each step's own lines (not its children's). */
  byStepId: Map<string, SequencerDiagnostic[]>;
  /** Count and worst severity of diagnostics inside each step, children included. */
  insideById: Map<string, { count: number; severity: SequencerDiagnostic["severity"] }>;
};

/**
 * Attach diagnostics to the steps they point at. Stale diagnostics (computed
 * for different text) and those without a line are left out: their position
 * can't be trusted, so they only appear in the list.
 */
export function mapDiagnosticsToSteps(
  outline: ReadonlyArray<SequencerStepOutlineNode>,
  diagnostics: ReadonlyArray<SequencerDiagnostic>
): StepDiagnostics {
  const byStepId = new Map<string, SequencerDiagnostic[]>();
  const insideById = new Map<string, { count: number; severity: SequencerDiagnostic["severity"] }>();
  for (const diag of diagnostics) {
    if (diag.stale || diag.line == null) {
      continue;
    }
    const path = stepPathAtLine(outline, diag.line);
    if (path.length === 0) {
      continue;
    }
    const own = path[path.length - 1];
    byStepId.set(own.id, [...(byStepId.get(own.id) ?? []), diag]);
    for (const node of path) {
      const prev = insideById.get(node.id);
      insideById.set(node.id, {
        count: (prev?.count ?? 0) + 1,
        severity: worseSeverity(prev?.severity ?? null, diag.severity),
      });
    }
  }
  return { byStepId, insideById };
}
