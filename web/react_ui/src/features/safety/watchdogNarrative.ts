import type { WatchdogStatus } from "../../types";
import {
  hasActiveWatchdogTrip,
  isWatchdogRuleConfirming,
  watchdogRuleDetails,
  type WatchdogActionChainAction,
} from "../watchdogs/watchdogStatusApi";
import type { WatchdogRuleRow } from "./safetyRows";

type WatchdogRule = WatchdogStatus["rules"][number];

/** Live readings: 4 significant digits, scientific for very small/large, integers as-is. */
export function formatReading(value: number): string {
  if (!Number.isFinite(value)) {
    return String(value);
  }
  if (Number.isInteger(value)) {
    return String(value);
  }
  if (value !== 0 && (Math.abs(value) < 1e-3 || Math.abs(value) >= 1e4)) {
    return value.toExponential(3);
  }
  return String(Number(value.toPrecision(4)));
}

function formatAny(value: unknown): string {
  if (typeof value === "number") {
    return formatReading(value);
  }
  if (value == null) {
    return "n/a";
  }
  return typeof value === "string" || typeof value === "boolean"
    ? String(value)
    : JSON.stringify(value);
}

/** Relative time used throughout the watchdog card ("just now", "4.2 s ago", "9.7 min ago"). */
export function formatAgo(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) {
    return "n/a";
  }
  if (value < 0.05) {
    return "just now";
  }
  if (value < 10) {
    return `${value.toFixed(1)} s ago`;
  }
  return value < 60 ? `${Math.round(value)} s ago` : `${(value / 60).toFixed(1)} min ago`;
}

/** Input age, e.g. "0.1 s old". */
export function formatInputAge(value: number): string {
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} s old`;
}

// ---------------------------------------------------------------------------
// Inputs
// ---------------------------------------------------------------------------

export type WatchdogInput = {
  alias: string;
  label: string;
  value: unknown;
  ageS: number | null;
  maxAgeS: number | null;
  quality: string | null;
  ok: boolean | null;
};

/** One row per telemetry input, merging the rule config with the live snapshot. */
export function watchdogInputs(rule: WatchdogRule): WatchdogInput[] {
  const snapshot = (rule.snapshot ?? {}) as Record<string, Record<string, unknown> | undefined>;
  const aliases = new Map<string, WatchdogInput>();
  for (const binding of rule.telemetry ?? []) {
    const live = snapshot[binding.as] ?? {};
    aliases.set(binding.as, {
      alias: binding.as,
      label: `${binding.device_id}.${binding.signal}`,
      value: live.value ?? null,
      ageS: typeof live.age_s === "number" ? live.age_s : null,
      maxAgeS: binding.max_age_s ?? null,
      quality: typeof live.quality === "string" ? live.quality : null,
      ok: typeof live.ok === "boolean" ? live.ok : null,
    });
  }
  // Snapshot-only aliases (e.g. derived inputs not listed in telemetry).
  for (const [alias, live] of Object.entries(snapshot)) {
    if (aliases.has(alias) || !live || typeof live !== "object") {
      continue;
    }
    const device = typeof live.device === "string" ? live.device : "";
    const signal = typeof live.signal === "string" ? live.signal : "";
    aliases.set(alias, {
      alias,
      label: device && signal ? `${device}.${signal}` : alias,
      value: live.value ?? null,
      ageS: typeof live.age_s === "number" ? live.age_s : null,
      maxAgeS: typeof live.max_age_s === "number" ? live.max_age_s : null,
      quality: typeof live.quality === "string" ? live.quality : null,
      ok: typeof live.ok === "boolean" ? live.ok : null,
    });
  }
  return [...aliases.values()];
}

export function inputUnavailable(input: WatchdogInput): boolean {
  return input.ok === false || input.value === null || input.value === undefined;
}

// ---------------------------------------------------------------------------
// Condition summary
// ---------------------------------------------------------------------------

const OP_SYMBOL: Record<string, string> = {
  gt: ">",
  ge: "≥",
  lt: "<",
  le: "≤",
  eq: "=",
  ne: "≠",
  abs_lt: "|x| <",
};

/**
 * For the common single-comparison rule (`gt: ["${v.value}", 3.15]`) return
 * the input label, its live value and the threshold; null for anything more
 * complex, which the condition view shows in full instead.
 */
export function simpleComparison(rule: WatchdogRule): {
  label: string;
  current: unknown;
  symbol: string;
  threshold: unknown;
} | null {
  const condition = rule.condition;
  if (!condition || typeof condition !== "object" || Array.isArray(condition)) {
    return null;
  }
  const entries = Object.entries(condition as Record<string, unknown>);
  if (entries.length !== 1) {
    return null;
  }
  const [op, args] = entries[0];
  if (!(op in OP_SYMBOL) || !Array.isArray(args) || args.length !== 2) {
    return null;
  }
  const [left, right] = args;
  const match = typeof left === "string" ? /^\$\{([A-Za-z_][A-Za-z0-9_]*)\.value\}$/.exec(left) : null;
  if (!match || typeof right === "string") {
    return null;
  }
  const input = watchdogInputs(rule).find((item) => item.alias === match[1]);
  return {
    label: input?.label ?? match[1],
    current: input?.value ?? null,
    symbol: OP_SYMBOL[op],
    threshold: right,
  };
}

// ---------------------------------------------------------------------------
// Action outcome
// ---------------------------------------------------------------------------

export type BlockingInterlock = {
  processId: string;
  interceptorId: string;
  rule: string;
};

export type ActionOutcome = {
  target: string;
  ok: boolean;
  attempts: number;
  message: string | null;
  blockedBy: BlockingInterlock | null;
};

function commandTarget(command: Record<string, unknown>): string {
  const action = typeof command.action === "string" ? command.action : "action";
  if (typeof command.device_id === "string" && command.device_id) {
    return `${command.device_id}.${action}`;
  }
  if (typeof command.process_id === "string" && command.process_id) {
    return `${command.process_id}:${action}`;
  }
  return action;
}

/** Unwrap `{ok:false, error:{kind, message, ...}}` (or a bare string) from a failed action. */
export function parseActionError(error: unknown): {
  message: string | null;
  blockedBy: BlockingInterlock | null;
} {
  if (error == null) {
    return { message: null, blockedBy: null };
  }
  if (typeof error === "string") {
    return { message: error, blockedBy: null };
  }
  if (typeof error !== "object") {
    return { message: String(error), blockedBy: null };
  }
  const outer = error as Record<string, unknown>;
  const inner =
    outer.error && typeof outer.error === "object"
      ? (outer.error as Record<string, unknown>)
      : outer;
  const message =
    typeof inner.message === "string"
      ? inner.message
      : typeof outer.message === "string"
        ? outer.message
        : typeof inner.code === "string"
          ? inner.code
          : null;
  const blockedBy =
    inner.kind === "command_interceptor" && typeof inner.rule === "string"
      ? {
          processId: typeof inner.process_id === "string" ? inner.process_id : "",
          interceptorId: typeof inner.interceptor_id === "string" ? inner.interceptor_id : "",
          rule: inner.rule,
        }
      : null;
  return { message, blockedBy };
}

export function lastActionOutcomes(rule: WatchdogRule): ActionOutcome[] {
  const chain = watchdogRuleDetails(rule).last_action_chain;
  if (!chain) {
    return [];
  }
  return chain.actions.map((action: WatchdogActionChainAction) => {
    const { message, blockedBy } = action.ok
      ? { message: null, blockedBy: null }
      : parseActionError(action.error);
    return {
      target: commandTarget(action.command),
      ok: action.ok,
      attempts: action.attempts,
      message,
      blockedBy,
    };
  });
}

// ---------------------------------------------------------------------------
// Status line
// ---------------------------------------------------------------------------

export type StatusTone = "red" | "orange" | "yellow" | "teal" | "gray";

export type WatchdogStatusLine = {
  tone: StatusTone;
  /** Main sentence: what the rule is doing right now. */
  text: string;
  /** Last action result, when it matters (a trip or latch happened). */
  actionText: string | null;
  blockedBy: BlockingInterlock | null;
};

function failedActionSummary(rule: WatchdogRule): {
  text: string | null;
  blockedBy: BlockingInterlock | null;
} {
  const chain = watchdogRuleDetails(rule).last_action_chain;
  if (!chain) {
    return { text: null, blockedBy: null };
  }
  if (chain.state === "running") {
    return { text: "Action running…", blockedBy: null };
  }
  const outcomes = lastActionOutcomes(rule);
  const failed = outcomes.find((outcome) => !outcome.ok);
  if (!failed) {
    return {
      text: `Action succeeded (${chain.succeeded_actions}/${chain.action_count}).`,
      blockedBy: null,
    };
  }
  if (failed.blockedBy) {
    return {
      text: `Action ${failed.target} failed: blocked by interlock rule "${failed.blockedBy.rule}".`,
      blockedBy: failed.blockedBy,
    };
  }
  return {
    text: `Action ${failed.target} failed${failed.message ? `: ${failed.message}` : ""}.`,
    blockedBy: null,
  };
}

/** One plain-language sentence for the rule card, built only from reported status. */
export function watchdogStatusLine(row: WatchdogRuleRow): WatchdogStatusLine {
  const rule = row.rule;
  const plain = (tone: StatusTone, text: string): WatchdogStatusLine => ({
    tone,
    text,
    actionText: null,
    blockedBy: null,
  });
  if (!row.watchdogEnabled) {
    return plain("gray", `Ruleset ${row.watchdogId} is disabled: this rule is not evaluated.`);
  }
  if (!row.processActive) {
    return plain("red", `${row.processId} is ${row.process.state}: this rule is not evaluated.`);
  }
  const comparison = simpleComparison(rule);
  const reading = comparison
    ? `${comparison.label} ${formatAny(comparison.current)} ${comparison.symbol} ${formatAny(
        comparison.threshold
      )}`
    : null;

  if (hasActiveWatchdogTrip(rule)) {
    const action = failedActionSummary(rule);
    const when = rule.last_trigger_age_s != null ? ` ${formatAgo(rule.last_trigger_age_s)}` : "";
    return {
      tone: "red",
      text: `Tripped${when}${reading ? `: ${reading}` : ""}.`,
      actionText: action.text,
      blockedBy: action.blockedBy,
    };
  }
  if (rule.latched) {
    const action = failedActionSummary(rule);
    return {
      tone: "orange",
      text: "Recovered but latched: clear the latch to re-arm.",
      actionText: action.text,
      blockedBy: action.blockedBy,
    };
  }
  if (isWatchdogRuleConfirming(rule)) {
    return plain(
      "orange",
      `Condition met, confirming before tripping${reading ? `: ${reading}` : ""}.`
    );
  }
  if (rule.unknown) {
    const missing = watchdogInputs(rule)
      .filter(inputUnavailable)
      .map((input) => input.label);
    const what =
      missing.length > 0
        ? `${missing.join(", ")} ${missing.length === 1 ? "has" : "have"} no fresh data`
        : "inputs are unavailable";
    const consequence =
      String(rule.on_unknown ?? "ignore").toLowerCase() === "trigger"
        ? "On unknown = trigger, so missing data trips this rule."
        : "On unknown = ignore, so this rule cannot trip until the data returns.";
    return plain("yellow", `Not evaluating: ${what}. ${consequence}`);
  }
  if (rule.last_evaluated_mono == null) {
    return plain("gray", "Waiting for the first evaluation.");
  }
  const details = watchdogRuleDetails(rule);
  if (details.arm != null && !details.armed) {
    return plain("gray", "Disarmed: the arming condition is not met, so this rule cannot trip.");
  }
  if (comparison) {
    return plain(
      "teal",
      `Safe: ${comparison.label} ${formatAny(comparison.current)}, trips when ${
        comparison.symbol
      } ${formatAny(comparison.threshold)}.`
    );
  }
  return plain("teal", "Safe: the trip condition is not met.");
}
