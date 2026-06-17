// Structured (real-YAML) reader for sequencer step snippets.
//
// The visual step editor used to parse YAML with line-by-line regexes, which
// could not read inline flow mappings (`bind: {value: ch}`,
// `linspace: {start, stop, num}`, `streams: - {device, stream}`) and silently
// dropped unmodeled siblings such as `sample`. This module replaces that read
// path with the `yaml` (eemeli) parser.
//
// Leaf VALUES are returned as their exact YAML source text (sliced from the
// snippet via each node's `range`), so quoting and flow spacing are preserved
// and round-trip through the flat `{name, value}` model the form components use
// (where nested structure is encoded as dotted names, e.g. `center.x`).
import { isMap, isScalar, isSeq, parseDocument, stringify } from "yaml";
import type { Document, Node, YAMLMap } from "yaml";
import type { SequencerOutlineMetadataEntry } from "./types";
import { conditionAstToEntries } from "./condition_ast";
import type { ConditionAst, ConditionCompareOp } from "./condition_ast";

export const FOR_GENERATOR_KINDS = new Set([
  "range",
  "linspace",
  "triangle",
  "logspace",
  "geomspace",
  "values",
  "scan2d",
]);

export const FOR_GENERATOR_MODIFIER_KEYS = new Set([
  "offset",
  "shuffle",
  "seed",
  "serpentine",
]);

const CONDITION_COMPARE_OPS = new Set([
  "eq",
  "ne",
  "gt",
  "ge",
  "lt",
  "le",
  "abs_lt",
]);

export type ParsedStep = {
  kind: string | null;
  /** Body node = value of the kind key (a map for block steps, a scalar for `- sleep: 0.5`). */
  body: Node | null;
  /** Original snippet text; required to slice node source ranges. */
  src: string;
};

/** Parse a single step snippet (`- kind: ...`). Returns null if it is not parseable. */
export function readStep(snippet: string): ParsedStep | null {
  let doc: Document;
  try {
    doc = parseDocument(snippet);
  } catch {
    return null;
  }
  if (doc.errors && doc.errors.length > 0) {
    return null;
  }
  const root = doc.contents;
  if (!isSeq(root) || root.items.length <= 0) {
    return null;
  }
  const item = root.items[0];
  if (!isMap(item) || item.items.length <= 0) {
    return null;
  }
  const first = item.items[0];
  const kind = first.key == null ? null : String(first.key).trim();
  const body = (first.value as Node | null) ?? null;
  return { kind: kind || null, body, src: snippet };
}

/** Exact YAML source text of a node, sliced from the snippet (preserves quotes/flow). */
export function leafText(node: unknown, src: string): string | null {
  if (node == null) {
    return null;
  }
  const range = (node as { range?: [number, number, number] }).range;
  if (range && typeof range[0] === "number" && typeof range[1] === "number") {
    return src.slice(range[0], range[1]).trim();
  }
  if (isScalar(node)) {
    return node.value == null ? null : String(node.value);
  }
  return null;
}

/** Render a plain JS scalar to the flat-model text form (used for condition operands). */
function scalarToText(value: unknown): string {
  if (value == null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  return String(value);
}

/** Flatten a YAML map node into dotted `{name, value}` entries (block maps recursed). */
export function flattenEntries(
  node: unknown,
  src: string,
  prefix = ""
): SequencerOutlineMetadataEntry[] {
  const out: SequencerOutlineMetadataEntry[] = [];
  if (!isMap(node)) {
    if (prefix) {
      out.push({ name: prefix, value: leafText(node, src) });
    }
    return out;
  }
  for (const pair of node.items) {
    const key = pair.key == null ? "" : String(pair.key).trim();
    if (!key) {
      continue;
    }
    const name = prefix ? `${prefix}.${key}` : key;
    const value = pair.value as Node | null;
    if (isMap(value) && value.items.length > 0) {
      out.push(...flattenEntries(value, src, name));
    } else {
      out.push({ name, value: leafText(value, src) });
    }
  }
  return out;
}

function asMap(node: Node | null): YAMLMap | null {
  return isMap(node) ? (node as YAMLMap) : null;
}

/** Child node of a map by key (keeps the node, not the JS value). */
export function childNode(base: Node | null, key: string): Node | null {
  const map = asMap(base);
  if (!map) {
    return null;
  }
  return (map.get(key, true) as Node | undefined) ?? null;
}

/**
 * Field value as source text. Scalars (including block scalars) are returned as
 * their exact source. Inline (single-line) flow collections are preserved
 * verbatim, so e.g. `set` `value: [1, 2]` keeps the author's spacing. Block
 * (multi-line) collections are flow-normalized to a single round-trippable line
 * (e.g. a block list becomes `[0, 200, 400]`) so an editable field still shows
 * the value: returning null here would blank the field and the writer would
 * overwrite the collection with an empty scalar on save.
 */
export function scalarField(base: Node | null, key: string, src: string): string | null {
  const child = childNode(base, key);
  if (child == null) {
    return null;
  }
  if (isScalar(child)) {
    return leafText(child, src);
  }
  const text = leafText(child, src);
  if (text != null && !text.includes("\n")) {
    return text;
  }
  return collectionToFlowText(child);
}

/** Serialize a collection node to a single-line flow string (round-trippable). */
function collectionToFlowText(node: Node): string | null {
  try {
    const text = stringify(node, { collectionStyle: "flow", lineWidth: 0 }).trim();
    return text.length > 0 ? text : null;
  } catch {
    return null;
  }
}

/** Entries flattened from a child map section (e.g. `params`, `fields`). */
export function sectionEntries(
  base: Node | null,
  key: string,
  src: string
): SequencerOutlineMetadataEntry[] {
  const child = childNode(base, key);
  return isMap(child) ? flattenEntries(child, src) : [];
}

/** Number of items in a child block/flow sequence (e.g. `then`, `else`). */
export function seqLength(base: Node | null, key: string): number {
  const child = childNode(base, key);
  return isSeq(child) ? child.items.length : 0;
}

/** Named field groups (e.g. adaptive `space`, `observe.metrics`). */
export function namedGroups(
  base: Node | null,
  key: string,
  src: string
): Array<{ name: string; entries: SequencerOutlineMetadataEntry[] }> {
  const map = asMap(childNode(base, key));
  if (!map) {
    return [];
  }
  return map.items.map((pair) => {
    const name = pair.key == null ? "" : String(pair.key).trim();
    const value = pair.value as Node | null;
    const entries = isMap(value)
      ? flattenEntries(value, src)
      : value != null
        ? [{ name: "value", value: leafText(value, src) }]
        : [];
    return { name, entries };
  });
}

// --- scan2d normalization ---------------------------------------------------
// The runtime accepts several equivalent shorthands (`size` vs flat
// `width`/`height`; scalar vs `{x, y}` for `pitch`/`steps`). The form binds to
// canonical flat keys, so normalize every accepted shape to those keys.
export function normalizeScan2d(
  node: Node | null,
  src: string
): SequencerOutlineMetadataEntry[] {
  const map = asMap(node);
  if (!map) {
    return [];
  }
  // Explicit axis form (`x: {linspace: ...}`, `y: {linspace: ...}`) already maps
  // to the form's expected dotted keys.
  if (map.has("x") || map.has("y")) {
    return flattenEntries(map, src);
  }

  const out: SequencerOutlineMetadataEntry[] = [];
  const push = (name: string, value: string | null) => {
    if (value != null) {
      out.push({ name, value });
    }
  };
  const at = (path: string[]): string | null => {
    const child = (map.getIn(path, true) as Node | undefined) ?? null;
    return child ? leafText(child, src) : null;
  };

  push("center.x", at(["center", "x"]));
  push("center.y", at(["center", "y"]));

  if (map.has("size")) {
    const size = map.get("size", true) as Node | null;
    if (isMap(size)) {
      push("width", at(["size", "width"]));
      push("height", at(["size", "height"]));
    } else {
      const text = leafText(size, src);
      push("width", text);
      push("height", text);
    }
  } else {
    push("width", at(["width"]));
    push("height", at(["height"]));
  }

  for (const res of ["steps", "pitch"] as const) {
    if (!map.has(res)) {
      continue;
    }
    const node2 = map.get(res, true) as Node | null;
    if (isMap(node2)) {
      push(`${res}.x`, at([res, "x"]));
      push(`${res}.y`, at([res, "y"]));
    } else {
      const text = leafText(node2, src);
      push(`${res}.x`, text);
      push(`${res}.y`, text);
    }
  }

  push("pattern", at(["pattern"]));
  push("order", at(["order"]));
  push("seed", at(["seed"]));
  return out;
}

// --- conditions -------------------------------------------------------------
// Reuse condition_ast's canonical entry format so inline and block conditions
// read identically (e.g. `{gt: [${x}, 0]}` with its exact spacing).
function conditionObjectToAst(obj: unknown): ConditionAst {
  if (obj == null || typeof obj !== "object" || Array.isArray(obj)) {
    return { kind: "raw", entries: [], reason: "non-mapping condition" };
  }
  const keys = Object.keys(obj as Record<string, unknown>);
  if (keys.length !== 1) {
    return { kind: "raw", entries: [], reason: "expected one operator key" };
  }
  const op = keys[0];
  const value = (obj as Record<string, unknown>)[op];

  if (CONDITION_COMPARE_OPS.has(op)) {
    if (Array.isArray(value) && value.length === 2) {
      return {
        kind: "compare",
        op: op as ConditionCompareOp,
        left: scalarToText(value[0]),
        right: scalarToText(value[1]),
      };
    }
    return { kind: "raw", entries: [], reason: `malformed ${op}` };
  }
  if (op === "not") {
    const child = conditionObjectToAst(value);
    if (child.kind === "empty" || child.kind === "raw") {
      return { kind: "raw", entries: [], reason: "malformed not" };
    }
    return { kind: "not", item: child };
  }
  if (op === "and" || op === "or") {
    if (!Array.isArray(value)) {
      return { kind: "raw", entries: [], reason: `malformed ${op}` };
    }
    const items: ConditionAst[] = [];
    for (const child of value) {
      const ast = conditionObjectToAst(child);
      if (ast.kind === "empty" || ast.kind === "raw") {
        return { kind: "raw", entries: [], reason: `malformed ${op} clause` };
      }
      items.push(ast);
    }
    return op === "and" ? { kind: "and", items } : { kind: "or", items };
  }
  return { kind: "raw", entries: [], reason: `unsupported operator ${op}` };
}

/** Read a `condition:` node into canonical condition entries (falls back to raw flatten). */
export function conditionEntries(
  base: Node | null,
  key: string,
  src: string
): SequencerOutlineMetadataEntry[] {
  const node = childNode(base, key);
  if (!node) {
    return [];
  }
  const ast = conditionObjectToAst(node.toJSON());
  if (ast.kind === "raw") {
    // Preserve unsupported conditions (e.g. `always: true`) verbatim from source.
    return flattenEntries(node, src);
  }
  return conditionAstToEntries(ast);
}

export { isMap, isScalar, isSeq };
