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
