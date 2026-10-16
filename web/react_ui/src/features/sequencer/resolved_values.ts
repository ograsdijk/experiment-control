/**
 * Resolved values of `${...}` templates, for the value chips in the Steps
 * and YAML tabs. The values come from `sequencer.resolve_vars` (the backend
 * evaluates; nothing is evaluated here) and, while running, `status.env`.
 */

import { isScalar, parse, parseDocument, visit } from "yaml";
import type { SequencerStepOutlineNode } from "./types";

export type ExpressionResult =
  | { ok: true; value: unknown }
  | { ok: false; runtime: true; names: string[] }
  | { ok: false; runtime?: false; error: string };

export type ResolvedVars = {
  vars: Record<string, unknown>;
  /** Values without the run overrides, for the vars the overrides changed. */
  baseVars: Record<string, unknown>;
  /** The `${...}` text of each derived var. */
  formulas: Record<string, string>;
  derived: ReadonlySet<string>;
  overridden: ReadonlySet<string>;
  /** Vars that read names only set while running (`label: run_${i}`), with those names. */
  runtimeVars: Record<string, string[]>;
  errors: Record<string, string>;
  expressions: Record<string, ExpressionResult>;
  /** Why the latest request failed (the values may be out of date), or unset. */
  stale?: string;
};

export type ValueChipKind =
  | "vars"
  | "override"
  | "derived"
  | "runtime"
  | "live"
  | "unknown"
  | "error";

export type ValueChip = {
  kind: ValueChipKind;
  /** Var name, or the expression for an inline expression. */
  name: string;
  /** Short value text (empty for a runtime value with no live value). */
  value: string;
  /** Derived from other vars: shown with the ƒ marker. */
  formula: boolean;
  /** Changed by a run override (an overridden var or one derived from it). */
  overrideDot: boolean;
  /** Tooltip lines. */
  detail: string[];
  /** Values may be out of date (shown dimmed). */
  stale?: boolean;
};

/** Where a name that only exists while running is set. */
export type RuntimeOrigin = {
  /** Short chip label: "loop", "index", "assign", "save_as", "adaptive". */
  label: string;
  /** Tooltip line, e.g. "loop variable of for at L15". */
  description: string;
  line: number;
  /** What it runs over, with `${...}` still in it (resolved for display). */
  over: string | null;
};

export type ValueContext = {
  resolved: ResolvedVars | null;
  /** Runtime env of the running sequence (loop variables etc.), scalars only. */
  liveEnv: Record<string, unknown> | null;
  /** Overrides set in the sidebar that differ from the running ones. */
  nextRunOverrides: Record<string, unknown> | null;
  /** Steps that set runtime names (loop variables, assign, save_as). */
  runtimeOrigins?: Record<string, RuntimeOrigin>;
};

const TEMPLATE_RE = /\$\{([^}]+)\}/g;
/** Own keys only: a template like `${toString}` must not find Object.prototype. */
function has(rec: object | null | undefined, key: string): boolean {
  return rec != null && Object.prototype.hasOwnProperty.call(rec, key);
}

function own<T>(rec: Record<string, T> | null | undefined, key: string): T | undefined {
  return rec != null && has(rec, key) ? rec[key] : undefined;
}

/** A fresh global regexp for `${...}` (shared with the YAML editor). */
export function templateRegExp(): RegExp {
  return new RegExp(TEMPLATE_RE.source, "g");
}

const SIMPLE_REF_RE = /^\s*(?:vars\.)?([A-Za-z_]\w*)\s*$/;

export type TemplateSegment =
  | { kind: "text"; text: string }
  | { kind: "template"; text: string; inner: string };

export function splitTemplates(text: string): TemplateSegment[] {
  const out: TemplateSegment[] = [];
  let last = 0;
  for (const match of text.matchAll(TEMPLATE_RE)) {
    const start = match.index ?? 0;
    if (start > last) {
      out.push({ kind: "text", text: text.slice(last, start) });
    }
    out.push({ kind: "template", text: match[0], inner: match[1] });
    last = start + match[0].length;
  }
  if (last < text.length) {
    out.push({ kind: "text", text: text.slice(last) });
  }
  return out;
}

/** Every distinct `${...}` in the text, to send as `expressions`. */
export function extractTemplateExpressions(text: string): string[] {
  return Array.from(new Set(Array.from(text.matchAll(TEMPLATE_RE), (m) => m[0])));
}

/** The var a template refers to, for `${name}` and `${vars.name}`. */
export function simpleTemplateRef(inner: string): string | null {
  return SIMPLE_REF_RE.exec(inner)?.[1] ?? null;
}

function asRecord(raw: unknown): Record<string, unknown> | null {
  return raw && typeof raw === "object" && !Array.isArray(raw)
    ? (raw as Record<string, unknown>)
    : null;
}

function stringList(raw: unknown): string[] {
  return Array.isArray(raw) ? raw.filter((v): v is string => typeof v === "string") : [];
}

function normalizeExpression(raw: unknown): ExpressionResult | null {
  const rec = asRecord(raw);
  if (!rec) {
    return null;
  }
  if (rec.ok === true) {
    return { ok: true, value: rec.value };
  }
  if (rec.runtime === true) {
    return { ok: false, runtime: true, names: stringList(rec.names) };
  }
  return { ok: false, error: typeof rec.error === "string" ? rec.error : "Can't evaluate" };
}

export function normalizeResolvedVars(raw: unknown): ResolvedVars | null {
  const rec = asRecord(raw);
  if (!rec) {
    return null;
  }
  const expressions: Record<string, ExpressionResult> = {};
  for (const [key, value] of Object.entries(asRecord(rec.expressions) ?? {})) {
    const normalized = normalizeExpression(value);
    if (normalized) {
      expressions[key] = normalized;
    }
  }
  const strings = (value: unknown) =>
    Object.fromEntries(
      Object.entries(asRecord(value) ?? {}).filter(
        (entry): entry is [string, string] => typeof entry[1] === "string"
      )
    );
  return {
    vars: asRecord(rec.vars) ?? {},
    baseVars: asRecord(rec.base_vars) ?? {},
    formulas: strings(rec.formulas),
    derived: new Set(stringList(rec.derived)),
    overridden: new Set(stringList(rec.overridden)),
    runtimeVars: Object.fromEntries(
      Object.entries(asRecord(rec.runtime_vars) ?? {}).map(([key, value]) => [key, stringList(value)])
    ),
    errors: strings(rec.errors),
    expressions,
  };
}

/** Scalars of the runtime env; save_as responses and the like are left out. */
export function scalarEnv(raw: unknown): Record<string, unknown> | null {
  const rec = asRecord(raw);
  if (!rec) {
    return null;
  }
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(rec)) {
    if (
      typeof value === "number" ||
      typeof value === "boolean" ||
      (typeof value === "string" && value.length <= 80)
    ) {
      out[key] = value;
    }
  }
  return out;
}

const SIG_FIGS = 5;

function trimZeros(text: string): string {
  return text.includes(".") ? text.replace(/0+$/, "").replace(/\.$/, "") : text;
}

/** Engineering notation (exponent a multiple of 3), about 5 significant figures. */
export function formatNumber(value: number): string {
  if (!Number.isFinite(value)) {
    return String(value);
  }
  if (value === 0) {
    return "0";
  }
  const abs = Math.abs(value);
  if (Number.isInteger(value) && abs < 1e6) {
    return String(value);
  }
  // toPrecision(5) switches to its own exponent form from 1e5 on, and for
  // values that round up to it (99999.7 -> "1.0000e+5"): use ours then.
  if (abs >= 1e-3 && abs < 1e5) {
    const text = value.toPrecision(SIG_FIGS);
    if (!text.includes("e")) {
      return trimZeros(text);
    }
  }
  let exponent = Math.floor(Math.log10(abs) / 3) * 3;
  let mantissa = value / 10 ** exponent;
  let text = trimZeros(mantissa.toPrecision(SIG_FIGS));
  // Rounding can carry to 1000 (999999.7 -> "1000e3").
  if (Math.abs(Number(text)) >= 1000) {
    exponent += 3;
    mantissa = value / 10 ** exponent;
    text = trimZeros(mantissa.toPrecision(SIG_FIGS));
  }
  return `${text}e${exponent}`;
}

/** Full value for tooltips: numbers in full, others as JSON. */
export function formatFull(value: unknown): string {
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toLocaleString("en-US") : String(value);
  }
  if (typeof value === "string") {
    return JSON.stringify(value);
  }
  const json = JSON.stringify(value);
  return json === undefined ? String(value) : json;
}

function generatorSummary(gen: Record<string, unknown>): string | null {
  const [kind, config] = Object.entries(gen)[0] ?? [];
  if (!kind) {
    return null;
  }
  if (Array.isArray(config)) {
    return `${kind} ×${config.length}`;
  }
  const rec = asRecord(config);
  const num = rec?.num ?? rec?.n ?? rec?.count;
  return typeof num === "number" ? `${kind} ×${num}` : kind;
}

/** Short value text for a chip. */
export function formatShort(value: unknown): string {
  if (typeof value === "number") {
    return formatNumber(value);
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (value === null || value === undefined) {
    return "null";
  }
  if (typeof value === "string") {
    return value.length > 24 ? `"${value.slice(0, 21)}…"` : `"${value}"`;
  }
  if (Array.isArray(value)) {
    return `list[${value.length}]`;
  }
  const rec = asRecord(value);
  const gen = asRecord(rec?.gen);
  if (gen) {
    return generatorSummary(gen) ?? "gen";
  }
  return `{${Object.keys(rec ?? {}).length} keys}`;
}

const IDENT_RE = /\b(?:vars\.)?([A-Za-z_]\w*)\b/g;

/** The formula with each var replaced by its value: `1e9 - 0.5 * 1e6 * (30 - 1)`. */
export function substituteFormula(formula: string, vars: Record<string, unknown>): string {
  return formula.replace(TEMPLATE_RE, (_match, inner: string) =>
    inner.trim().replace(IDENT_RE, (whole, name: string) =>
      typeof vars[name] === "number" ? formatNumber(vars[name] as number) : whole
    )
  );
}

function formulaInner(formula: string): string {
  const parts = splitTemplates(formula);
  return parts.length === 1 && parts[0].kind === "template" ? parts[0].inner.trim() : formula;
}

/** Override lines for a var, including a sidebar value that waits for the next run. */
function overrideDetail(name: string, ctx: ValueContext, resolved: ResolvedVars): string[] {
  const lines: string[] = [];
  if (resolved.overridden.has(name)) {
    lines.push(`Run override: ${formatFull(resolved.vars[name])}`);
  }
  if (has(resolved.baseVars, name)) {
    lines.push(`Without overrides: ${formatFull(resolved.baseVars[name])}`);
  }
  const next = ctx.nextRunOverrides;
  if (next && has(next, name)) {
    lines.push(`Next run: ${formatFull(next[name])}`);
  }
  return lines;
}

function varChip(name: string, ctx: ValueContext, resolved: ResolvedVars): ValueChip {
  const value = resolved.vars[name];
  const error = own(resolved.errors, name);
  if (error) {
    return { kind: "error", name, value: "error", formula: false, overrideDot: false, detail: [error] };
  }
  const runtimeNames = own(resolved.runtimeVars, name);
  if (runtimeNames) {
    const single = runtimeNames.length === 1 ? findOrigin(runtimeNames[0], ctx) : undefined;
    const unknown = unknownNames(runtimeNames, ctx).length > 0;
    return {
      kind: unknown ? "unknown" : "runtime",
      name,
      value: unknown ? "unknown" : single?.label ?? "runtime",
      formula: true,
      overrideDot: false,
      detail: [
        `${name} = ${formatFull(resolved.vars[name])}`,
        ...originLines(runtimeNames, ctx),
        "Value known once the step runs.",
      ],
    };
  }
  const overridden = resolved.overridden.has(name);
  const derived = resolved.derived.has(name) && !overridden;
  const changed = has(resolved.baseVars, name);
  const detail = [`${name} = ${formatFull(value)}`];
  const formula = own(resolved.formulas, name);
  if (derived && formula) {
    detail.push(formulaInner(formula), `= ${formulaInner(substituteFormula(formula, resolved.vars))}`);
  } else if (!overridden) {
    detail.push("from vars");
  }
  detail.push(...overrideDetail(name, ctx, resolved));
  return {
    kind: overridden ? "override" : derived ? "derived" : "vars",
    name,
    value: formatShort(value),
    formula: derived,
    overrideDot: overridden || changed,
    detail,
  };
}

/** `linspace(start: ${a}, num: ${n})` with each template replaced by its value. */
export function resolveTemplatesInText(text: string, ctx: ValueContext): string {
  return splitTemplates(text)
    .map((segment) => {
      if (segment.kind === "text") {
        return segment.text;
      }
      const chip = describeTemplate(segment.text, segment.inner, { ...ctx, runtimeOrigins: undefined });
      return chip && chip.value && chip.kind !== "error" ? chip.value : segment.text;
    })
    .join("");
}

/** The step that sets a runtime name; `metric_*` entries match adaptive `<metric>_<stat>`. */
function findOrigin(name: string, ctx: ValueContext): RuntimeOrigin | undefined {
  const origins = ctx.runtimeOrigins;
  if (!origins) {
    return undefined;
  }
  const exact = own(origins, name);
  if (exact) {
    return exact;
  }
  const prefix = Object.keys(origins).find(
    (key) => key.endsWith("_*") && name.startsWith(key.slice(0, -1))
  );
  return prefix ? origins[prefix] : undefined;
}

/** Names no step sets (only known once the steps are: `runtimeOrigins` given). */
function unknownNames(names: string[], ctx: ValueContext): string[] {
  return ctx.runtimeOrigins ? names.filter((name) => !findOrigin(name, ctx)) : [];
}

function originLines(names: string[], ctx: ValueContext): string[] {
  const lines: string[] = [];
  for (const name of names) {
    const origin = findOrigin(name, ctx);
    if (!origin) {
      lines.push(
        ctx.runtimeOrigins
          ? `${name}: not a var, and no step in this sequence sets it (typo?)`
          : `${name}: set while running`
      );
      continue;
    }
    lines.push(`${name}: ${origin.description}`);
    if (origin.over) {
      lines.push(`  over ${resolveTemplatesInText(origin.over, ctx)}`);
    }
  }
  return lines;
}

function runtimeChip(name: string, names: string[], ctx: ValueContext): ValueChip {
  const live = ctx.liveEnv;
  const origins = originLines(names, ctx);
  if (live && names.length === 1 && names[0] === name && has(live, name)) {
    return {
      kind: "live",
      name,
      value: formatShort(live[name]),
      formula: false,
      overrideDot: false,
      detail: [`${name} = ${formatFull(live[name])} (live)`, ...origins],
    };
  }
  if (unknownNames(names, ctx).length > 0) {
    return { kind: "unknown", name, value: "unknown", formula: false, overrideDot: false, detail: origins };
  }
  const single = names.length === 1 ? findOrigin(names[0], ctx) : undefined;
  return {
    kind: "runtime",
    name,
    value: single?.label ?? "runtime",
    formula: false,
    overrideDot: false,
    detail: [...origins, "Value known once the step runs."],
  };
}

/** A call step's `save_as`, `extract` and `assign` targets, read from its YAML. */
function callTargets(snippet: string): string[] {
  let parsed: unknown;
  try {
    parsed = parse(snippet);
  } catch {
    return [];
  }
  const step = asRecord(Array.isArray(parsed) ? parsed[0] : parsed);
  if (!step) {
    return [];
  }
  const out: string[] = [];
  const saveAs = typeof step.save_as === "string" ? step.save_as : null;
  if (saveAs) {
    out.push(saveAs);
  } else if (step.extract != null) {
    out.push("value");
  }
  out.push(...Object.keys(asRecord(step.assign) ?? {}));
  return out;
}

/** Names set while running, from the steps that set them (first one wins). */
export function buildRuntimeOrigins(
  outline: ReadonlyArray<SequencerStepOutlineNode>
): Record<string, RuntimeOrigin> {
  const out: Record<string, RuntimeOrigin> = {};
  const add = (name: string | null | undefined, origin: RuntimeOrigin) => {
    const key = name?.trim();
    if (key && /^[A-Za-z_]\w*(?:_\*)?$/.test(key) && !has(out, key)) {
      out[key] = origin;
    }
  };
  const visit = (node: SequencerStepOutlineNode) => {
    const at = `${node.kind} at L${node.line}`;
    if (node.forDetail) {
      const detail = node.forDetail;
      const over =
        detail.sourceMode === "direct"
          ? detail.directValue
          : node.summary?.split(" over ").slice(1).join(" over ") || detail.generatorKind;
      for (const entry of detail.bind) {
        const isIndex = entry.name === "index";
        add(entry.value, {
          label: isIndex ? "index" : "loop",
          description: isIndex ? `loop index of ${at}` : `loop variable of ${at}`,
          line: node.line,
          over: isIndex ? null : over,
        });
      }
    }
    for (const entry of node.assignDetail?.entries ?? []) {
      add(entry.name, { label: "assign", description: `set by ${at}`, line: node.line, over: null });
    }
    for (const group of node.adaptiveDetail?.space ?? []) {
      add(group.name, {
        label: "adaptive",
        description: `parameter of ${at}, chosen per trial`,
        line: node.line,
        over: null,
      });
    }
    if (node.adaptiveDetail) {
      const trial = { label: "adaptive", line: node.line, over: null };
      add("score", { ...trial, description: `trial score of ${at}` });
      for (const metric of node.adaptiveDetail.metrics) {
        add(`${metric.name}_*`, { ...trial, description: `metric statistic of ${at}` });
      }
    }
    if (node.kind === "wait_until") {
      const sample = { label: "sample", line: node.line, over: null };
      add("sample", { ...sample, description: `latest sample of ${at}` });
      add("samples", { ...sample, description: `samples of ${at}` });
    }
    if (node.kind === "call") {
      for (const target of callTargets(node.snippet)) {
        add(target, {
          label: "save_as",
          description: `set from the response of ${at}`,
          line: node.line,
          over: null,
        });
      }
    }
    node.children.forEach(visit);
  };
  outline.forEach(visit);
  return out;
}

/**
 * The chip for one `${...}` template, or null when there is nothing to show
 * (values not resolved yet).
 */
export function describeTemplate(text: string, inner: string, ctx: ValueContext): ValueChip | null {
  return markStale(describeTemplateValue(text, inner, ctx), ctx);
}

function markStale(chip: ValueChip | null, ctx: ValueContext): ValueChip | null {
  if (!chip || !ctx.resolved?.stale || chip.kind === "live") {
    return chip;
  }
  return {
    ...chip,
    stale: true,
    detail: [...chip.detail, `May be out of date: ${ctx.resolved.stale}.`],
  };
}

function describeTemplateValue(text: string, inner: string, ctx: ValueContext): ValueChip | null {
  const resolved = ctx.resolved;
  if (!resolved) {
    return null;
  }
  const ref = simpleTemplateRef(inner);
  if (ref && has(resolved.vars, ref)) {
    return varChip(ref, ctx, resolved);
  }
  const name = ref ?? inner.trim();
  const result = resolved.expressions[text];
  if (!result) {
    return null;
  }
  if (result.ok) {
    return {
      kind: "derived",
      name,
      value: formatShort(result.value),
      formula: true,
      overrideDot: false,
      detail: [`${name} = ${formatFull(result.value)}`],
    };
  }
  if (result.runtime) {
    return runtimeChip(name, result.names, ctx);
  }
  return { kind: "error", name, value: "error", formula: false, overrideDot: false, detail: [result.error] };
}

/** Chip for a var's own line in the `vars` block (only when it adds something). */
export function describeVarDefinition(name: string, ctx: ValueContext): ValueChip | null {
  const resolved = ctx.resolved;
  if (!resolved || !has(resolved.vars, name)) {
    return null;
  }
  const show =
    resolved.derived.has(name) ||
    resolved.overridden.has(name) ||
    has(resolved.baseVars, name) ||
    has(resolved.errors, name) ||
    has(resolved.runtimeVars, name) ||
    has(ctx.nextRunOverrides, name);
  return show ? markStale(varChip(name, ctx, resolved), ctx) : null;
}

/** Runtime overrides apply to the running sequence; sidebar rows wait for the next start. */
export function overridesForDisplay(
  running: boolean,
  activeOverrides: Record<string, unknown> | null,
  sidebarOverrides: Record<string, unknown>
): { overrides: Record<string, unknown>; nextRun: Record<string, unknown> | null } {
  if (!running) {
    return { overrides: sidebarOverrides, nextRun: null };
  }
  const active = activeOverrides ?? {};
  const nextRun: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(sidebarOverrides)) {
    if (JSON.stringify(active[name]) !== JSON.stringify(value)) {
      nextRun[name] = value;
    }
  }
  return {
    overrides: active,
    nextRun: Object.keys(nextRun).length > 0 ? nextRun : null,
  };
}

/**
 * Lines [start, end) of the top-level `vars:` block. It ends at the next line
 * that starts in the first column (another key, `---`, `...`), except
 * comments. Shared by the request and the YAML editor, so both agree.
 */
export function varsBlockRange(lines: ReadonlyArray<string>): { start: number; end: number } | null {
  const start = lines.findIndex((line) => /^vars\s*:/.test(line));
  if (start < 0) {
    return null;
  }
  let end = start + 1;
  while (end < lines.length && !(/^\S/.test(lines[end]) && !lines[end].startsWith("#"))) {
    end += 1;
  }
  return { start, end };
}

/** A YAML alias (`*name`): it needs its anchor, which may be outside the block. */
const YAML_ALIAS_RE = /(?:^|[\s[{,:-])\*[^\s,[\]{}]+/m;

/**
 * The part of the YAML text resolve_vars needs: the top-level `vars:` block
 * (parsing just that keeps the request cheap on a long sequence), or all of
 * it when the block uses an alias.
 */
export function extractVarsBlock(text: string): string {
  const lines = text.split(/\r?\n/);
  const range = varsBlockRange(lines);
  if (!range) {
    return "vars: {}\n";
  }
  const block = `${lines.slice(range.start, range.end).join("\n")}\n`;
  return YAML_ALIAS_RE.test(block) ? text : block;
}

/**
 * Template text as written in the YAML, mapped to the text the runtime sees
 * after YAML decoding, where they differ: escaped quotes in a double-quoted
 * string, or a template folded over two lines.
 */
export function decodedTemplates(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  let doc;
  try {
    doc = parseDocument(text);
  } catch {
    return out;
  }
  visit(doc, {
    Scalar(_key, node) {
      if (!isScalar(node) || typeof node.value !== "string" || !node.range) {
        return;
      }
      const raw = text.slice(node.range[0], node.range[1]).match(TEMPLATE_RE) ?? [];
      const decoded = node.value.match(TEMPLATE_RE) ?? [];
      if (raw.length !== decoded.length) {
        return;
      }
      raw.forEach((rawTemplate, index) => {
        if (rawTemplate !== decoded[index] && !has(out, rawTemplate)) {
          out[rawTemplate] = decoded[index];
        }
      });
    },
  });
  return out;
}
