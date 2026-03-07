import type { SequencerOutlineMetadataEntry, SequencerStepOutlineNode } from "../types";
import {
  buildNestedEntryLines,
  replaceStepSnippet,
  sanitizeYamlScalar,
} from "./shared";

function renderAdaptiveGroupLines(
  groups: ReadonlyArray<{
    name: string;
    entries: ReadonlyArray<SequencerOutlineMetadataEntry>;
  }>,
  indent: number
): string[] {
  const lines: string[] = [];
  for (const group of groups) {
    const groupName = group.name.trim();
    if (!groupName) {
      continue;
    }
    const prefix = " ".repeat(Math.max(0, indent));
    const cleanEntries = group.entries
      .map((entry) => ({
        name: entry.name.trim(),
        value: sanitizeYamlScalar(entry.value ?? ""),
      }))
      .filter((entry) => entry.name.length > 0);
    if (cleanEntries.length === 1 && cleanEntries[0]?.name === "value") {
      lines.push(`${prefix}${groupName}: ${cleanEntries[0].value}`);
      continue;
    }
    if (cleanEntries.length <= 0) {
      lines.push(`${prefix}${groupName}: {}`);
      continue;
    }
    lines.push(`${prefix}${groupName}:`);
    lines.push(...buildNestedEntryLines(cleanEntries, indent + 2));
  }
  return lines;
}

function renderAdaptiveMetricLines(
  metrics: ReadonlyArray<{
    name: string;
    sourceKind: string | null;
    config: ReadonlyArray<SequencerOutlineMetadataEntry>;
  }>,
  indent: number
): string[] {
  const lines: string[] = [];
  for (const metric of metrics) {
    const metricName = metric.name.trim();
    if (!metricName) {
      continue;
    }
    const prefix = " ".repeat(Math.max(0, indent));
    lines.push(`${prefix}${metricName}:`);
    if (metric.sourceKind) {
      lines.push(`${prefix}  kind: ${sanitizeYamlScalar(metric.sourceKind)}`);
    }
    const cleanConfig = metric.config
      .map((entry) => ({
        name: entry.name.trim(),
        value: sanitizeYamlScalar(entry.value ?? ""),
      }))
      .filter((entry) => entry.name.length > 0);
    if (cleanConfig.length > 0) {
      lines.push(`${prefix}  config:`);
      lines.push(...buildNestedEntryLines(cleanConfig, indent + 4));
    }
  }
  return lines;
}

function renderAdaptiveSnippet(
  node: SequencerStepOutlineNode,
  adaptiveId: string,
  controllerKind: string,
  minLoss: string,
  controllerConfigExtra: ReadonlyArray<SequencerOutlineMetadataEntry>,
  space: ReadonlyArray<{
    name: string;
    entries: ReadonlyArray<SequencerOutlineMetadataEntry>;
  }>,
  bind: ReadonlyArray<SequencerOutlineMetadataEntry>,
  metrics: ReadonlyArray<{
    name: string;
    sourceKind: string | null;
    config: ReadonlyArray<SequencerOutlineMetadataEntry>;
  }>,
  aggregate: ReadonlyArray<SequencerOutlineMetadataEntry>,
  observeRepeats: string,
  score: string,
  maxTrials: string,
  stoppingExtra: ReadonlyArray<SequencerOutlineMetadataEntry>
): string {
  const detail = node.adaptiveDetail;
  if (!detail) {
    return node.snippet;
  }

  const lines = ["- adaptive:"];
  if (adaptiveId.trim()) {
    lines.push(`    id: ${sanitizeYamlScalar(adaptiveId)}`);
  }

  lines.push("    controller:");
  lines.push(
    `      kind: ${sanitizeYamlScalar(
      controllerKind || detail.controllerKind || "adaptive.adaptive_grid_1d"
    )}`
  );
  const controllerConfig = controllerConfigExtra
    .filter((entry) => entry.name !== "min_loss")
    .map((entry) => ({ ...entry }));
  if (minLoss.trim()) {
    controllerConfig.push({ name: "min_loss", value: minLoss });
  }
  if (controllerConfig.length > 0) {
    lines.push("      config:");
    lines.push(...buildNestedEntryLines(controllerConfig, 8));
  }

  if (space.length > 0) {
    lines.push("    space:");
    lines.push(...renderAdaptiveGroupLines(space, 6));
  }

  if (bind.length > 0) {
    lines.push("    bind:");
    lines.push(...buildNestedEntryLines(bind, 6));
  }

  lines.push("    observe:");
  if (observeRepeats.trim()) {
    lines.push(`      repeats: ${sanitizeYamlScalar(observeRepeats)}`);
  }
  if (metrics.length > 0) {
    lines.push("      metrics:");
    lines.push(...renderAdaptiveMetricLines(metrics, 8));
  }
  if (aggregate.length > 0) {
    lines.push("      aggregate:");
    lines.push(...buildNestedEntryLines(aggregate, 8));
  }
  if (score.trim()) {
    lines.push(`      score: ${sanitizeYamlScalar(score)}`);
  }

  const stoppingEntries = stoppingExtra
    .filter((entry) => entry.name !== "max_trials")
    .map((entry) => ({ ...entry }));
  if (maxTrials.trim()) {
    stoppingEntries.push({ name: "max_trials", value: maxTrials });
  }
  if (stoppingEntries.length > 0) {
    lines.push("    stopping:");
    lines.push(...buildNestedEntryLines(stoppingEntries, 6));
  }

  const snippetLines = node.snippet.split("\n");
  const doIndex = snippetLines.findIndex(
    (line, index) => index > 0 && /^\s*do:\s*(?:#.*)?$/.test(line)
  );
  const bodyLines = doIndex >= 0 ? snippetLines.slice(doIndex) : ["    do:"];
  return [...lines, ...bodyLines].join("\n");
}

export function applyEditedAdaptiveStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  adaptiveId: string,
  controllerKind: string,
  minLoss: string,
  controllerConfigExtra: ReadonlyArray<SequencerOutlineMetadataEntry>,
  space: ReadonlyArray<{
    name: string;
    entries: ReadonlyArray<SequencerOutlineMetadataEntry>;
  }>,
  bind: ReadonlyArray<SequencerOutlineMetadataEntry>,
  metrics: ReadonlyArray<{
    name: string;
    sourceKind: string | null;
    config: ReadonlyArray<SequencerOutlineMetadataEntry>;
  }>,
  aggregate: ReadonlyArray<SequencerOutlineMetadataEntry>,
  observeRepeats: string,
  score: string,
  maxTrials: string,
  stoppingExtra: ReadonlyArray<SequencerOutlineMetadataEntry>
): string {
  return replaceStepSnippet(
    yamlText,
    node,
    renderAdaptiveSnippet(
      node,
      adaptiveId,
      controllerKind,
      minLoss,
      controllerConfigExtra,
      space,
      bind,
      metrics,
      aggregate,
      observeRepeats,
      score,
      maxTrials,
      stoppingExtra
    )
  );
}
