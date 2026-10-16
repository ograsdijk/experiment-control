/**
 * Resolved values of `${...}` templates, for the value chips in the Steps
 * and YAML tabs. The values come from `sequencer.resolve_vars` (the backend
 * evaluates; nothing is evaluated here) and, while running, `status.env`.
 */

import { asRecord, has, own, stringList } from "./records";
import { findOrigin, type RuntimeOrigins } from "./runtime_origins";
import { simpleTemplateRef, splitTemplates, templateInner } from "./templates";
import { formatFull, formatShort, substituteFormula } from "./value_format";

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
  /** Why the latest request failed, so the values may be out of date; unset when current. */
  staleReason?: string;
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
  /** Short value text, or a label such as "loop" for a runtime name. */
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

export type ValueContext = {
  resolved: ResolvedVars | null;
  /** Runtime env of the running sequence (loop variables etc.), scalars only. */
  liveEnv: Record<string, unknown> | null;
  /** Overrides set in the sidebar that differ from the running ones. */
  nextRunOverrides: Record<string, unknown> | null;
  /** Steps that set runtime names; without them no name is flagged unknown. */
  runtimeOrigins?: RuntimeOrigins;
};

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

function stringRecord(raw: unknown): Record<string, string> {
  return Object.fromEntries(
    Object.entries(asRecord(raw) ?? {}).filter(
      (entry): entry is [string, string] => typeof entry[1] === "string"
    )
  );
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
  return {
    vars: asRecord(rec.vars) ?? {},
    baseVars: asRecord(rec.base_vars) ?? {},
    formulas: stringRecord(rec.formulas),
    derived: new Set(stringList(rec.derived)),
    overridden: new Set(stringList(rec.overridden)),
    runtimeVars: Object.fromEntries(
      Object.entries(asRecord(rec.runtime_vars) ?? {}).map(([key, value]) => [key, stringList(value)])
    ),
    errors: stringRecord(rec.errors),
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

function chip(
  kind: ValueChipKind,
  name: string,
  value: string,
  detail: string[],
  extra: Partial<Pick<ValueChip, "formula" | "overrideDot">> = {}
): ValueChip {
  return { kind, name, value, detail, formula: false, overrideDot: false, ...extra };
}

/** `linspace(start: ${a}, num: ${n})` with each template replaced by its value. */
export function resolveTemplatesInText(text: string, ctx: ValueContext): string {
  return splitTemplates(text)
    .map((segment) => {
      if (segment.kind === "text") {
        return segment.text;
      }
      const found = describeTemplate(segment.text, segment.inner, { ...ctx, runtimeOrigins: undefined });
      return found && found.value && found.kind !== "error" ? found.value : segment.text;
    })
    .join("");
}

/** Where each runtime name comes from, one tooltip line each (plus what a loop runs over). */
function originLines(names: string[], ctx: ValueContext): string[] {
  const lines: string[] = [];
  for (const name of names) {
    const origin = findOrigin(name, ctx.runtimeOrigins);
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

/**
 * A template (or a var built from one, `formula`) that needs names set while
 * running: its live value if running, else where they come from.
 */
function runtimeChip(
  name: string,
  names: string[],
  ctx: ValueContext,
  { formula = false, firstLine }: { formula?: boolean; firstLine?: string } = {}
): ValueChip {
  const origins = originLines(names, ctx);
  const live = ctx.liveEnv;
  if (live && names.length === 1 && names[0] === name && has(live, name)) {
    return chip("live", name, formatShort(live[name]), [
      `${name} = ${formatFull(live[name])} (live)`,
      ...origins,
    ]);
  }
  const head = firstLine ? [firstLine] : [];
  const unknown = ctx.runtimeOrigins && names.some((n) => !findOrigin(n, ctx.runtimeOrigins));
  if (unknown) {
    return chip("unknown", name, "unknown", [...head, ...origins], { formula });
  }
  const single = names.length === 1 ? findOrigin(names[0], ctx.runtimeOrigins) : undefined;
  return chip("runtime", name, single?.label ?? "runtime", [...head, ...origins, "Value known once the step runs."], {
    formula,
  });
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
    return chip("error", name, "error", [error]);
  }
  const runtimeNames = own(resolved.runtimeVars, name);
  if (runtimeNames) {
    return runtimeChip(name, runtimeNames, ctx, {
      formula: true,
      firstLine: `${name} = ${formatFull(value)}`,
    });
  }
  const overridden = resolved.overridden.has(name);
  const derived = resolved.derived.has(name) && !overridden;
  const detail = [`${name} = ${formatFull(value)}`];
  const formula = own(resolved.formulas, name);
  if (derived && formula) {
    detail.push(templateInner(formula), `= ${templateInner(substituteFormula(formula, resolved.vars))}`);
  } else if (!overridden) {
    detail.push("from vars");
  }
  detail.push(...overrideDetail(name, ctx, resolved));
  return chip(overridden ? "override" : derived ? "derived" : "vars", name, formatShort(value), detail, {
    formula: derived,
    overrideDot: overridden || has(resolved.baseVars, name),
  });
}

function markStale(found: ValueChip | null, ctx: ValueContext): ValueChip | null {
  const reason = ctx.resolved?.staleReason;
  if (!found || !reason || found.kind === "live") {
    return found;
  }
  return { ...found, stale: true, detail: [...found.detail, `May be out of date: ${reason}.`] };
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
  const result = own(resolved.expressions, text);
  if (!result) {
    return null;
  }
  if (result.ok) {
    return chip("derived", name, formatShort(result.value), [`${name} = ${formatFull(result.value)}`], {
      formula: true,
    });
  }
  if (result.runtime) {
    return runtimeChip(name, result.names, ctx);
  }
  return chip("error", name, "error", [result.error]);
}

/**
 * The chip for one `${...}` template, or null when there is nothing to show
 * (values not resolved yet).
 */
export function describeTemplate(text: string, inner: string, ctx: ValueContext): ValueChip | null {
  return markStale(describeTemplateValue(text, inner, ctx), ctx);
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

/** Running overrides apply to the running sequence; sidebar rows wait for the next start. */
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
