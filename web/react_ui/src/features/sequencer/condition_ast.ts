import type { SequencerOutlineMetadataEntry } from "./types";

export type ConditionCompareOp =
  | "eq"
  | "ne"
  | "gt"
  | "ge"
  | "lt"
  | "le"
  | "abs_lt";

export const CONDITION_COMPARE_OPERATORS: ReadonlyArray<ConditionCompareOp> = [
  "eq",
  "ne",
  "gt",
  "ge",
  "lt",
  "le",
  "abs_lt",
];

export type ConditionLogicalOp = "and" | "or" | "not";

export type ConditionAst =
  | { kind: "empty" }
  | { kind: "compare"; op: ConditionCompareOp; left: string; right: string }
  | { kind: "and"; items: ConditionAst[] }
  | { kind: "or"; items: ConditionAst[] }
  | { kind: "not"; item: ConditionAst }
  | {
      kind: "raw";
      entries: SequencerOutlineMetadataEntry[];
      reason: string;
    };

export type ConditionAstIssue = {
  severity: "error" | "warning";
  message: string;
  path: string;
};

function cloneEntries(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): SequencerOutlineMetadataEntry[] {
  return entries.map((entry) => ({ name: entry.name, value: entry.value }));
}

function splitTopLevelCsv(text: string): string[] {
  const out: string[] = [];
  let current = "";
  let depthParen = 0;
  let depthBracket = 0;
  let depthBrace = 0;
  let quote: "'" | '"' | null = null;
  let escaped = false;

  for (const ch of text) {
    if (quote) {
      current += ch;
      if (escaped) {
        escaped = false;
        continue;
      }
      if (ch === "\\") {
        escaped = true;
        continue;
      }
      if (ch === quote) {
        quote = null;
      }
      continue;
    }

    if (ch === "'" || ch === '"') {
      quote = ch;
      current += ch;
      continue;
    }
    if (ch === "(") {
      depthParen += 1;
      current += ch;
      continue;
    }
    if (ch === ")") {
      depthParen = Math.max(0, depthParen - 1);
      current += ch;
      continue;
    }
    if (ch === "[") {
      depthBracket += 1;
      current += ch;
      continue;
    }
    if (ch === "]") {
      depthBracket = Math.max(0, depthBracket - 1);
      current += ch;
      continue;
    }
    if (ch === "{") {
      depthBrace += 1;
      current += ch;
      continue;
    }
    if (ch === "}") {
      depthBrace = Math.max(0, depthBrace - 1);
      current += ch;
      continue;
    }
    if (ch === "," && depthParen === 0 && depthBracket === 0 && depthBrace === 0) {
      out.push(current.trim());
      current = "";
      continue;
    }
    current += ch;
  }
  out.push(current.trim());
  return out;
}

function splitTopLevelKeyValue(text: string): [string, string] | null {
  let depthParen = 0;
  let depthBracket = 0;
  let depthBrace = 0;
  let quote: "'" | '"' | null = null;
  let escaped = false;

  for (let index = 0; index < text.length; index += 1) {
    const ch = text[index];
    if (quote) {
      if (escaped) {
        escaped = false;
        continue;
      }
      if (ch === "\\") {
        escaped = true;
        continue;
      }
      if (ch === quote) {
        quote = null;
      }
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      continue;
    }
    if (ch === "(") {
      depthParen += 1;
      continue;
    }
    if (ch === ")") {
      depthParen = Math.max(0, depthParen - 1);
      continue;
    }
    if (ch === "[") {
      depthBracket += 1;
      continue;
    }
    if (ch === "]") {
      depthBracket = Math.max(0, depthBracket - 1);
      continue;
    }
    if (ch === "{") {
      depthBrace += 1;
      continue;
    }
    if (ch === "}") {
      depthBrace = Math.max(0, depthBrace - 1);
      continue;
    }
    if (
      ch === ":" &&
      depthParen === 0 &&
      depthBracket === 0 &&
      depthBrace === 0
    ) {
      return [text.slice(0, index).trim(), text.slice(index + 1).trim()];
    }
  }
  return null;
}

function parseBinaryList(value: string | null): [string, string] | null {
  const text = String(value ?? "").trim();
  if (!text.startsWith("[") || !text.endsWith("]")) {
    return null;
  }
  const inner = text.slice(1, -1).trim();
  if (!inner) {
    return null;
  }
  const parts = splitTopLevelCsv(inner);
  if (parts.length !== 2) {
    return null;
  }
  return [parts[0], parts[1]];
}

function isCompareOp(name: string): name is ConditionCompareOp {
  return (CONDITION_COMPARE_OPERATORS as string[]).includes(name);
}

function isLogicalOp(name: string): name is ConditionLogicalOp {
  return name === "and" || name === "or" || name === "not";
}

function parseFlowNode(text: string): ConditionAst | null {
  const trimmed = text.trim();
  if (!trimmed) {
    return null;
  }
  if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
    const inner = trimmed.slice(1, -1).trim();
    if (!inner) {
      return null;
    }
    const pair = splitTopLevelKeyValue(inner);
    if (!pair) {
      return null;
    }
    return parseKeyValueAst(pair[0], pair[1]);
  }
  const pair = splitTopLevelKeyValue(trimmed);
  if (!pair) {
    return null;
  }
  return parseKeyValueAst(pair[0], pair[1]);
}

function parseFlowList(value: string): string[] | null {
  const trimmed = value.trim();
  if (!trimmed.startsWith("[") || !trimmed.endsWith("]")) {
    return null;
  }
  const inner = trimmed.slice(1, -1).trim();
  if (!inner) {
    return [];
  }
  return splitTopLevelCsv(inner);
}

function parseKeyValueAst(name: string, value: string): ConditionAst | null {
  const key = name.trim();
  if (isCompareOp(key)) {
    const parsed = parseBinaryList(value);
    if (!parsed) {
      return null;
    }
    return {
      kind: "compare",
      op: key,
      left: parsed[0],
      right: parsed[1],
    };
  }
  if (!isLogicalOp(key)) {
    return null;
  }
  if (key === "not") {
    const node = parseFlowNode(value);
    if (!node || node.kind === "empty" || node.kind === "raw") {
      return null;
    }
    return { kind: "not", item: node };
  }
  const list = parseFlowList(value);
  if (!list) {
    return null;
  }
  const items: ConditionAst[] = [];
  for (const itemText of list) {
    const node = parseFlowNode(itemText);
    if (!node || node.kind === "empty" || node.kind === "raw") {
      return null;
    }
    items.push(node);
  }
  return key === "and" ? { kind: "and", items } : { kind: "or", items };
}

function astToFlowObject(ast: ConditionAst): string | null {
  if (ast.kind === "compare") {
    return `{${ast.op}: [${ast.left}, ${ast.right}]}`;
  }
  if (ast.kind === "not") {
    const child = astToFlowObject(ast.item);
    if (!child) {
      return null;
    }
    return `{not: ${child}}`;
  }
  if (ast.kind === "and" || ast.kind === "or") {
    const parts = ast.items
      .map((item) => astToFlowObject(item))
      .filter((item): item is string => Boolean(item));
    return `{${ast.kind}: [${parts.join(", ")}]}`;
  }
  return null;
}

export function defaultConditionAst(
  op: ConditionCompareOp = "gt"
): Exclude<ConditionAst, { kind: "empty" } | { kind: "raw" }> {
  if (op === "abs_lt") {
    return {
      kind: "compare",
      op,
      left: "${sample_reduced - target}",
      right: "0.1",
    };
  }
  return {
    kind: "compare",
    op,
    left: "${sample_reduced}",
    right: "0.0",
  };
}

export function parseConditionEntries(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): ConditionAst {
  const cleaned = entries
    .map((entry) => ({
      name: entry.name.trim(),
      value: entry.value,
    }))
    .filter((entry) => entry.name.length > 0);

  if (cleaned.length <= 0) {
    return { kind: "empty" };
  }
  if (cleaned.length !== 1) {
    return {
      kind: "raw",
      entries: cloneEntries(cleaned),
      reason: "builder supports one top-level operator entry",
    };
  }

  const entry = cleaned[0];
  const parsed = parseKeyValueAst(entry.name, String(entry.value ?? "").trim());
  if (!parsed) {
    return {
      kind: "raw",
      entries: cloneEntries(cleaned),
      reason: `unsupported or malformed condition expression for operator: ${entry.name}`,
    };
  }
  return parsed;
}

export function conditionAstToEntries(ast: ConditionAst): SequencerOutlineMetadataEntry[] {
  if (ast.kind === "empty") {
    return [];
  }
  if (ast.kind === "raw") {
    return cloneEntries(ast.entries);
  }
  if (ast.kind === "not") {
    const child = astToFlowObject(ast.item);
    if (!child) {
      return [];
    }
    return [{ name: "not", value: child }];
  }
  if (ast.kind === "and" || ast.kind === "or") {
    const parts = ast.items
      .map((item) => astToFlowObject(item))
      .filter((item): item is string => Boolean(item));
    return [{ name: ast.kind, value: `[${parts.join(", ")}]` }];
  }
  return [
    {
      name: ast.op,
      value: `[${ast.left}, ${ast.right}]`,
    },
  ];
}

export function validateConditionAst(ast: ConditionAst): ConditionAstIssue[] {
  const out: ConditionAstIssue[] = [];

  const walk = (node: ConditionAst, path: string) => {
    if (node.kind === "empty") {
      out.push({
        severity: "error",
        message: "Condition is empty.",
        path,
      });
      return;
    }
    if (node.kind === "raw") {
      out.push({
        severity: "warning",
        message: `Condition is in raw mode and cannot be fully validated (${node.reason}).`,
        path,
      });
      return;
    }
    if (node.kind === "compare") {
      if (!node.left.trim()) {
        out.push({
          severity: "error",
          message: `Left argument is required for '${node.op}'.`,
          path,
        });
      }
      if (!node.right.trim()) {
        out.push({
          severity: "error",
          message: `Right argument is required for '${node.op}'.`,
          path,
        });
      }
      return;
    }
    if (node.kind === "not") {
      walk(node.item, `${path}.not`);
      return;
    }

    if (node.items.length <= 0) {
      out.push({
        severity: "error",
        message: `'${node.kind}' requires at least one clause.`,
        path,
      });
      return;
    }
    if (node.items.length === 1) {
      out.push({
        severity: "warning",
        message: `'${node.kind}' has only one clause; consider removing the wrapper.`,
        path,
      });
    }
    node.items.forEach((item, index) => walk(item, `${path}[${index}]`));
  };

  walk(ast, "root");
  return out;
}
