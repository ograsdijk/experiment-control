import type { SequencerYamlEditorHandle } from "./types";

export function lineColumnToOffset(
  text: string,
  line: number,
  column: number | null
): number {
  const lines = text.split("\n");
  const safeLine = Math.max(1, Math.min(line, lines.length || 1));
  let offset = 0;
  for (let idx = 0; idx < safeLine - 1; idx += 1) {
    offset += lines[idx].length + 1;
  }
  const target = lines[safeLine - 1] ?? "";
  const safeColumn = Math.max(1, column ?? 1);
  offset += Math.min(target.length, safeColumn - 1);
  return offset;
}

export type SequencerDiagnosticJumpPlan = {
  offset: number;
  requiresEditMode: boolean;
};

export function computeSequencerDiagnosticJumpPlan(
  yamlText: string,
  yamlViewMode: "edit" | "preview",
  line: number | null,
  column: number | null
): SequencerDiagnosticJumpPlan | null {
  if (line == null) {
    return null;
  }
  return {
    offset: lineColumnToOffset(yamlText, line, column),
    requiresEditMode: yamlViewMode !== "edit",
  };
}

export function focusSequencerDiagnosticOffset(
  editor: SequencerYamlEditorHandle | null,
  offset: number
): boolean {
  if (!editor) {
    return false;
  }
  editor.focusAtOffset(offset);
  return true;
}

