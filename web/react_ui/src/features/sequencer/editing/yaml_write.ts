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
import type { Document, Node } from "yaml";
import type { SequencerOutlineMetadataEntry } from "../types";
import { parseConditionEntries } from "../condition_ast";
import type { ConditionAst } from "../condition_ast";

type Entry = SequencerOutlineMetadataEntry;

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
    parsed = parseDocument(trimmed);
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
  try {
    doc = parseDocument(snippet);
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
  return doc.toString({ lineWidth: 0 }).replace(/\n+$/, "");
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
