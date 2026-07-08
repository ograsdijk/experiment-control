import type {
  SequencerAdaptiveFieldGroup,
  SequencerAdaptiveMetricDetail,
  SequencerOutlineMetadataEntry,
  SequencerStepOutlineNode,
} from "./types";
import type { CapabilityParam } from "../../types";
import {
  ADAPTIVE_BIND_EXTRA_FIELDS,
  SCALAR_FOR_FIELDS,
  SCAN2D_FOR_FIELDS,
} from "./editor_constants";

export type MetricConfigFieldSpec = {
  key: string;
  label: string;
  kind: "text" | "boolean";
};

export function renderValue(value: string | null): string {
  return value ?? "";
}

export function nextParamName(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): string {
  const existing = new Set(entries.map((entry) => entry.name));
  let index = existing.size + 1;
  while (existing.has(`param_${index}`)) {
    index += 1;
  }
  return `param_${index}`;
}

export function nextEntryName(
  prefix: string,
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): string {
  const existing = new Set(entries.map((entry) => entry.name));
  let index = existing.size + 1;
  while (existing.has(`${prefix}_${index}`)) {
    index += 1;
  }
  return `${prefix}_${index}`;
}

export function duplicateNameSet(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): Set<string> {
  const seen = new Set<string>();
  const duplicates = new Set<string>();
  for (const entry of entries) {
    const name = String(entry.name ?? "").trim();
    if (!name) {
      continue;
    }
    if (seen.has(name)) {
      duplicates.add(name);
      continue;
    }
    seen.add(name);
  }
  return duplicates;
}

export function isBlank(value: string | null | undefined): boolean {
  return String(value ?? "").trim().length <= 0;
}

function parseStrictNumber(value: string | null | undefined): number | null {
  const text = String(value ?? "").trim();
  if (!text) {
    return null;
  }
  if (!/^-?\d+(?:\.\d+)?$/.test(text)) {
    return null;
  }
  const num = Number(text);
  return Number.isFinite(num) ? num : null;
}

export function isNonNegativeNumberLiteral(
  value: string | null | undefined
): boolean {
  const parsed = parseStrictNumber(value);
  return parsed !== null && parsed >= 0;
}

export function isPositiveIntegerLiteral(
  value: string | null | undefined
): boolean {
  const parsed = parseStrictNumber(value);
  return parsed !== null && Number.isInteger(parsed) && parsed > 0;
}

export function renderUnknownValue(value: unknown): string {
  if (typeof value === "string") {
    return JSON.stringify(value);
  }
  if (value === undefined) {
    return "";
  }
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

export function getCapabilityParamPlaceholder(
  param: CapabilityParam | null | undefined
): string {
  if (!param) {
    return "value";
  }
  if (param.default !== undefined) {
    return renderUnknownValue(param.default);
  }
  if (typeof param.annotation === "string" && param.annotation.trim().length > 0) {
    return param.annotation.trim();
  }
  if (typeof param.kind === "string" && param.kind.trim().length > 0) {
    return param.kind.trim();
  }
  return "value";
}

export function getCapabilityParamDefaultValue(
  param: CapabilityParam | null | undefined
): string {
  if (!param || param.default === undefined) {
    return '""';
  }
  return renderUnknownValue(param.default);
}

export function defaultForBindEntries(
  sourceMode: "generator" | "direct",
  generatorKind: string | null
): SequencerOutlineMetadataEntry[] {
  if (sourceMode === "direct") {
    return [
      { name: "value", value: '""' },
      { name: "index", value: '""' },
    ];
  }
  if (generatorKind === "scan2d") {
    return [
      { name: "x", value: "scan_x" },
      { name: "y", value: "scan_y" },
      { name: "row", value: "scan_row" },
      { name: "col", value: "scan_col" },
      { name: "index", value: "scan_idx" },
    ];
  }
  return [
    { name: "value", value: "loop_value" },
    { name: "index", value: "loop_index" },
  ];
}

export function defaultForGeneratorConfig(
  generatorKind: string
): SequencerOutlineMetadataEntry[] {
  switch (generatorKind) {
    case "range":
      return [
        { name: "start", value: "0" },
        { name: "stop", value: "10" },
        { name: "step", value: "1" },
      ];
    case "linspace":
      return [
        { name: "start", value: "0" },
        { name: "stop", value: "10" },
        { name: "num", value: "11" },
      ];
    case "triangle":
      return [
        { name: "start", value: "0" },
        { name: "stop", value: "10" },
        { name: "num", value: "11" },
      ];
    case "centered_triangle":
      return [
        { name: "center", value: "0" },
        { name: "span", value: "10" },
        { name: "num", value: "11" },
        { name: "dir", value: "1" },
      ];
    case "logspace":
      return [
        { name: "start", value: "0" },
        { name: "stop", value: "1" },
        { name: "num", value: "10" },
        { name: "base", value: "10" },
      ];
    case "geomspace":
      return [
        { name: "start", value: "1" },
        { name: "stop", value: "10" },
        { name: "num", value: "10" },
      ];
    case "values":
      return [{ name: "0", value: "0" }];
    case "scan2d":
      return [
        { name: "center.x", value: "0.0" },
        { name: "center.y", value: "0.0" },
        { name: "width", value: "1.0" },
        { name: "height", value: "1.0" },
        { name: "steps.x", value: "11" },
        { name: "steps.y", value: "11" },
        { name: "pattern", value: "serpentine" },
        { name: "order", value: "row_major" },
      ];
    default:
      return [];
  }
}

export function defaultForGeneratorModifiers(
  generatorKind: string
): SequencerOutlineMetadataEntry[] {
  if (generatorKind === "scan2d") {
    return [];
  }
  return [];
}

export function availableForBindFields(
  sourceMode: "generator" | "direct",
  generatorKind: string | null
): string[] {
  if (sourceMode === "direct") {
    return [...SCALAR_FOR_FIELDS];
  }
  if (generatorKind === "scan2d") {
    return [...SCAN2D_FOR_FIELDS];
  }
  return [...SCALAR_FOR_FIELDS];
}

export function nextBindSourceField(
  sourceMode: "generator" | "direct",
  generatorKind: string | null,
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): string {
  const allowed = availableForBindFields(sourceMode, generatorKind);
  const used = new Set(entries.map((entry) => entry.name));
  const nextAllowed = allowed.find((field) => !used.has(field));
  if (nextAllowed) {
    return nextAllowed;
  }
  return nextEntryName("field", entries);
}

export function scalarGeneratorFieldNames(generatorKind: string): string[] | null {
  switch (generatorKind) {
    case "range":
      return ["start", "stop", "step"];
    case "linspace":
    case "triangle":
      return ["start", "stop", "num"];
    case "centered_triangle":
      return ["center", "span", "num", "dir"];
    case "logspace":
      return ["start", "stop", "num", "base"];
    case "geomspace":
      return ["start", "stop", "num"];
    default:
      return null;
  }
}

export function valueByKey(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>,
  key: string
): string {
  return entries.find((entry) => entry.name === key)?.value ?? "";
}

export function setEntryValue(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>,
  key: string,
  value: string
): SequencerOutlineMetadataEntry[] {
  const index = entries.findIndex((entry) => entry.name === key);
  if (index < 0) {
    return [...entries, { name: key, value }];
  }
  return entries.map((entry, entryIndex) =>
    entryIndex === index ? { ...entry, value } : entry
  );
}

export function removeEntry(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>,
  key: string
): SequencerOutlineMetadataEntry[] {
  return entries.filter((entry) => entry.name !== key);
}

export function hasEntryPrefix(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>,
  prefix: string
): boolean {
  return entries.some((entry) => entry.name.startsWith(`${prefix}.`));
}

export function detectScan2dForm(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): "shorthand" | "explicit" {
  return hasEntryPrefix(entries, "x") || hasEntryPrefix(entries, "y")
    ? "explicit"
    : "shorthand";
}

export function detectScan2dResolutionMode(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): "steps" | "pitch" {
  return hasEntryPrefix(entries, "pitch") ? "pitch" : "steps";
}

export function buildScan2dConfig(opts: {
  previous: ReadonlyArray<SequencerOutlineMetadataEntry>;
  form: "shorthand" | "explicit";
  resolutionMode: "steps" | "pitch";
}): SequencerOutlineMetadataEntry[] {
  const pattern = valueByKey(opts.previous, "pattern") || "serpentine";
  const order = valueByKey(opts.previous, "order") || "row_major";
  const seed = valueByKey(opts.previous, "seed");

  if (opts.form === "explicit") {
    const next: SequencerOutlineMetadataEntry[] = [
      { name: "x.linspace.start", value: valueByKey(opts.previous, "x.linspace.start") || "-0.5" },
      { name: "x.linspace.stop", value: valueByKey(opts.previous, "x.linspace.stop") || "0.5" },
      { name: "x.linspace.num", value: valueByKey(opts.previous, "x.linspace.num") || "11" },
      { name: "y.linspace.start", value: valueByKey(opts.previous, "y.linspace.start") || "-0.5" },
      { name: "y.linspace.stop", value: valueByKey(opts.previous, "y.linspace.stop") || "0.5" },
      { name: "y.linspace.num", value: valueByKey(opts.previous, "y.linspace.num") || "11" },
      { name: "pattern", value: pattern },
      { name: "order", value: order },
    ];
    if (pattern === "random" && seed) {
      next.push({ name: "seed", value: seed });
    }
    return next;
  }

  const next: SequencerOutlineMetadataEntry[] = [
    { name: "center.x", value: valueByKey(opts.previous, "center.x") || "0.0" },
    { name: "center.y", value: valueByKey(opts.previous, "center.y") || "0.0" },
    { name: "width", value: valueByKey(opts.previous, "width") || "1.0" },
    { name: "height", value: valueByKey(opts.previous, "height") || "1.0" },
  ];
  if (opts.resolutionMode === "pitch") {
    next.push(
      { name: "pitch.x", value: valueByKey(opts.previous, "pitch.x") || "0.1" },
      { name: "pitch.y", value: valueByKey(opts.previous, "pitch.y") || "0.1" }
    );
  } else {
    next.push(
      { name: "steps.x", value: valueByKey(opts.previous, "steps.x") || "11" },
      { name: "steps.y", value: valueByKey(opts.previous, "steps.y") || "11" }
    );
  }
  next.push(
    { name: "pattern", value: pattern },
    { name: "order", value: order }
  );
  if (pattern === "random" && seed) {
    next.push({ name: "seed", value: seed });
  }
  return next;
}

export function modifierValue(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>,
  key: string
): string {
  return entries.find((entry) => entry.name === key)?.value ?? "";
}

export function setModifierValue(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>,
  key: string,
  value: string | null
): SequencerOutlineMetadataEntry[] {
  const cleanValue = value?.trim() ?? "";
  if (!cleanValue) {
    return entries.filter((entry) => entry.name !== key);
  }
  const existingIndex = entries.findIndex((entry) => entry.name === key);
  if (existingIndex < 0) {
    return [...entries, { name: key, value: cleanValue }];
  }
  return entries.map((entry, index) =>
    index === existingIndex ? { ...entry, value: cleanValue } : entry
  );
}

export function cloneAdaptiveSpace(
  groups: ReadonlyArray<SequencerAdaptiveFieldGroup>
): SequencerAdaptiveFieldGroup[] {
  return groups.map((group) => ({
    name: group.name,
    entries: group.entries.map((entry) => ({ ...entry })),
  }));
}

export function cloneEntries(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): SequencerOutlineMetadataEntry[] {
  return entries.map((entry) => ({ ...entry }));
}

export function cloneAdaptiveMetrics(
  metrics: ReadonlyArray<SequencerAdaptiveMetricDetail>
): SequencerAdaptiveMetricDetail[] {
  return metrics.map((metric) => ({
    name: metric.name,
    sourceKind: metric.sourceKind,
    config: cloneEntries(metric.config),
  }));
}

export function adaptiveBindFieldOptions(
  space: ReadonlyArray<SequencerAdaptiveFieldGroup>,
  bind: ReadonlyArray<SequencerOutlineMetadataEntry>
): { value: string; label: string }[] {
  const fields = new Set<string>();
  for (const group of space) {
    const name = group.name.trim();
    if (name) {
      fields.add(name);
    }
  }
  for (const extra of ADAPTIVE_BIND_EXTRA_FIELDS) {
    fields.add(extra);
  }
  for (const entry of bind) {
    const name = entry.name.trim();
    if (name) {
      fields.add(name);
    }
  }
  return Array.from(fields).map((value) => ({ value, label: value }));
}

export function metricConfigFieldSpecs(
  sourceKind: string | null
): MetricConfigFieldSpec[] {
  switch (sourceKind) {
    case "analysis_output":
      return [
        { key: "workspace_id", label: "Workspace id", kind: "text" },
        { key: "output_id", label: "Output id", kind: "text" },
        {
          key: "require_current_context",
          label: "Require current context",
          kind: "boolean",
        },
        { key: "timeout_s", label: "Timeout (s)", kind: "text" },
      ];
    case "telemetry":
      return [
        { key: "device", label: "Device", kind: "text" },
        { key: "signal", label: "Signal", kind: "text" },
        { key: "timeout_s", label: "Timeout (s)", kind: "text" },
      ];
    case "call":
      return [{ key: "timeout_s", label: "Timeout (s)", kind: "text" }];
    default:
      return [];
  }
}

export function metricExtraConfigEntries(
  metric: SequencerAdaptiveMetricDetail
): SequencerOutlineMetadataEntry[] {
  const reserved = new Set(metricConfigFieldSpecs(metric.sourceKind).map((spec) => spec.key));
  return metric.config.filter(
    (entry) => !reserved.has(entry.name) && !entry.name.startsWith("params.")
  );
}

export function metricCallParamEntries(
  metric: SequencerAdaptiveMetricDetail
): SequencerOutlineMetadataEntry[] {
  return metric.config
    .filter((entry) => entry.name.startsWith("params."))
    .map((entry) => ({
      name: entry.name.slice("params.".length),
      value: entry.value,
    }));
}

export function withMetricCallParamEntries(
  metric: SequencerAdaptiveMetricDetail,
  params: ReadonlyArray<SequencerOutlineMetadataEntry>
): SequencerOutlineMetadataEntry[] {
  const nonParamEntries = metric.config.filter((entry) => !entry.name.startsWith("params."));
  const paramEntries = params
    .map((entry) => ({
      name: entry.name.trim(),
      value: entry.value,
    }))
    .filter((entry) => entry.name.length > 0)
    .map((entry) => ({
      name: `params.${entry.name}`,
      value: entry.value,
  }));
  return [...nonParamEntries, ...paramEntries];
}

export function countMetadataNameIssues(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): number {
  return (
    duplicateNameSet(entries).size + entries.filter((entry) => isBlank(entry.name)).length
  );
}

export function countContextColumnIssues(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): number {
  const validTypes = new Set(["float64", "int64", "bool"]);
  const invalidTypes = entries.filter(
    (entry) => !isBlank(entry.name) && !isBlank(entry.value) && !validTypes.has(entry.value ?? "")
  ).length;
  return countMetadataNameIssues(entries) + invalidTypes;
}

function countCallIssues(node: SequencerStepOutlineNode): number {
  if (!node.callDetail) {
    return 0;
  }
  const blankParamNames = node.callDetail.params.filter((entry) => isBlank(entry.name)).length;
  const target =
    node.callDetail.targetKind === "process"
      ? node.callDetail.process
      : node.callDetail.device;
  return (
    (isBlank(target) ? 1 : 0) +
    (isBlank(node.callDetail.action) ? 1 : 0) +
    duplicateNameSet(node.callDetail.params).size +
    blankParamNames
  );
}

function countSetIssues(node: SequencerStepOutlineNode): number {
  if (!node.setDetail) {
    return 0;
  }
  return (
    (isBlank(node.setDetail.device) ? 1 : 0) +
    (isBlank(node.setDetail.name) ? 1 : 0) +
    (isBlank(node.setDetail.value) ? 1 : 0)
  );
}

function countAssignIssues(node: SequencerStepOutlineNode): number {
  if (!node.assignDetail) {
    return 0;
  }
  return (
    (node.assignDetail.entries.length <= 0 ? 1 : 0) +
    duplicateNameSet(node.assignDetail.entries).size +
    node.assignDetail.entries.filter((entry) => isBlank(entry.name)).length
  );
}

function countForIssues(node: SequencerStepOutlineNode): number {
  if (!node.forDetail) {
    return 0;
  }
  const detail = node.forDetail;
  let count =
    duplicateNameSet(detail.bind).size +
    detail.bind.filter((entry) => isBlank(entry.value)).length +
    detail.bind.filter((entry) => isBlank(entry.name)).length;
  if (detail.sourceMode === "direct") {
    return count + (isBlank(detail.directValue) ? 1 : 0);
  }
  if (isBlank(detail.generatorKind)) {
    count += 1;
  }
  const needsPositiveInt = (fieldName: string) =>
    fieldName === "num" ||
    fieldName.endsWith(".num") ||
    fieldName.startsWith("steps.");
  for (const entry of detail.iterableConfig) {
    if (isBlank(entry.name)) {
      count += 1;
      continue;
    }
    if (isBlank(entry.value)) {
      count += 1;
      continue;
    }
    if (needsPositiveInt(entry.name) && /^-?\d+(?:\.\d+)?$/.test(entry.value?.trim() ?? "")) {
      if (!isPositiveIntegerLiteral(entry.value)) {
        count += 1;
      }
    }
  }
  return count;
}

function countSetContextIssues(node: SequencerStepOutlineNode): number {
  if (!node.setContextDetail) {
    return 0;
  }
  const invalidStreams = node.setContextDetail.streams.filter(
    (stream) => isBlank(stream.device) !== isBlank(stream.stream)
  ).length;
  return (
    invalidStreams +
    duplicateNameSet(node.setContextDetail.fields).size +
    node.setContextDetail.fields.filter((entry) => isBlank(entry.name)).length
  );
}

function countWaitUntilIssues(node: SequencerStepOutlineNode): number {
  if (!node.waitUntilDetail) {
    return 0;
  }
  const sample = node.waitUntilDetail.sample;
  const hasTelemetry = sample.some((entry) => entry.name.startsWith("telemetry."));
  const hasCall = sample.some((entry) => entry.name.startsWith("call."));
  const telemetryDevice = valueByKey(sample, "telemetry.device");
  const telemetrySignal = valueByKey(sample, "telemetry.signal");
  const callDevice = valueByKey(sample, "call.device");
  const callAction = valueByKey(sample, "call.action");
  return (
    (sample.length <= 0 ? 1 : 0) +
    (node.waitUntilDetail.condition.length <= 0 ? 1 : 0) +
    duplicateNameSet(sample).size +
    duplicateNameSet(node.waitUntilDetail.condition).size +
    (hasTelemetry && isBlank(telemetryDevice) ? 1 : 0) +
    (hasTelemetry && isBlank(telemetrySignal) ? 1 : 0) +
    (hasCall && isBlank(callDevice) ? 1 : 0) +
    (hasCall && isBlank(callAction) ? 1 : 0)
  );
}

function countConditionIssues(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): number {
  return (entries.length <= 0 ? 1 : 0) + duplicateNameSet(entries).size;
}

function countAdaptiveIssues(node: SequencerStepOutlineNode): number {
  if (!node.adaptiveDetail) {
    return 0;
  }
  const detail = node.adaptiveDetail;
  let count =
    (isBlank(detail.id) ? 1 : 0) +
    (isBlank(detail.score) ? 1 : 0) +
    (!isBlank(valueByKey(detail.controllerConfig, "min_loss")) &&
    /^-?\d+(?:\.\d+)?$/.test(valueByKey(detail.controllerConfig, "min_loss").trim()) &&
    !isNonNegativeNumberLiteral(valueByKey(detail.controllerConfig, "min_loss"))
      ? 1
      : 0) +
    (!isBlank(valueByKey(detail.stopping, "max_trials")) &&
    /^-?\d+(?:\.\d+)?$/.test(valueByKey(detail.stopping, "max_trials").trim()) &&
    !isPositiveIntegerLiteral(valueByKey(detail.stopping, "max_trials"))
      ? 1
      : 0) +
    (!isBlank(detail.observeRepeats) &&
    /^-?\d+(?:\.\d+)?$/.test((detail.observeRepeats ?? "").trim()) &&
    !isPositiveIntegerLiteral(detail.observeRepeats)
      ? 1
      : 0);

  count +=
    duplicateNameSet(detail.space.map((group) => ({ name: group.name, value: null }))).size +
    detail.space.filter((group) => isBlank(group.name)).length;
  for (const group of detail.space) {
    count += duplicateNameSet(group.entries).size;
    count += group.entries.filter((entry) => isBlank(entry.name)).length;
  }

  count += duplicateNameSet(detail.bind).size;
  count += detail.bind.filter((entry) => isBlank(entry.name) || isBlank(entry.value)).length;

  count += detail.metrics.length <= 0 ? 1 : 0;
  const duplicateMetricNames = duplicateNameSet(
    detail.metrics.map((metric) => ({ name: metric.name, value: null }))
  );
  for (const metric of detail.metrics) {
    count += isBlank(metric.name) ? 1 : 0;
    count += !isBlank(metric.name) && duplicateMetricNames.has(metric.name.trim()) ? 1 : 0;
    if (metric.sourceKind === "analysis_output") {
      count += isBlank(valueByKey(metric.config, "workspace_id")) ? 1 : 0;
      count += isBlank(valueByKey(metric.config, "output_id")) ? 1 : 0;
    } else if (metric.sourceKind === "telemetry") {
      count += isBlank(valueByKey(metric.config, "device")) ? 1 : 0;
      count += isBlank(valueByKey(metric.config, "signal")) ? 1 : 0;
    } else if (metric.sourceKind === "call") {
      count += isBlank(valueByKey(metric.config, "device")) ? 1 : 0;
      count += isBlank(valueByKey(metric.config, "action")) ? 1 : 0;
      const callParams = metricCallParamEntries(metric);
      count += duplicateNameSet(callParams).size;
      count += callParams.filter((entry) => isBlank(entry.name)).length;
    }
  }

  count += duplicateNameSet(detail.aggregate).size;
  count += detail.aggregate.filter((entry) => isBlank(entry.name)).length;
  return count;
}

export function countStepIssues(node: SequencerStepOutlineNode): number {
  if (node.callDetail) {
    return countCallIssues(node);
  }
  if (node.setDetail) {
    return countSetIssues(node);
  }
  if (node.assignDetail) {
    return countAssignIssues(node);
  }
  if (node.forDetail) {
    return countForIssues(node);
  }
  if (node.setContextDetail) {
    return countSetContextIssues(node);
  }
  if (node.waitUntilDetail) {
    return countWaitUntilIssues(node);
  }
  if (node.ifDetail) {
    return countConditionIssues(node.ifDetail.condition);
  }
  if (node.whileDetail) {
    return countConditionIssues(node.whileDetail.condition);
  }
  if (node.adaptiveDetail) {
    return countAdaptiveIssues(node);
  }
  return 0;
}
