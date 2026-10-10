import { stepPathAtLine } from "./diagnostic_locations";
import type { SequencerStepDetail, SequencerStepOutlineNode } from "./types";

/** Runtime states in which the current step is worth showing. */
const ACTIVE_RUNTIME_STATES = new Set(["RUNNING", "PAUSED", "STOP_REQUESTED"]);

export type ActiveStepInput = {
  runtimeState: string;
  detail: SequencerStepDetail | null;
  /** Source the loaded sequence came from (`loaded_source`). */
  loadedSource: string | null;
  /** The editor text differs from the loaded sequence: its lines can't be trusted. */
  yamlDirty: boolean;
};

/**
 * Line of the running step in the editor text, or null when it can't be
 * shown reliably. No highlight beats a wrong one, so this is conservative:
 * the step must come from the loaded sequence's own source (steps of a
 * `use:` sub-sequence carry no line or another source) and the editor must
 * still hold the loaded text (same rule as run events).
 */
export function activeStepLine({
  runtimeState,
  detail,
  loadedSource,
  yamlDirty,
}: ActiveStepInput): number | null {
  if (yamlDirty || !ACTIVE_RUNTIME_STATES.has(runtimeState)) {
    return null;
  }
  if (!detail || detail.line == null || detail.line < 1) {
    return null;
  }
  if (!detail.source || !loadedSource || detail.source !== loadedSource) {
    return null;
  }
  return detail.line;
}

/** Inclusive 1-based line range. */
export type LineRange = { from: number; to: number };

export type ActiveStep = {
  /** Innermost step at the active line. */
  leafId: string;
  /** Enclosing steps, outermost first (the leaf itself excluded). */
  ancestorIds: string[];
  /** Lines to highlight: the whole block, or just the header of a container. */
  highlight: LineRange;
  /** Full range of each enclosing container, outermost first. */
  bars: LineRange[];
};

/**
 * Resolve the active line to the step to highlight. A container that is
 * itself the active step (e.g. a `for` advancing its iteration) highlights
 * only its header, up to the line before its first child.
 */
export function resolveActiveStep(
  outline: ReadonlyArray<SequencerStepOutlineNode>,
  line: number | null
): ActiveStep | null {
  if (line == null) {
    return null;
  }
  const path = stepPathAtLine(outline, line);
  if (path.length === 0) {
    return null;
  }
  const leaf = path[path.length - 1];
  const ancestors = path.slice(0, -1);
  let to = leaf.endLine;
  if (leaf.children.length > 0) {
    const firstChildLine = Math.min(...leaf.children.map((child) => child.line));
    to = Math.max(leaf.line, Math.min(leaf.endLine, firstChildLine - 1));
  }
  return {
    leafId: leaf.id,
    ancestorIds: ancestors.map((node) => node.id),
    highlight: { from: leaf.line, to },
    bars: ancestors.map((node) => ({ from: node.line, to: node.endLine })),
  };
}
