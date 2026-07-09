import type { SequencerStepOutlineNode } from "../types";
import {
  buildTemplateSnippet,
  findContainerInsertion,
  indentSnippet,
  insertLinesAfter,
  joinLines,
  splitLines,
  type BasicSequencerStepTemplate,
  type SequencerChildContainer,
} from "./shared";

type ChildInsertionTarget = {
  key: SequencerChildContainer;
  label: string;
};

function swapSiblingStepRanges(
  yamlText: string,
  firstNode: SequencerStepOutlineNode,
  secondNode: SequencerStepOutlineNode
): string {
  const { lines, hasTrailingNewline } = splitLines(yamlText);
  const firstStart = Math.max(0, firstNode.line - 1);
  const firstEnd = Math.max(firstStart, firstNode.endLine - 1);
  const secondStart = Math.max(0, secondNode.line - 1);
  const secondEnd = Math.max(secondStart, secondNode.endLine - 1);

  const firstLines = lines.slice(firstStart, firstEnd + 1);
  const betweenLines = lines.slice(firstEnd + 1, secondStart);
  const secondLines = lines.slice(secondStart, secondEnd + 1);

  const nextLines = [
    ...lines.slice(0, firstStart),
    ...secondLines,
    ...betweenLines,
    ...firstLines,
    ...lines.slice(secondEnd + 1),
  ];

  return joinLines(nextLines, hasTrailingNewline);
}

export function listChildInsertionTargets(
  node: SequencerStepOutlineNode
): ChildInsertionTarget[] {
  if (
    node.kind === "for" ||
    node.kind === "repeat" ||
    node.kind === "while" ||
    node.kind === "atomic" ||
    node.kind === "parallel" ||
    node.kind === "adaptive"
  ) {
    return findContainerInsertion(node.snippet, "do")
      ? [{ key: "do", label: "body" }]
      : [];
  }
  if (node.kind === "if") {
    const targets: ChildInsertionTarget[] = [];
    if (findContainerInsertion(node.snippet, "then")) {
      targets.push({ key: "then", label: "then" });
    }
    if (findContainerInsertion(node.snippet, "else")) {
      targets.push({ key: "else", label: "else" });
    }
    return targets;
  }
  if (node.kind === "try") {
    const targets: ChildInsertionTarget[] = [];
    if (findContainerInsertion(node.snippet, "do")) {
      targets.push({ key: "do", label: "body" });
    }
    if (findContainerInsertion(node.snippet, "finally")) {
      targets.push({ key: "finally", label: "finally" });
    }
    return targets;
  }
  return [];
}

export function getChildInsertionLine(
  node: SequencerStepOutlineNode,
  containerKey: SequencerChildContainer
): number | null {
  const insertion = findContainerInsertion(node.snippet, containerKey);
  if (!insertion) {
    return null;
  }
  return node.line + insertion.insertLineIndex;
}

export function duplicateStep(
  yamlText: string,
  node: SequencerStepOutlineNode
): string {
  return insertLinesAfter(yamlText, node.endLine, indentSnippet(node.snippet, node.indent));
}

export function insertStepBelow(
  yamlText: string,
  node: SequencerStepOutlineNode,
  kind: BasicSequencerStepTemplate
): string {
  return insertLinesAfter(
    yamlText,
    node.endLine,
    indentSnippet(buildTemplateSnippet(kind), node.indent)
  );
}

export function insertStepAtTopLevel(
  yamlText: string,
  kind: BasicSequencerStepTemplate
): string {
  const { lines, hasTrailingNewline } = splitLines(yamlText);
  const snippet = indentSnippet(buildTemplateSnippet(kind), 2);
  const stepsIndex = lines.findIndex((line) => {
    const match = line.match(/^(\s*)steps:\s*(.*)$/);
    if (!match) {
      return false;
    }
    const indent = match[1]?.length ?? 0;
    return indent === 0;
  });

  if (stepsIndex >= 0) {
    const stepsLine = lines[stepsIndex] ?? "";
    const stepsMatch = stepsLine.match(/^(\s*)steps:\s*(.*)$/);
    const remainder = (stepsMatch?.[2] ?? "").replace(/\s+#.*$/, "").trim();
    if (remainder.length > 0) {
      lines[stepsIndex] = "steps:";
    }

    let insertIndex = stepsIndex + 1;
    for (let index = stepsIndex + 1; index < lines.length; index += 1) {
      const line = lines[index] ?? "";
      if (!line.trim()) {
        insertIndex = index + 1;
        continue;
      }
      const indent = line.match(/^\s*/)?.[0].length ?? 0;
      if (indent === 0) {
        break;
      }
      insertIndex = index + 1;
    }
    return joinLines(
      [...lines.slice(0, insertIndex), ...snippet, ...lines.slice(insertIndex)],
      hasTrailingNewline
    );
  }

  const nextLines = [...lines];
  if (nextLines.length > 0 && nextLines[nextLines.length - 1]?.trim().length > 0) {
    nextLines.push("");
  }
  nextLines.push("steps:");
  nextLines.push(...snippet);
  return joinLines(nextLines, hasTrailingNewline);
}

export function insertStepInside(
  yamlText: string,
  node: SequencerStepOutlineNode,
  kind: BasicSequencerStepTemplate,
  containerKey: SequencerChildContainer
): string {
  const insertion = findContainerInsertion(node.snippet, containerKey);
  if (!insertion) {
    return yamlText;
  }
  const snippetLines = node.snippet.split("\n");
  snippetLines.splice(
    insertion.insertLineIndex,
    0,
    ...indentSnippet(buildTemplateSnippet(kind), insertion.childIndent)
  );
  const nextSnippet = snippetLines.join("\n");
  const startLine = node.line;
  const endLine = node.endLine;
  const { lines, hasTrailingNewline } = splitLines(yamlText);
  const startIndex = Math.max(0, startLine - 1);
  const endIndex = Math.max(startIndex, endLine - 1);
  const nextLines = [
    ...lines.slice(0, startIndex),
    ...indentSnippet(nextSnippet, node.indent),
    ...lines.slice(endIndex + 1),
  ];
  return joinLines(nextLines, hasTrailingNewline);
}

export function moveStepUp(
  yamlText: string,
  node: SequencerStepOutlineNode,
  previousSibling: SequencerStepOutlineNode
): string {
  return swapSiblingStepRanges(yamlText, previousSibling, node);
}

export function moveStepDown(
  yamlText: string,
  node: SequencerStepOutlineNode,
  nextSibling: SequencerStepOutlineNode
): string {
  return swapSiblingStepRanges(yamlText, node, nextSibling);
}

export function toggleStepEnabled(
  yamlText: string,
  node: SequencerStepOutlineNode
): string {
  const { lines, hasTrailingNewline } = splitLines(yamlText);
  const startIndex = Math.max(0, node.line - 1);
  const endIndex = Math.max(startIndex, node.endLine - 1);
  const disabledIndent = node.indent + 2;
  const disabledPattern = new RegExp(`^ {${disabledIndent}}disabled:\\s*true\\s*$`);

  if (node.disabled) {
    for (let index = startIndex + 1; index <= endIndex; index += 1) {
      if (disabledPattern.test(lines[index] ?? "")) {
        const nextLines = [...lines.slice(0, index), ...lines.slice(index + 1)];
        return joinLines(nextLines, hasTrailingNewline);
      }
    }
    return yamlText;
  }

  const insertionLine = `${" ".repeat(disabledIndent)}disabled: true`;
  const nextLines = [
    ...lines.slice(0, startIndex + 1),
    insertionLine,
    ...lines.slice(startIndex + 1),
  ];
  return joinLines(nextLines, hasTrailingNewline);
}

export function deleteStep(
  yamlText: string,
  node: SequencerStepOutlineNode
): string {
  const { lines, hasTrailingNewline } = splitLines(yamlText);
  const startIndex = Math.max(0, node.line - 1);
  const endIndex = Math.max(startIndex, node.endLine - 1);
  const nextLines = [...lines.slice(0, startIndex), ...lines.slice(endIndex + 1)];

  if (
    startIndex > 0 &&
    startIndex < nextLines.length &&
    nextLines[startIndex - 1]?.trim() === "" &&
    nextLines[startIndex]?.trim() === ""
  ) {
    nextLines.splice(startIndex, 1);
  }

  return joinLines(nextLines, hasTrailingNewline);
}
