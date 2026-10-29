/**
 * A sequence's `form:` block: the Sequencer's "Run with form" dialog.
 *
 * Every field sets one declared var for the next start (`vars_override`);
 * nothing is written to the YAML. The backend checks the block at load
 * (`sequencer/run_form.py`); this module reads it from the editor text, keeps
 * the dialog's values and turns them into the overrides. See
 * docs/sequencer_run_form.md.
 */

import { parse } from "yaml";
import type { CapabilityMember } from "../../types";
import { has } from "./records";

export type RunFormOption = { value: unknown; label: string };

export type RunFormOptionsCall = {
  device: string;
  action: string;
  params?: Record<string, unknown>;
  /** Key of the result holding the list (the result itself when absent). */
  list?: string;
  /** Key of each item used as the value (the item itself when absent). */
  value?: string;
  label?: string;
};

type FieldBase = { label?: string; enabled_by?: string };

export type RunFormField =
  | (FieldBase & { type: "checkbox" | "text"; var: string })
  | (FieldBase & {
      type: "number" | "integer";
      var: string;
      min?: number;
      max?: number;
      step?: number;
      unit?: string;
    })
  | (FieldBase & {
      type: "select";
      var: string;
      options?: string;
      options_call?: RunFormOptionsCall;
      empty_label?: string;
    })
  | (FieldBase & { type: "chips"; var: string; options: string })
  | (FieldBase & { type: "target"; var: string; catalog: string; other_exclude?: string[] })
  | (FieldBase & {
      type: "range";
      target: string;
      relative: string;
      from: string;
      to: string;
      points: string;
    });

export type RunFormSection = { title?: string; toggle?: string; fields: RunFormField[] };

export type RunForm = {
  title?: string;
  presets: { label: string; set: Record<string, unknown> }[];
  summary: Partial<Record<"points" | "passes" | "bursts" | "shots", string>>;
  sections: RunFormSection[];
};

/** A catalog entry (`scan_targets`), or an "Other" target built from capabilities. */
export type ScanTarget = Record<string, unknown> & {
  id: string;
  kind: string;
  device: string;
  device_label: string;
  param_label: string;
  unit: string;
  display_unit: string;
  display_scale: number;
  offset_unit: string;
  offset_scale: number;
  min: number | null;
  max: number | null;
  default_span: number;
  current?: { device: string; signal: string; abs?: boolean } | null;
};

export type ParsedRunForm = { form: RunForm; vars: Record<string, unknown> };

/** The form and vars of a sequence's YAML, or null when it has no `form:`. */
export function parseRunForm(yamlText: string): ParsedRunForm | null {
  let raw: unknown;
  try {
    raw = parse(yamlText);
  } catch {
    return null;
  }
  if (!raw || typeof raw !== "object" || !has(raw, "form")) {
    return null;
  }
  const doc = raw as Record<string, unknown>;
  const formRaw = doc.form;
  if (!formRaw || typeof formRaw !== "object") {
    return null;
  }
  const vars =
    doc.vars && typeof doc.vars === "object" ? (doc.vars as Record<string, unknown>) : {};
  const form = formRaw as Record<string, unknown>;
  const sections = Array.isArray(form.sections) ? (form.sections as RunFormSection[]) : [];
  if (sections.length === 0) {
    return null;
  }
  return {
    vars,
    form: {
      title: typeof form.title === "string" ? form.title : undefined,
      presets: Array.isArray(form.presets)
        ? (form.presets as RunForm["presets"]).filter(
            (preset) => preset && typeof preset.set === "object"
          )
        : [],
      summary:
        form.summary && typeof form.summary === "object"
          ? (form.summary as RunForm["summary"])
          : {},
      sections: sections.map((section) => ({
        ...section,
        fields: Array.isArray(section.fields) ? section.fields : [],
      })),
    },
  };
}

/** Every var the form sets: these are sent on start, whether changed or not. */
export function formVarNames(form: RunForm): string[] {
  const names = new Set<string>();
  for (const section of form.sections) {
    if (section.toggle) {
      names.add(section.toggle);
    }
    for (const field of section.fields) {
      if (field.type === "range") {
        for (const name of [field.relative, field.from, field.to, field.points]) {
          names.add(name);
        }
      } else {
        names.add(field.var);
      }
    }
  }
  return [...names];
}

/** The dialog's starting values: the sequence's own vars. */
export function initialFormValues(parsed: ParsedRunForm): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const name of formVarNames(parsed.form)) {
    out[name] = parsed.vars[name];
  }
  return out;
}

export function buildRunOverrides(
  form: RunForm,
  values: Record<string, unknown>
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const name of formVarNames(form)) {
    if (values[name] !== undefined) {
      out[name] = values[name];
    }
  }
  return out;
}

export function optionsOf(value: unknown): RunFormOption[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter((item) => item && typeof item === "object" && has(item, "value"))
    .map((item) => {
      const option = item as { value: unknown; label?: unknown };
      return {
        value: option.value,
        label: typeof option.label === "string" ? option.label : String(option.value),
      };
    });
}

/** Options from an `options_call` result. */
export function optionsFromCallResult(
  call: RunFormOptionsCall,
  result: unknown
): RunFormOption[] {
  const list =
    call.list && result && typeof result === "object"
      ? (result as Record<string, unknown>)[call.list]
      : result;
  if (!Array.isArray(list)) {
    return [];
  }
  return list.map((item) => {
    const record = item && typeof item === "object" ? (item as Record<string, unknown>) : null;
    const value = call.value && record ? record[call.value] : item;
    const label = call.label && record ? record[call.label] : value;
    return { value, label: String(label) };
  });
}

/** Catalog entries usable as targets (others are skipped, not fatal). */
export function catalogTargets(value: unknown): ScanTarget[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter(
    (item): item is ScanTarget =>
      Boolean(item) &&
      typeof item === "object" &&
      typeof (item as ScanTarget).id === "string" &&
      typeof (item as ScanTarget).kind === "string"
  );
}

/**
 * "Other" targets of a device without a catalog entry: each `set_X` with a
 * matching `get_X` whose parameters are a value, or a channel and a value.
 * No lock wait, raw units, restored through `get_X`.
 */
export function otherTargetsFor(
  deviceId: string,
  members: ReadonlyArray<CapabilityMember>
): ScanTarget[] {
  const methods = new Map(
    members.filter((member) => member.kind === "method").map((member) => [member.name, member])
  );
  const out: ScanTarget[] = [];
  for (const [name, member] of methods) {
    if (!name.startsWith("set_")) {
      continue;
    }
    const getter = `get_${name.slice(4)}`;
    if (!methods.has(getter)) {
      continue;
    }
    const params = (member.params ?? []).map((param) => param.name);
    let channelParam: string | null = null;
    let valueParam: string;
    if (params.length === 1) {
      valueParam = params[0];
    } else if (params.length === 2 && params.includes("channel")) {
      channelParam = "channel";
      valueParam = params.find((param) => param !== "channel") as string;
    } else {
      continue;
    }
    out.push({
      id: `other:${deviceId}:${name}`,
      kind: "generic",
      device: deviceId,
      device_label: deviceId,
      param_label: channelParam ? `${name.slice(4)} (per channel)` : name.slice(4),
      set_action: name,
      get_action: getter,
      value_param: valueParam,
      channel_param: channelParam,
      channel: channelParam ? 1 : null,
      lock: "none",
      settle_s: 1.0,
      unit: "",
      display_unit: "",
      display_scale: 1,
      offset_unit: "",
      offset_scale: 1,
      min: null,
      max: null,
      default_span: 1,
      current: null,
    });
  }
  return out.sort((a, b) => a.param_label.localeCompare(b.param_label));
}

/** Evenly spaced values, like the sequencer's `gen: linspace`. */
export function linspace(start: number, stop: number, num: number): number[] {
  const n = Math.max(1, Math.trunc(num));
  if (n === 1) {
    return [start];
  }
  return Array.from({ length: n }, (_, i) => start + ((stop - start) * i) / (n - 1));
}

/** The scan values (base units), or null while the origin is unknown. */
export function rangeValues(
  relative: boolean,
  from: number,
  to: number,
  points: number,
  origin: number | null
): number[] | null {
  if (relative && origin === null) {
    return null;
  }
  const base = relative ? (origin as number) : 0;
  return linspace(base + from, base + to, points);
}

/** Problems that should keep Start disabled. */
export function rangeProblems(
  target: ScanTarget | null,
  values: number[] | null
): string[] {
  const problems: string[] = [];
  if (!target) {
    return ["Choose a target"];
  }
  if (!values) {
    return problems;
  }
  if (values.some((value) => !Number.isFinite(value))) {
    problems.push("The range has a value that isn't a number");
    return problems;
  }
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  if (typeof target.min === "number" && lo < target.min) {
    problems.push(`The range goes below the minimum, ${target.min} ${target.unit}`);
  }
  if (typeof target.max === "number" && hi > target.max) {
    problems.push(`The range goes above the maximum, ${target.max} ${target.unit}`);
  }
  return problems;
}

export function totalShots(
  summary: RunForm["summary"],
  values: Record<string, unknown>
): { points: number; shots: number } | null {
  const read = (key: keyof RunForm["summary"]) => {
    const name = summary[key];
    const value = name ? Number(values[name]) : 1;
    return Number.isFinite(value) ? Math.max(0, Math.trunc(value)) : 0;
  };
  if (!summary.points) {
    return null;
  }
  const points = read("points");
  return { points, shots: points * read("passes") * read("bursts") * read("shots") };
}

/** Number shown with a sensible number of significant digits. */
export function formatNumber(value: number, digits = 9): string {
  if (!Number.isFinite(value)) {
    return "–";
  }
  return Number(value.toPrecision(digits)).toString();
}
