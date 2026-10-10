import type { SequencerDiagnostic, SequencerStepOutlineNode } from "./types";

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

export type SeverityCounts = Record<SequencerDiagnostic["severity"], number>;

export type StepDiagnostics = {
  /** Diagnostics on each step's own lines (not its children's). */
  byStepId: Map<string, SequencerDiagnostic[]>;
  /** Diagnostics inside each step, children included, counted per severity. */
  insideById: Map<string, SeverityCounts>;
};

export const SEVERITY_ORDER: ReadonlyArray<SequencerDiagnostic["severity"]> = [
  "error",
  "warning",
  "info",
];

/** Diagnostics grouped by severity, worst first, skipping empty groups. */
export function groupBySeverity(
  diagnostics: ReadonlyArray<SequencerDiagnostic>
): Array<{ severity: SequencerDiagnostic["severity"]; items: SequencerDiagnostic[] }> {
  return SEVERITY_ORDER.map((severity) => ({
    severity,
    items: diagnostics.filter((diag) => diag.severity === severity),
  })).filter((group) => group.items.length > 0);
}

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
  const insideById = new Map<string, SeverityCounts>();
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
      const counts = insideById.get(node.id) ?? { error: 0, warning: 0, info: 0 };
      counts[diag.severity] += 1;
      insideById.set(node.id, counts);
    }
  }
  return { byStepId, insideById };
}
