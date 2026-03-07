import {
  buildSequencerStepOutline,
  flattenSequencerStepOutline,
} from "./outline";
import {
  parseConditionEntries,
  validateConditionAst,
} from "./condition_ast";
import type { SequencerDiagnostic, SequencerStepOutlineNode } from "./types";

function conditionAnchorLine(node: SequencerStepOutlineNode): number | null {
  const lines = node.snippet.split("\n");
  for (let index = 0; index < lines.length; index += 1) {
    if (/^\s*condition:\s*(?:#.*)?$/.test(lines[index])) {
      return node.line + index;
    }
  }
  return node.line;
}

function conditionEntriesForNode(
  node: SequencerStepOutlineNode
): { entries: ReturnType<typeof parseConditionEntries>; kindLabel: string } | null {
  if (node.waitUntilDetail) {
    return {
      entries: parseConditionEntries(node.waitUntilDetail.condition),
      kindLabel: "wait_until",
    };
  }
  if (node.ifDetail) {
    return {
      entries: parseConditionEntries(node.ifDetail.condition),
      kindLabel: "if",
    };
  }
  if (node.whileDetail) {
    return {
      entries: parseConditionEntries(node.whileDetail.condition),
      kindLabel: "while",
    };
  }
  return null;
}

export function buildLocalConditionDiagnostics(
  yamlText: string
): SequencerDiagnostic[] {
  if (!yamlText.trim()) {
    return [];
  }
  const outline = flattenSequencerStepOutline(buildSequencerStepOutline(yamlText));
  const out: SequencerDiagnostic[] = [];

  for (const node of outline) {
    const data = conditionEntriesForNode(node);
    if (!data) {
      continue;
    }
    const issues = validateConditionAst(data.entries);
    if (issues.length <= 0) {
      continue;
    }
    const line = conditionAnchorLine(node);
    for (const issue of issues) {
      out.push({
        severity: issue.severity,
        message:
          issue.path === "root"
            ? `${data.kindLabel}: ${issue.message}`
            : `${data.kindLabel}: ${issue.message} (${issue.path})`,
        line,
        column: null,
        source: "condition.local",
      });
    }
  }

  return out;
}

export function mergeDiagnostics(
  primary: ReadonlyArray<SequencerDiagnostic>,
  secondary: ReadonlyArray<SequencerDiagnostic>
): SequencerDiagnostic[] {
  const seen = new Set<string>();
  const out: SequencerDiagnostic[] = [];
  for (const item of [...primary, ...secondary]) {
    const key = [
      item.severity,
      item.source ?? "",
      item.line ?? "",
      item.column ?? "",
      item.message,
    ].join("|");
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    out.push(item);
  }
  return out;
}

