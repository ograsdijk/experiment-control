import type { SequencerOutlineMetadataEntry, SequencerStepOutlineNode } from "./types";

export type BasicSequencerStepTemplate = "call" | "sleep" | "repeat";
export type SequencerChildContainer = "do" | "then" | "else";

function splitLines(yamlText: string): { lines: string[]; hasTrailingNewline: boolean } {
  const normalized = yamlText.replace(/\r\n/g, "\n");
  const hasTrailingNewline = normalized.endsWith("\n");
  const lines = normalized.split("\n");
  if (hasTrailingNewline) {
    lines.pop();
  }
  return { lines, hasTrailingNewline };
}

function joinLines(lines: string[], hasTrailingNewline: boolean): string {
  const out = lines.join("\n");
  return hasTrailingNewline ? `${out}\n` : out;
}

function replaceLineRange(
  yamlText: string,
  startLine: number,
  endLine: number,
  replacementLines: string[]
): string {
  const { lines, hasTrailingNewline } = splitLines(yamlText);
  const startIndex = Math.max(0, startLine - 1);
  const endIndex = Math.max(startIndex, endLine - 1);
  const nextLines = [
    ...lines.slice(0, startIndex),
    ...replacementLines,
    ...lines.slice(endIndex + 1),
  ];
  return joinLines(nextLines, hasTrailingNewline);
}

function insertLinesAfter(
  yamlText: string,
  afterLine: number,
  insertionLines: string[]
): string {
  const { lines, hasTrailingNewline } = splitLines(yamlText);
  const insertIndex = Math.max(0, Math.min(lines.length, afterLine));
  const nextLines = [
    ...lines.slice(0, insertIndex),
    ...insertionLines,
    ...lines.slice(insertIndex),
  ];
  return joinLines(nextLines, hasTrailingNewline);
}

function indentSnippet(snippet: string, indent: number): string[] {
  const prefix = " ".repeat(Math.max(0, indent));
  return snippet.split("\n").map((line) => (line.length > 0 ? `${prefix}${line}` : line));
}

function replaceStepSnippet(
  yamlText: string,
  node: SequencerStepOutlineNode,
  snippet: string
): string {
  return replaceLineRange(
    yamlText,
    node.line,
    node.endLine,
    indentSnippet(snippet, node.indent)
  );
}

function sanitizeYamlScalar(value: string): string {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : '""';
}

type NestedEntryNode = {
  value: string | null;
  children: Map<string, NestedEntryNode>;
};

function createNestedEntryNode(): NestedEntryNode {
  return {
    value: null,
    children: new Map<string, NestedEntryNode>(),
  };
}

function buildNestedEntryLines(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>,
  indent: number
): string[] {
  const root = createNestedEntryNode();

  for (const entry of entries) {
    const name = entry.name.trim();
    if (!name) {
      continue;
    }
    const parts = name.split(".").filter((part) => part.trim().length > 0);
    if (parts.length <= 0) {
      continue;
    }
    let node = root;
    for (const part of parts) {
      let child = node.children.get(part);
      if (!child) {
        child = createNestedEntryNode();
        node.children.set(part, child);
      }
      node = child;
    }
    node.value = sanitizeYamlScalar(entry.value ?? "");
  }

  const renderNode = (node: NestedEntryNode, currentIndent: number): string[] => {
    const lines: string[] = [];
    for (const [key, child] of node.children) {
      const prefix = " ".repeat(Math.max(0, currentIndent));
      if (child.children.size <= 0) {
        lines.push(`${prefix}${key}: ${child.value ?? "{}"}`);
      } else {
        lines.push(`${prefix}${key}:`);
        if (child.value) {
          lines.push(`${prefix}  value: ${child.value}`);
        }
        lines.push(...renderNode(child, currentIndent + 2));
      }
    }
    return lines;
  };

  return renderNode(root, indent);
}

function stepSiblingTailLines(snippet: string): string[] {
  const lines = snippet.split("\n");
  if (lines.length <= 1) {
    return [];
  }
  let tailStart = -1;
  for (let index = 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (!line.trim()) {
      continue;
    }
    const indent = line.match(/^\s*/)?.[0].length ?? 0;
    if (indent <= 2) {
      tailStart = index;
      break;
    }
  }
  if (tailStart < 0) {
    return [];
  }
  for (let index = tailStart - 1; index > 0; index -= 1) {
    if (lines[index].trim()) {
      break;
    }
    tailStart = index;
  }
  return lines.slice(tailStart);
}

function renderCallSnippet(
  node: SequencerStepOutlineNode,
  device: string,
  action: string,
  params: SequencerOutlineMetadataEntry[]
): string {
  const lines = ["- call:", `    device: ${sanitizeYamlScalar(device)}`, `    action: ${sanitizeYamlScalar(action)}`];
  const cleanParams = params
    .map((entry) => ({
      name: entry.name.trim(),
      value: sanitizeYamlScalar(entry.value ?? ""),
    }))
    .filter((entry) => entry.name.length > 0);
  if (cleanParams.length <= 0) {
    lines.push("    params: {}");
  } else {
    lines.push("    params:");
    for (const entry of cleanParams) {
      lines.push(`      ${entry.name}: ${entry.value}`);
    }
  }
  return [...lines, ...stepSiblingTailLines(node.snippet)].join("\n");
}

function renderSleepSnippet(node: SequencerStepOutlineNode, duration: string): string {
  const lines = [`- sleep: ${sanitizeYamlScalar(duration)}`];
  const tail = node.snippet.split("\n").slice(1);
  return [...lines, ...tail].join("\n");
}

function renderRepeatSnippet(node: SequencerStepOutlineNode, times: string): string {
  const lines = node.snippet.split("\n");
  const doIndex = lines.findIndex((line, index) => index > 0 && /^\s*do:\s*(?:#.*)?$/.test(line));
  const bodyLines = doIndex >= 0 ? lines.slice(doIndex) : ["    do:"];
  return ["- repeat:", `    times: ${sanitizeYamlScalar(times)}`, ...bodyLines].join("\n");
}

function renderForSnippet(
  node: SequencerStepOutlineNode,
  bind: SequencerOutlineMetadataEntry[],
  sourceMode: "generator" | "direct",
  generatorKind: string | null,
  directValue: string,
  generatorModifiers: SequencerOutlineMetadataEntry[],
  iterableConfig: SequencerOutlineMetadataEntry[]
): string {
  const lines = ["- for:"];
  const cleanBind = bind
    .map((entry) => ({
      name: entry.name.trim(),
      value: sanitizeYamlScalar(entry.value ?? ""),
    }))
    .filter((entry) => entry.name.length > 0);
  if (cleanBind.length <= 0) {
    lines.push("    bind: {}");
  } else {
    lines.push("    bind:");
    for (const entry of cleanBind) {
      lines.push(`      ${entry.name}: ${entry.value}`);
    }
  }

  const normalizedKind = iterableKind.trim() || "value";
  const cleanIterable = iterableConfig
    .map((entry) => ({
      name: entry.name.trim(),
      value: sanitizeYamlScalar(entry.value ?? ""),
    }))
    .filter((entry) => entry.name.length > 0);

  if (sourceMode === "direct") {
    lines.push(`    in: ${sanitizeYamlScalar(directValue)}`);
  } else {
    const normalizedKind = (generatorKind ?? "").trim() || "linspace";
    const cleanModifiers = generatorModifiers
      .map((entry) => ({
        name: entry.name.trim(),
        value: sanitizeYamlScalar(entry.value ?? ""),
      }))
      .filter((entry) => entry.name.length > 0);
    lines.push("    in:");
    lines.push("      gen:");
    for (const modifier of cleanModifiers) {
      lines.push(`        ${modifier.name}: ${modifier.value}`);
    }
    if (normalizedKind === "values") {
      if (cleanIterable.length === 1 && cleanIterable[0]?.name === "inline") {
        lines.push(`        values: ${cleanIterable[0].value}`);
      } else if (cleanIterable.length <= 0) {
        lines.push("        values: []");
      } else {
        lines.push("        values:");
        for (const entry of cleanIterable) {
          lines.push(`          - ${entry.value}`);
        }
      }
    } else if (cleanIterable.length === 1 && cleanIterable[0]?.name === "value") {
      lines.push(`        ${normalizedKind}: ${cleanIterable[0].value}`);
    } else if (cleanIterable.length <= 0) {
      lines.push(`        ${normalizedKind}: {}`);
    } else {
      lines.push(`        ${normalizedKind}:`);
      lines.push(...buildNestedEntryLines(cleanIterable, 10));
    }
  }

  const snippetLines = node.snippet.split("\n");
  const doIndex = snippetLines.findIndex(
    (line, index) => index > 0 && /^\s*do:\s*(?:#.*)?$/.test(line)
  );
  const bodyLines = doIndex >= 0 ? snippetLines.slice(doIndex) : ["    do:"];
  return [...lines, ...bodyLines].join("\n");
}

export function applyEditedCallStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  device: string,
  action: string,
  params: SequencerOutlineMetadataEntry[]
): string {
  return replaceStepSnippet(yamlText, node, renderCallSnippet(node, device, action, params));
}

export function applyEditedSleepStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  duration: string
): string {
  return replaceStepSnippet(yamlText, node, renderSleepSnippet(node, duration));
}

export function applyEditedRepeatStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  times: string
): string {
  return replaceStepSnippet(yamlText, node, renderRepeatSnippet(node, times));
}

export function applyEditedForStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  bind: SequencerOutlineMetadataEntry[],
  sourceMode: "generator" | "direct",
  generatorKind: string | null,
  directValue: string,
  generatorModifiers: SequencerOutlineMetadataEntry[],
  iterableConfig: SequencerOutlineMetadataEntry[]
): string {
  return replaceStepSnippet(
    yamlText,
    node,
    renderForSnippet(
      node,
      bind,
      sourceMode,
      generatorKind,
      directValue,
      generatorModifiers,
      iterableConfig
    )
  );
}

function buildVarsLines(entries: SequencerOutlineMetadataEntry[]): string[] {
  const cleanEntries = entries
    .map((entry) => ({
      name: entry.name.trim(),
      value: sanitizeYamlScalar(entry.value ?? ""),
    }))
    .filter((entry) => entry.name.length > 0);
  if (cleanEntries.length <= 0) {
    return ["vars: {}"];
  }
  return [
    "vars:",
    ...cleanEntries.map((entry) => `  ${entry.name}: ${entry.value}`),
  ];
}

function findTopLevelSectionRange(
  lines: string[],
  key: string
): { startLine: number; endLine: number } | null {
  const pattern = new RegExp(`^${key}:\\s*(.*)$`);
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (!pattern.test(line.trim())) {
      continue;
    }
    const rawIndent = line.match(/^\s*/)?.[0].length ?? 0;
    if (rawIndent !== 0) {
      continue;
    }
    let endIndex = index;
    for (let next = index + 1; next < lines.length; next += 1) {
      const nextLine = lines[next];
      if (!nextLine.trim()) {
        endIndex = next;
        continue;
      }
      const indent = nextLine.match(/^\s*/)?.[0].length ?? 0;
      if (indent === 0) {
        break;
      }
      endIndex = next;
    }
    return {
      startLine: index + 1,
      endLine: endIndex + 1,
    };
  }
  return null;
}

export function applyEditedVars(
  yamlText: string,
  entries: SequencerOutlineMetadataEntry[]
): string {
  const { lines, hasTrailingNewline } = splitLines(yamlText);
  const replacement = buildVarsLines(entries);
  const range = findTopLevelSectionRange(lines, "vars");
  let nextLines: string[];
  if (range) {
    const startIndex = range.startLine - 1;
    const endIndex = range.endLine - 1;
    nextLines = [
      ...lines.slice(0, startIndex),
      ...replacement,
      ...lines.slice(endIndex + 1),
    ];
  } else {
    const stepsIndex = lines.findIndex((line) => /^steps:\s*/.test(line.trim()));
    if (stepsIndex >= 0) {
      nextLines = [
        ...lines.slice(0, stepsIndex),
        ...replacement,
        ...lines.slice(stepsIndex),
      ];
    } else {
      nextLines = [...lines, ...replacement];
    }
  }
  return joinLines(nextLines, hasTrailingNewline);
}

function buildTemplateSnippet(kind: BasicSequencerStepTemplate): string {
  switch (kind) {
    case "call":
      return ["- call:", '    device: ""', '    action: ""', "    params: {}"].join(
        "\n"
      );
    case "sleep":
      return "- sleep: 0.1";
    case "repeat":
      return [
        "- repeat:",
        "    times: 2",
        "    do:",
        "      - sleep: 0.1",
      ].join("\n");
    default:
      return "- sleep: 0.1";
  }
}

type ChildInsertionTarget = {
  key: SequencerChildContainer;
  label: string;
};

function findContainerInsertion(
  snippet: string,
  containerKey: SequencerChildContainer
): { insertLineIndex: number; childIndent: number } | null {
  const lines = snippet.split("\n");
  const pattern = new RegExp(`^(\\s*)${containerKey}:\\s*(?:#.*)?$`);
  let containerIndex = -1;
  let containerIndent = 0;

  for (let index = 1; index < lines.length; index += 1) {
    const match = lines[index]?.match(pattern);
    if (!match) {
      continue;
    }
    containerIndex = index;
    containerIndent = match[1]?.length ?? 0;
    break;
  }

  if (containerIndex < 0) {
    return null;
  }

  let insertLineIndex = containerIndex + 1;
  for (let index = containerIndex + 1; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    if (!line.trim()) {
      insertLineIndex = index + 1;
      continue;
    }
    const indent = line.match(/^\s*/)?.[0].length ?? 0;
    if (indent <= containerIndent) {
      break;
    }
    insertLineIndex = index + 1;
  }

  return {
    insertLineIndex,
    childIndent: containerIndent + 2,
  };
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
  return insertLinesAfter(
    yamlText,
    node.endLine,
    indentSnippet(node.snippet, node.indent)
  );
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
  return replaceStepSnippet(yamlText, node, snippetLines.join("\n"));
}

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
  const nextLines = [
    ...lines.slice(0, startIndex),
    ...lines.slice(endIndex + 1),
  ];

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
