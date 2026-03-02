import type { SequencerOutlineMetadataEntry, SequencerStepOutlineNode } from "./types";

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
