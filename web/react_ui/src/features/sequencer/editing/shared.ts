import type { SequencerOutlineMetadataEntry, SequencerStepOutlineNode } from "../types";

export type BasicSequencerStepTemplate = "call" | "sleep" | "repeat";
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

type NestedEntryNode = {
  value: string | null;
  children: Map<string, NestedEntryNode>;
};

function createNestedEntryNode(): NestedEntryNode {
  return { value: null, children: new Map<string, NestedEntryNode>() };
}

export function buildNestedEntryLines(
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

export function stepSiblingTailLines(snippet: string): string[] {
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
