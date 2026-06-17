import type { SequencerOutlineMetadataEntry, SequencerStepOutlineNode } from "../types";

export type BasicSequencerStepTemplate =
  | "call"
  | "sleep"
  | "repeat"
  | "adaptive"
  | "set"
  | "assign"
  | "wait_until"
  | "set_context"
  | "for"
  | "if"
  | "while";
export type SequencerChildContainer = "do" | "then" | "else";

export function splitLines(yamlText: string): {
  lines: string[];
  hasTrailingNewline: boolean;
} {
  const normalized = yamlText.replace(/\r\n/g, "\n");
  const hasTrailingNewline = normalized.endsWith("\n");
  const lines = normalized.split("\n");
  if (hasTrailingNewline) {
    lines.pop();
  }
  return { lines, hasTrailingNewline };
}

export function joinLines(lines: string[], hasTrailingNewline: boolean): string {
  const out = lines.join("\n");
  return hasTrailingNewline ? `${out}\n` : out;
}

export function replaceLineRange(
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

export function insertLinesAfter(
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

export function indentSnippet(snippet: string, indent: number): string[] {
  const prefix = " ".repeat(Math.max(0, indent));
  return snippet
    .split("\n")
    .map((line) => (line.length > 0 ? `${prefix}${line}` : line));
}

export function replaceStepSnippet(
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

export function sanitizeYamlScalar(value: string): string {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : '""';
}

export function buildTemplateSnippet(kind: BasicSequencerStepTemplate): string {
  switch (kind) {
    case "call":
      return ["- call:", '    device: ""', '    action: ""', "    params: {}"].join(
        "\n"
      );
    case "sleep":
      return "- sleep: 0.1";
    case "repeat":
      return ["- repeat:", "    times: 2", "    do:", "      - sleep: 0.1"].join(
        "\n"
      );
    case "adaptive":
      return [
        "- adaptive:",
        '    id: ""',
        "    controller:",
        '      kind: "adaptive.adaptive_grid_1d"',
        "    space:",
        "      x:",
        "        type: float",
        "        min: 0",
        "        max: 1",
        "    bind:",
        "      value: x",
        "    observe:",
        "      metrics:",
        "        score:",
        "          kind: analysis_output",
        "          config:",
        '            workspace_id: ""',
        '            output_id: ""',
        "      aggregate:",
        "        score: [mean]",
        "      score: ${metrics.score}",
        "    stopping:",
        "      max_trials: 20",
        "    do:",
        "      - sleep: 0.1",
      ].join("\n");
    case "set":
      return [
        "- set:",
        '    device: ""',
        '    name: ""',
        '    value: ""',
      ].join("\n");
    case "assign":
      return "- assign: {}";
    case "wait_until":
      return [
        "- wait_until:",
        "    timeout_s: 10",
        "    every_s: 0.2",
        "    sample:",
        "      telemetry:",
        '        device: ""',
        '        signal: ""',
        "    condition:",
        "      gt: [${sample}, 0.0]",
      ].join("\n");
    case "set_context":
      return [
        "- set_context:",
        "    streams:",
        "      -",
        '          device: ""',
        '          stream: ""',
        "    fields: {}",
      ].join("\n");
    case "for":
      return [
        "- for:",
        "    bind: value",
        "    in:",
        "      gen:",
        "        range: {start: 0, stop: 10, step: 1}",
        "    do:",
        "      - sleep: 0.1",
      ].join("\n");
    case "if":
      return [
        "- if:",
        "    condition:",
        "      gt: [${value}, 0.0]",
        "    then:",
        "      - sleep: 0.1",
        "    else: []",
      ].join("\n");
    case "while":
      return [
        "- while:",
        "    condition:",
        "      lt: [${value}, 10]",
        "    do:",
        "      - sleep: 0.1",
      ].join("\n");
    default:
      return "- sleep: 0.1";
  }
}

export function findContainerInsertion(
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

  return { insertLineIndex, childIndent: containerIndent + 2 };
}

export function findTopLevelSectionRange(
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
    return { startLine: index + 1, endLine: endIndex + 1 };
  }
  return null;
}

export function buildTopLevelMetadataSectionLines(
  key: string,
  entries: SequencerOutlineMetadataEntry[]
): string[] {
  const cleanEntries = entries
    .map((entry) => ({
      name: entry.name.trim(),
      value: sanitizeYamlScalar(entry.value ?? ""),
    }))
    .filter((entry) => entry.name.length > 0);
  if (cleanEntries.length <= 0) {
    return [`${key}: {}`];
  }
  return [
    `${key}:`,
    ...cleanEntries.map((entry) => `  ${entry.name}: ${entry.value}`),
  ];
}
