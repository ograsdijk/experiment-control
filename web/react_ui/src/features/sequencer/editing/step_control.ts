import type {
  SequencerOutlineMetadataEntry,
  SequencerSetContextStreamDetail,
  SequencerStepOutlineNode,
} from "../types";
import {
  buildNestedEntryLines,
  replaceStepSnippet,
  sanitizeYamlScalar,
  stepSiblingTailLines,
} from "./shared";

function renderSleepSnippet(node: SequencerStepOutlineNode, duration: string): string {
  const lines = [`- sleep: ${sanitizeYamlScalar(duration)}`];
  const tail = node.snippet.split("\n").slice(1);
  return [...lines, ...tail].join("\n");
}

function renderSetSnippet(
  node: SequencerStepOutlineNode,
  device: string,
  name: string,
  value: string
): string {
  const lines = [
    "- set:",
    `    device: ${sanitizeYamlScalar(device)}`,
    `    name: ${sanitizeYamlScalar(name)}`,
    `    value: ${sanitizeYamlScalar(value)}`,
  ];
  return [...lines, ...stepSiblingTailLines(node.snippet)].join("\n");
}

function renderWaitUntilSnippet(
  timeoutS: string,
  everyS: string,
  sample: SequencerOutlineMetadataEntry[],
  condition: SequencerOutlineMetadataEntry[]
): string {
  const lines = ["- wait_until:"];
  if (timeoutS.trim()) {
    lines.push(`    timeout_s: ${sanitizeYamlScalar(timeoutS)}`);
  }
  if (everyS.trim()) {
    lines.push(`    every_s: ${sanitizeYamlScalar(everyS)}`);
  }
  const cleanSample = sample
    .map((entry) => ({
      name: entry.name.trim(),
      value: sanitizeYamlScalar(entry.value ?? ""),
    }))
    .filter((entry) => entry.name.length > 0);
  const cleanCondition = condition
    .map((entry) => ({
      name: entry.name.trim(),
      value: sanitizeYamlScalar(entry.value ?? ""),
    }))
    .filter((entry) => entry.name.length > 0);

  if (cleanSample.length <= 0) {
    lines.push("    sample: {}");
  } else {
    lines.push("    sample:");
    lines.push(...buildNestedEntryLines(cleanSample, 6));
  }

  if (cleanCondition.length <= 0) {
    lines.push("    condition: {}");
  } else {
    lines.push("    condition:");
    lines.push(...buildNestedEntryLines(cleanCondition, 6));
  }

  return lines.join("\n");
}

function renderRepeatSnippet(node: SequencerStepOutlineNode, times: string): string {
  const lines = node.snippet.split("\n");
  const doIndex = lines.findIndex(
    (line, index) => index > 0 && /^\s*do:\s*(?:#.*)?$/.test(line)
  );
  const bodyLines = doIndex >= 0 ? lines.slice(doIndex) : ["    do:"];
  return ["- repeat:", `    times: ${sanitizeYamlScalar(times)}`, ...bodyLines].join(
    "\n"
  );
}

function renderAssignSnippet(
  node: SequencerStepOutlineNode,
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): string {
  const cleanEntries = entries
    .map((entry) => ({
      name: entry.name.trim(),
      value: sanitizeYamlScalar(entry.value ?? ""),
    }))
    .filter((entry) => entry.name.length > 0);

  const lines =
    cleanEntries.length <= 0
      ? ["- assign: {}"]
      : ["- assign:", ...buildNestedEntryLines(cleanEntries, 4)];

  return [...lines, ...stepSiblingTailLines(node.snippet)].join("\n");
}

function renderSetContextSnippet(
  streams: ReadonlyArray<SequencerSetContextStreamDetail>,
  fields: ReadonlyArray<SequencerOutlineMetadataEntry>
): string {
  const lines = ["- set_context:"];
  const cleanStreams = streams
    .map((entry) => ({
      device: sanitizeYamlScalar(entry.device ?? ""),
      stream: sanitizeYamlScalar(entry.stream ?? ""),
    }));
  const cleanFields = fields
    .map((entry) => ({
      name: entry.name.trim(),
      value: sanitizeYamlScalar(entry.value ?? ""),
    }))
    .filter((entry) => entry.name.length > 0);

  if (cleanStreams.length <= 0) {
    lines.push("    streams: []");
  } else {
    lines.push("    streams:");
    for (const entry of cleanStreams) {
      lines.push("      -");
      lines.push(`          device: ${entry.device}`);
      lines.push(`          stream: ${entry.stream}`);
    }
  }

  if (cleanFields.length <= 0) {
    lines.push("    fields: {}");
  } else {
    lines.push("    fields:");
    lines.push(...buildNestedEntryLines(cleanFields, 6));
  }

  return lines.join("\n");
}

function renderIfSnippet(
  node: SequencerStepOutlineNode,
  condition: SequencerOutlineMetadataEntry[]
): string {
  const lines = ["- if:"];
  const cleanCondition = condition
    .map((entry) => ({
      name: entry.name.trim(),
      value: sanitizeYamlScalar(entry.value ?? ""),
    }))
    .filter((entry) => entry.name.length > 0);
  if (cleanCondition.length <= 0) {
    lines.push("    condition: {}");
  } else {
    lines.push("    condition:");
    lines.push(...buildNestedEntryLines(cleanCondition, 6));
  }
  const snippetLines = node.snippet.split("\n");
  const bodyIndex = snippetLines.findIndex(
    (line, index) => index > 0 && /^\s*(then|else):\s*(?:#.*)?$/.test(line)
  );
  const bodyLines = bodyIndex >= 0 ? snippetLines.slice(bodyIndex) : ["    then:"];
  return [...lines, ...bodyLines].join("\n");
}

function renderWhileSnippet(
  node: SequencerStepOutlineNode,
  condition: SequencerOutlineMetadataEntry[]
): string {
  const lines = ["- while:"];
  const cleanCondition = condition
    .map((entry) => ({
      name: entry.name.trim(),
      value: sanitizeYamlScalar(entry.value ?? ""),
    }))
    .filter((entry) => entry.name.length > 0);
  if (cleanCondition.length <= 0) {
    lines.push("    condition: {}");
  } else {
    lines.push("    condition:");
    lines.push(...buildNestedEntryLines(cleanCondition, 6));
  }
  const snippetLines = node.snippet.split("\n");
  const bodyIndex = snippetLines.findIndex(
    (line, index) => index > 0 && /^\s*do:\s*(?:#.*)?$/.test(line)
  );
  const bodyLines = bodyIndex >= 0 ? snippetLines.slice(bodyIndex) : ["    do:"];
  return [...lines, ...bodyLines].join("\n");
}

export function applyEditedSleepStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  duration: string
): string {
  return replaceStepSnippet(yamlText, node, renderSleepSnippet(node, duration));
}

export function applyEditedSetStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  device: string,
  name: string,
  value: string
): string {
  return replaceStepSnippet(yamlText, node, renderSetSnippet(node, device, name, value));
}

export function applyEditedWaitUntilStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  timeoutS: string,
  everyS: string,
  sample: SequencerOutlineMetadataEntry[],
  condition: SequencerOutlineMetadataEntry[]
): string {
  return replaceStepSnippet(
    yamlText,
    node,
    renderWaitUntilSnippet(timeoutS, everyS, sample, condition)
  );
}

export function applyEditedRepeatStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  times: string
): string {
  return replaceStepSnippet(yamlText, node, renderRepeatSnippet(node, times));
}

export function applyEditedAssignStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): string {
  return replaceStepSnippet(yamlText, node, renderAssignSnippet(node, entries));
}

export function applyEditedSetContextStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  streams: ReadonlyArray<SequencerSetContextStreamDetail>,
  fields: ReadonlyArray<SequencerOutlineMetadataEntry>
): string {
  return replaceStepSnippet(
    yamlText,
    node,
    renderSetContextSnippet(streams, fields)
  );
}

export function applyEditedIfStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  condition: SequencerOutlineMetadataEntry[]
): string {
  return replaceStepSnippet(yamlText, node, renderIfSnippet(node, condition));
}

export function applyEditedWhileStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  condition: SequencerOutlineMetadataEntry[]
): string {
  return replaceStepSnippet(yamlText, node, renderWhileSnippet(node, condition));
}
