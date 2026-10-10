// Document-based, lossless writers for sequencer step snippets.
//
// Each writer parses the step snippet with the `yaml` (eemeli) parser, mutates
// ONLY the fields the inspector form models, and stringifies. Keys the form
// does not model — nested bodies (`do`/`then`/`else`), step-level siblings
// (`save_as`/`extract`/`assign`, `wait_until.reduce`/`stable_for_s`), the
// generator `sample` modifier, and comments on untouched nodes — are left
// intact. This replaces the previous line-by-line string builders, which
// rebuilt the whole step from a flat model and silently dropped anything they
// did not model.
import { YAMLMap, YAMLSeq, isMap, isSeq, parseDocument } from "yaml";
import type { Document, Node, ScalarTag, Tags } from "yaml";
import type { SequencerOutlineMetadataEntry } from "../types";
import { parseConditionEntries } from "../condition_ast";
import type { ConditionAst } from "../condition_ast";

type Entry = SequencerOutlineMetadataEntry;

const NUMBER_TAGS = new Set(["tag:yaml.org,2002:int", "tag:yaml.org,2002:float"]);

/**
 * Number tags that write a number back as it was written (`5e6`, `20.0e+6`,
 * `0x1F`) while the source text still means the same value; the default
 * stringifiers normalize them (`5e+6`, `2e+7`, `0x1f`).
 */
function keepNumberSource(tags: Tags): Tags {
  return tags.map((tag) => {
    const scalar = tag as ScalarTag;
    if (!NUMBER_TAGS.has(String(scalar.tag)) || typeof scalar.stringify !== "function") {
      return tag;
    }
    const base = scalar.stringify;
    return {
      ...scalar,
      stringify(item, ctx, onComment, onChompKeep) {
        const source = (item as { source?: unknown }).source;
        if (
          typeof source === "string" &&
          scalar.test?.test(source) &&
          scalar.resolve(source, () => undefined, {}) === item.value
        ) {
          return source;
        }
        return base(item, ctx, onComment, onChompKeep);
      },
    } as ScalarTag;
  });
}

/** Options for every document the editors parse or create. */
export const YAML_OPTIONS = { customTags: keepNumberSource } as const;

/** Drop entries with blank names and normalize whitespace. */
export function cleanEntries(entries: ReadonlyArray<Entry>): Entry[] {
  return entries
    .map((entry) => ({ name: entry.name.trim(), value: entry.value }))
    .filter((entry) => entry.name.length > 0);
}

/**
 * Parse a flat-model value (raw YAML source text) into a node, preserving the
 * author's quoting/structure. A bare `${expr}` parses as a plain scalar; when
 * placed inside a flow collection the serializer quotes it as needed. Falls
 * back to a literal string scalar if the text is not parseable on its own.
 */
export function textToNode(doc: Document, text: string | null | undefined): Node {
  const raw = text ?? "";
  const trimmed = raw.trim();
  if (trimmed === "") {
    return doc.createNode("") as unknown as Node;
  }
  let parsed: Document | null = null;
  try {
    parsed = parseDocument(trimmed, YAML_OPTIONS);
  } catch {
    parsed = null;
  }
  if (!parsed || (parsed.errors && parsed.errors.length > 0) || parsed.contents == null) {
    return doc.createNode(raw) as unknown as Node;
  }
  return parsed.contents as unknown as Node;
}

/** An empty flow mapping node (`{}`). */
export function emptyMap(): YAMLMap {
  const map = new YAMLMap();
  map.flow = true;
  return map;
}

/** Build a (possibly nested) map from dotted-name entries; `flow` controls top-level style. */
export function entriesToMap(
  doc: Document,
  entries: ReadonlyArray<Entry>,
  flow = false
): YAMLMap {
  const root = new YAMLMap();
  for (const entry of cleanEntries(entries)) {
    const parts = entry.name.split(".").filter((part) => part.trim().length > 0);
    if (parts.length <= 0) {
      continue;
    }
    let cursor = root;
    for (let i = 0; i < parts.length - 1; i += 1) {
      let child = cursor.get(parts[i], true) as Node | undefined;
      if (!isMap(child)) {
        child = new YAMLMap();
        cursor.set(parts[i], child);
      }
      cursor = child as YAMLMap;
    }
    cursor.set(parts[parts.length - 1], textToNode(doc, entry.value));
  }
  if (flow) {
    root.flow = true;
  }
  return root;
}

/** Set a scalar key, or delete it when the value is blank (matches prior "omit if empty"). */
export function setScalarOrDelete(
  doc: Document,
  body: YAMLMap,
  key: string,
  value: string
): void {
  if (value.trim().length > 0) {
    body.set(key, textToNode(doc, value));
  } else {
    body.delete(key);
  }
}

/** Get (or create) the body map for a step item `{kind: body}`. */
export function bodyMap(item: YAMLMap, kind: string): YAMLMap {
  let body = item.get(kind, true) as Node | undefined;
  if (!isMap(body)) {
    body = new YAMLMap();
    item.set(kind, body);
  }
  return body as YAMLMap;
}

/**
 * Parse the step snippet, run `mutate` on the step item map, and stringify.
 * Returns the snippet unchanged if it does not parse to a single step item.
 */
export function editStep(
  snippet: string,
  mutate: (doc: Document, item: YAMLMap, kind: string) => void
): string {
  let doc: Document;
  let original: Document;
  try {
    doc = parseDocument(snippet, YAML_OPTIONS);
    original = parseDocument(snippet, YAML_OPTIONS);
  } catch {
    return snippet;
  }
  if (doc.errors && doc.errors.length > 0) {
    return snippet;
  }
  const root = doc.contents;
  if (!isSeq(root) || root.items.length <= 0) {
    return snippet;
  }
  const item = root.items[0];
  if (!isMap(item) || item.items.length <= 0) {
    return snippet;
  }
  const kind = item.items[0].key == null ? "" : String(item.items[0].key).trim();
  mutate(doc, item as YAMLMap, kind);
  const originalItem = isSeq(original.contents) ? original.contents.items[0] : null;
  if (originalItem != null && sameContent(item as Node, originalItem as Node)) {
    return snippet;
  }
  restoreUnchanged(item as Node, originalItem as Node | null);
  return doc.toString(STEP_TO_STRING).replace(/\n+$/, "");
}

/** Serializer options for step snippets: unwrapped lines, `{a: b}` flow maps. */
export const STEP_TO_STRING = { lineWidth: 0, flowCollectionPadding: false } as const;

function sameContent(a: Node | null | undefined, b: Node | null | undefined): boolean {
  if (a == null || b == null) {
    return a == null && b == null;
  }
  try {
    return canonicalJson(a.toJSON()) === canonicalJson(b.toJSON());
  } catch {
    return false;
  }
}

/** JSON with object keys sorted: key order is formatting, not content. */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(value, (_key, item) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : item
  );
}

/**
 * Writers rebuild whole sub-trees (`params`, `bind`, `in`, ...) from the form
 * model. Swap every rebuilt node whose content is unchanged back for the
 * original, so an edit to one field keeps the rest of the step's formatting
 * and comments as written.
 */
function restoreUnchanged(next: Node, original: Node | null): void {
  if (original == null) {
    return;
  }
  if (isMap(next) && isMap(original)) {
    for (const pair of next.items) {
      const key = pair.key == null ? null : String(pair.key);
      if (key === null) {
        continue;
      }
      const before = original.get(key, true) as Node | undefined;
      const after = pair.value as Node | null;
      if (before == null || after == null) {
        continue;
      }
      if (sameContent(after, before)) {
        pair.value = before;
      } else {
        restoreUnchanged(after, before);
      }
    }
    // Keys the original had keep their original order; new keys follow.
    const position = new Map(
      original.items.map((pair, index) => [String(pair.key), index] as const)
    );
    const order = (pair: { key: unknown }) =>
      position.get(String(pair.key)) ?? Number.MAX_SAFE_INTEGER;
    next.items.sort((a, b) => order(a) - order(b));
    keepStyle(next as YAMLMap, original as YAMLMap);
    return;
  }
  if (isSeq(next) && isSeq(original)) {
    next.items.forEach((after, index) => {
      const before = original.items[index] as Node | undefined;
      if (before == null || after == null) {
        return;
      }
      if (sameContent(after as Node, before)) {
        next.items[index] = before;
      } else {
        restoreUnchanged(after as Node, before);
      }
    });
    keepStyle(next as YAMLSeq, original as YAMLSeq);
  }
}

/**
 * An edited collection keeps the author's block/flow style. An empty `{}` or
 * `[]` is a template placeholder, so filling it uses the writer's style.
 */
function keepStyle(next: YAMLMap | YAMLSeq, original: YAMLMap | YAMLSeq): void {
  if (next.items.length === 0) {
    next.flow = true;
  } else if (original.items.length > 0) {
    next.flow = Boolean(original.flow);
  }
}

// --- conditions -------------------------------------------------------------
// Build a valid condition node from the flat entries. Operands are parsed
// individually (a bare `${expr}` is a valid plain scalar) so the serializer can
// quote them correctly inside flow collections.
function flowSeq(doc: Document, texts: string[]): YAMLSeq {
  const seq = new YAMLSeq();
  seq.flow = true;
  for (const text of texts) {
    seq.add(textToNode(doc, text));
  }
  return seq;
}

function astToConditionNode(doc: Document, ast: ConditionAst): Node | null {
  if (ast.kind === "compare") {
    const map = new YAMLMap();
    map.set(ast.op, flowSeq(doc, [ast.left, ast.right]));
    return map;
  }
  if (ast.kind === "not") {
    const child = astToConditionNode(doc, ast.item);
    if (!child) {
      return null;
    }
    const map = new YAMLMap();
    map.set("not", child);
    return map;
  }
  if (ast.kind === "and" || ast.kind === "or") {
    const seq = new YAMLSeq();
    seq.flow = true;
    for (const item of ast.items) {
      const child = astToConditionNode(doc, item);
      if (child) {
        seq.add(child);
      }
    }
    const map = new YAMLMap();
    map.set(ast.kind, seq);
    return map;
  }
  if (ast.kind === "raw") {
    return entriesToMap(doc, ast.entries);
  }
  return null;
}

/** Set a `condition:` key on a body from condition entries (valid, quoted YAML). */
export function setCondition(
  doc: Document,
  body: YAMLMap,
  entries: ReadonlyArray<Entry>
): void {
  const ast = parseConditionEntries(entries);
  if (ast.kind === "empty") {
    body.set("condition", emptyMap());
    return;
  }
  const node = astToConditionNode(doc, ast);
  body.set("condition", node ?? entriesToMap(doc, entries));
}
