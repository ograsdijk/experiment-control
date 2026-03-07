import type { SequencerOutlineMetadataEntry, SequencerStepOutlineNode } from "../types";
import {
  buildNestedEntryLines,
  replaceStepSnippet,
  sanitizeYamlScalar,
  stepSiblingTailLines,
} from "./shared";

function renderCallSnippet(
  node: SequencerStepOutlineNode,
  device: string,
  action: string,
  params: SequencerOutlineMetadataEntry[]
): string {
  const lines = [
    "- call:",
    `    device: ${sanitizeYamlScalar(device)}`,
    `    action: ${sanitizeYamlScalar(action)}`,
  ];
  const cleanParams = params
    .map((entry) => ({
      name: entry.name.trim(),
      value: sanitizeYamlScalar(entry.value ?? ""),
    }))
    .filter((entry) => entry.name.length > 0);
  if (cleanParams.length <= 0) {
    lines.push("    params: {}");
  } else {
    lines.push("    params:");
    lines.push(...buildNestedEntryLines(cleanParams, 6));
  }
  return [...lines, ...stepSiblingTailLines(node.snippet)].join("\n");
}

export function applyEditedCallStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  device: string,
  action: string,
  params: SequencerOutlineMetadataEntry[]
): string {
  return replaceStepSnippet(yamlText, node, renderCallSnippet(node, device, action, params));
}
