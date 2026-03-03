import type {
  SequencerAdaptiveFieldGroup,
  SequencerAdaptiveMetricDetail,
  SequencerOutlineMetadataEntry,
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
