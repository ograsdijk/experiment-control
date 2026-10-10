import { Document, YAMLMap, isMap, isScalar, parseDocument, stringify } from "yaml";
import type { Node, Pair } from "yaml";
import type { SequencerOutlineMetadataEntry } from "../types";
import {
  STEP_TO_STRING,
  YAML_OPTIONS,
  canonicalJson,
  cleanEntries,
  textToNode,
} from "./yaml_write";

type TextEdit = { start: number; end: number; text: string };

function topLevelPair(doc: Document, key: string): Pair<Node, Node> | null {
  if (!isMap(doc.contents)) {
    return null;
  }
  for (const pair of doc.contents.items) {
    if (isScalar(pair.key) && String(pair.key.value) === key) {
      return pair as Pair<Node, Node>;
    }
  }
  return null;
}

function lineStart(text: string, offset: number): number {
  return text.lastIndexOf("\n", Math.max(0, offset - 1)) + 1;
}

function lineEnd(text: string, offset: number): number {
  const end = text.indexOf("\n", offset);
  return end < 0 ? text.length : end;
}

function keyName(pair: Pair<unknown, unknown>): string {
  return pair.key == null ? "" : String(pair.key).trim();
}

/** A value as one line of YAML (collections in flow style). */
function inlineText(node: Node): string {
  return stringify(node, { ...YAML_OPTIONS, ...STEP_TO_STRING, collectionStyle: "flow" }).trim();
}

function sectionText(key: string, section: YAMLMap): string {
  const doc = new Document(undefined, YAML_OPTIONS);
  doc.contents = new YAMLMap();
  (doc.contents as YAMLMap).set(key, section);
  return doc.toString(STEP_TO_STRING).replace(/\n+$/, "");
}

function applyEdits(text: string, edits: TextEdit[]): string {
  let out = text;
  for (const edit of [...edits].sort((a, b) => b.start - a.start)) {
    out = out.slice(0, edit.start) + edit.text + out.slice(edit.end);
  }
  return out;
}

/**
 * Edit a block-style section in place, entry by entry: changed values,
 * renamed keys, removed and added entries touch only their own text, so
 * untouched entries keep their exact formatting (`20.0e+6` stays as written)
 * and comments. Returns null when the section can't be edited this way.
 */
function editSectionInPlace(
  yamlText: string,
  section: YAMLMap,
  desired: Array<{ name: string; node: Node }>
): string | null {
  const items = section.items as Array<Pair<Node, Node>>;
  if (section.flow || items.length === 0) {
    return null;
  }
  if (items.some((pair) => !pair.key?.range || !pair.value?.range)) {
    return null;
  }
  if (new Set(desired.map((entry) => entry.name)).size !== desired.length) {
    return null;
  }
  const desiredNames = new Set(desired.map((entry) => entry.name));
  const used = new Set<number>();
  const match: Array<number | null> = desired.map((entry) => {
    const index = items.findIndex(
      (pair, i) => !used.has(i) && keyName(pair) === entry.name
    );
    if (index < 0) {
      return null;
    }
    used.add(index);
    return index;
  });
  const edits: TextEdit[] = [];
  // An entry renamed in place: same position, old name no longer wanted.
  desired.forEach((entry, i) => {
    if (match[i] !== null || i >= items.length || used.has(i)) {
      return;
    }
    if (desiredNames.has(keyName(items[i]))) {
      return;
    }
    match[i] = i;
    used.add(i);
    const range = items[i].key.range!;
    edits.push({ start: range[0], end: range[1], text: inlineText(textToNode(new Document(undefined, YAML_OPTIONS), entry.name)) });
  });
  desired.forEach((entry, i) => {
    const index = match[i];
    if (index === null) {
      return;
    }
    const value = items[index].value!;
    if (canonicalJson(value.toJSON()) === canonicalJson(entry.node.toJSON())) {
      return;
    }
    const range = value.range!;
    edits.push({ start: range[0], end: range[1], text: inlineText(entry.node) });
  });
  items.forEach((pair, index) => {
    if (used.has(index)) {
      return;
    }
    const start = lineStart(yamlText, pair.key.range![0]);
    const end = lineEnd(yamlText, Math.max(pair.key.range![1], pair.value!.range![1] - 1));
    edits.push({ start, end: Math.min(yamlText.length, end + 1), text: "" });
  });
  const added = desired.filter((_, i) => match[i] === null);
  if (added.length > 0) {
    const first = items[0].key.range![0];
    const indent = " ".repeat(first - lineStart(yamlText, first));
    const last = items[items.length - 1];
    const at = lineEnd(yamlText, Math.max(last.key.range![1], last.value!.range![1] - 1));
    const lines = added.map(
      (entry) => `\n${indent}${inlineText(textToNode(new Document(undefined, YAML_OPTIONS), entry.name))}: ${inlineText(entry.node)}`
    );
    edits.push({ start: at, end: at, text: lines.join("") });
  }
  return applyEdits(yamlText, edits);
}

/**
 * Rewrite one top-level section (`vars`, `context_columns`). The rest of the
 * file is kept byte for byte: re-serializing the whole document reflowed
 * folded descriptions and other formatting on every edit.
 */
function applyEditedTopLevelMetadataSection(
  yamlText: string,
  key: "vars" | "context_columns",
  entries: SequencerOutlineMetadataEntry[]
): string {
  const doc = parseDocument(yamlText, YAML_OPTIONS);
  if (doc.errors.length > 0 || !isMap(doc.contents)) {
    return yamlText;
  }
  const desired = cleanEntries(entries).map((entry) => ({
    name: entry.name,
    node: textToNode(doc, entry.value),
  }));
  const pair = topLevelPair(doc, key);
  const currentSection = isMap(pair?.value) ? (pair!.value as YAMLMap) : null;
  const desiredJson = canonicalJson(
    Object.fromEntries(desired.map((entry) => [entry.name, entry.node.toJSON()]))
  );
  if (currentSection && canonicalJson(currentSection.toJSON()) === desiredJson) {
    return yamlText;
  }
  if (!currentSection && !pair && desired.length === 0) {
    return yamlText;
  }

  if (currentSection) {
    const edited = editSectionInPlace(yamlText, currentSection, desired);
    if (edited !== null) {
      return edited;
    }
  }

  const section = new YAMLMap();
  for (const entry of desired) {
    const existing = currentSection?.get(entry.name, true) as Node | undefined;
    section.set(
      entry.name,
      existing != null && canonicalJson(existing.toJSON()) === canonicalJson(entry.node.toJSON())
        ? existing
        : entry.node
    );
  }
  section.flow = section.items.length === 0;
  const text = sectionText(key, section);
  if (pair && pair.key?.range && pair.value?.range) {
    const start = lineStart(yamlText, pair.key.range[0]);
    const end = pair.value.range[2];
    // Keep the whitespace (blank lines) that followed the section.
    const trailing = /\s*$/.exec(yamlText.slice(start, end))?.[0] ?? "";
    const tail = yamlText.slice(end);
    const gap = trailing.includes("\n") || tail.startsWith("\n") ? trailing : "\n";
    return `${yamlText.slice(0, start)}${text}${gap}${tail}`;
  }
  // Missing section: insert it before `steps:` (or append).
  const steps = topLevelPair(doc, "steps");
  if (steps?.key?.range) {
    const start = lineStart(yamlText, steps.key.range[0]);
    return `${yamlText.slice(0, start)}${text}\n\n${yamlText.slice(start)}`;
  }
  const sep = yamlText.endsWith("\n") ? "" : "\n";
  return `${yamlText}${sep}${text}\n`;
}

export function applyEditedVars(
  yamlText: string,
  entries: SequencerOutlineMetadataEntry[]
): string {
  return applyEditedTopLevelMetadataSection(yamlText, "vars", entries);
}

export function applyEditedContextColumns(
  yamlText: string,
  entries: SequencerOutlineMetadataEntry[]
): string {
  return applyEditedTopLevelMetadataSection(yamlText, "context_columns", entries);
}
