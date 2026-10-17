/** How resolved values are written in chips and tooltips. */

import { asRecord } from "./records";
import { templateRegExp } from "./templates";

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
  let text = trimZeros((value / 10 ** exponent).toPrecision(SIG_FIGS));
  // Rounding can carry to 1000 (999999.7 -> "1000e3").
  if (Math.abs(Number(text)) >= 1000) {
    exponent += 3;
    text = trimZeros((value / 10 ** exponent).toPrecision(SIG_FIGS));
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
    // Bare, like the YAML; the tooltip shows it quoted. Empty stays visible.
    return value === "" ? '""' : value.length > 24 ? `${value.slice(0, 23)}…` : value;
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
  return formula.replace(templateRegExp(), (_match, inner: string) =>
    inner.trim().replace(IDENT_RE, (whole, name: string) =>
      typeof vars[name] === "number" ? formatNumber(vars[name] as number) : whole
    )
  );
}
