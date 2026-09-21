import { YAMLMap, isMap, parseDocument } from "yaml";
import type { SequencerOutlineMetadataEntry } from "../types";
import { cleanEntries, textToNode } from "./yaml_write";

function applyEditedTopLevelMetadataSection(
  yamlText: string,
  key: "vars" | "context_columns",
  entries: SequencerOutlineMetadataEntry[]
): string {
  const doc = parseDocument(yamlText);
  if (doc.errors.length > 0 || !isMap(doc.contents)) {
    return yamlText;
  }
  const cleaned = cleanEntries(entries);
  const currentSection = doc.get(key, true);
  const section = isMap(currentSection) ? currentSection : new YAMLMap();
  if (!isMap(currentSection)) {
    doc.set(key, section);
  }

  const desiredNames = new Set(cleaned.map((entry) => entry.name));
  for (const pair of [...section.items]) {
    const name = pair.key == null ? "" : String(pair.key).trim();
    if (!desiredNames.has(name)) {
      section.delete(name);
    }
  }
  for (const entry of cleaned) {
    const existing = section.get(entry.name, true);
    const replacement = textToNode(doc, entry.value);
    if (
      existing != null &&
      JSON.stringify(existing.toJSON()) === JSON.stringify(replacement.toJSON())
    ) {
      continue;
    }
    section.set(entry.name, replacement);
  }
  if (section.items.length === 0) {
    section.flow = true;
  }
  return doc.toString({ lineWidth: 0 });
}

export function applyEditedVars(
  yamlText: string,
  entries: SequencerOutlineMetadataEntry[]
): string {
  return applyEditedTopLevelMetadataSection(
    yamlText,
    "vars",
    entries
  );
}

export function applyEditedContextColumns(
  yamlText: string,
  entries: SequencerOutlineMetadataEntry[]
): string {
  return applyEditedTopLevelMetadataSection(
    yamlText,
    "context_columns",
    entries
  );
}
