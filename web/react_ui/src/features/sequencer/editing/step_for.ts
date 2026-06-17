import { YAMLMap, YAMLSeq } from "yaml";
import type { Document } from "yaml";
import type { SequencerOutlineMetadataEntry, SequencerStepOutlineNode } from "../types";
import { replaceStepSnippet } from "./shared";
import {
  bodyMap,
  cleanEntries,
  editStep,
  emptyMap,
  entriesToMap,
  textToNode,
} from "./yaml_write";

type Entry = SequencerOutlineMetadataEntry;

function buildGenIn(
  doc: Document,
  generatorKind: string | null,
  generatorModifiers: ReadonlyArray<Entry>,
  iterableConfig: ReadonlyArray<Entry>
): YAMLMap {
  const gen = new YAMLMap();
  const modifiers = cleanEntries(generatorModifiers);
  for (const modifier of modifiers) {
    // `sample.*` is folded back below; plain modifiers (offset/shuffle/...) pass through.
    if (modifier.name === "sample" || modifier.name.startsWith("sample.")) {
      continue;
    }
    gen.set(modifier.name, textToNode(doc, modifier.value));
  }

  const kind = (generatorKind ?? "").trim() || "linspace";
  const config = cleanEntries(iterableConfig);
  if (kind === "values") {
    if (config.length === 1 && config[0].name === "inline") {
      gen.set("values", textToNode(doc, config[0].value));
    } else {
      const seq = new YAMLSeq();
      seq.flow = true;
      for (const entry of config) {
        seq.add(textToNode(doc, entry.value));
      }
      gen.set("values", seq);
    }
  } else if (kind === "scan2d") {
    gen.set("scan2d", entriesToMap(doc, config));
  } else {
    gen.set(kind, entriesToMap(doc, config, true));
  }

  const sampleEntries = modifiers
    .filter((modifier) => modifier.name.startsWith("sample."))
    .map((modifier) => ({
      name: modifier.name.slice("sample.".length),
      value: modifier.value,
    }));
  if (sampleEntries.length > 0) {
    gen.set("sample", entriesToMap(doc, sampleEntries, true));
  }

  const inMap = new YAMLMap();
  inMap.set("gen", gen);
  return inMap;
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
  const out = editStep(node.snippet, (doc, item) => {
    const body = bodyMap(item, "for");
    body.set(
      "bind",
      cleanEntries(bind).length > 0 ? entriesToMap(doc, bind, true) : emptyMap()
    );
    if (sourceMode === "direct") {
      const value = directValue && directValue.trim() ? directValue : '"${points}"';
      body.set("in", textToNode(doc, value));
    } else {
      body.set("in", buildGenIn(doc, generatorKind, generatorModifiers, iterableConfig));
    }
  });
  return replaceStepSnippet(yamlText, node, out);
}
