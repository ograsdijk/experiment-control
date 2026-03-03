import type { SequencerOutlineMetadataEntry, SequencerStepOutlineNode } from "../types";
import {
  buildNestedEntryLines,
  replaceStepSnippet,
  sanitizeYamlScalar,
} from "./shared";

function renderForSnippet(
  node: SequencerStepOutlineNode,
  bind: SequencerOutlineMetadataEntry[],
  sourceMode: "generator" | "direct",
  generatorKind: string | null,
  directValue: string,
  generatorModifiers: SequencerOutlineMetadataEntry[],
  iterableConfig: SequencerOutlineMetadataEntry[]
): string {
  const lines = ["- for:"];
  const cleanBind = bind
    .map((entry) => ({
      name: entry.name.trim(),
      value: sanitizeYamlScalar(entry.value ?? ""),
    }))
    .filter((entry) => entry.name.length > 0);
  if (cleanBind.length <= 0) {
    lines.push("    bind: {}");
  } else {
    lines.push("    bind:");
    for (const entry of cleanBind) {
      lines.push(`      ${entry.name}: ${entry.value}`);
    }
  }
  const cleanIterable = iterableConfig
    .map((entry) => ({
      name: entry.name.trim(),
      value: sanitizeYamlScalar(entry.value ?? ""),
    }))
    .filter((entry) => entry.name.length > 0);

  if (sourceMode === "direct") {
    lines.push(`    in: ${sanitizeYamlScalar(directValue)}`);
  } else {
    const normalizedKind = (generatorKind ?? "").trim() || "linspace";
    const cleanModifiers = generatorModifiers
      .map((entry) => ({
        name: entry.name.trim(),
        value: sanitizeYamlScalar(entry.value ?? ""),
      }))
      .filter((entry) => entry.name.length > 0);
    lines.push("    in:");
    lines.push("      gen:");
    for (const modifier of cleanModifiers) {
      lines.push(`        ${modifier.name}: ${modifier.value}`);
    }
    if (normalizedKind === "values") {
      if (cleanIterable.length === 1 && cleanIterable[0]?.name === "inline") {
        lines.push(`        values: ${cleanIterable[0].value}`);
      } else if (cleanIterable.length <= 0) {
        lines.push("        values: []");
      } else {
        lines.push("        values:");
        for (const entry of cleanIterable) {
          lines.push(`          - ${entry.value}`);
        }
      }
    } else if (cleanIterable.length === 1 && cleanIterable[0]?.name === "value") {
      lines.push(`        ${normalizedKind}: ${cleanIterable[0].value}`);
    } else if (cleanIterable.length <= 0) {
      lines.push(`        ${normalizedKind}: {}`);
    } else {
      lines.push(`        ${normalizedKind}:`);
      lines.push(...buildNestedEntryLines(cleanIterable, 10));
    }
  }

  const snippetLines = node.snippet.split("\n");
  const doIndex = snippetLines.findIndex(
    (line, index) => index > 0 && /^\s*do:\s*(?:#.*)?$/.test(line)
  );
  const bodyLines = doIndex >= 0 ? snippetLines.slice(doIndex) : ["    do:"];
  return [...lines, ...bodyLines].join("\n");
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
  return replaceStepSnippet(
    yamlText,
    node,
    renderForSnippet(
      node,
      bind,
      sourceMode,
      generatorKind,
      directValue,
      generatorModifiers,
      iterableConfig
    )
  );
}
