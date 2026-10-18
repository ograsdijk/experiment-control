import { formatFreqHz } from "../runtime/helpers";
import type { InterlockRuleRow } from "./safetyRows";
import { followerGuardKind } from "./safetyRows";
import { formatReading, type StatusTone } from "./watchdogNarrative";

/**
 * "set_temperature commands (any device)", "neon_flow commands", "tec.set
 * commands"; singular form for "every …" ("every neon_flow command").
 */
export function commandTargetNoun(deviceId: string, action: string, plural = true): string {
  const anyDevice = !deviceId || deviceId === "*";
  const anyAction = !action || action === "*";
  const noun = plural ? "commands" : "command";
  if (anyDevice && anyAction) {
    return plural ? "all commands" : "command";
  }
  if (anyDevice) {
    return `${action} ${noun} (any device)`;
  }
  if (anyAction) {
    return `${deviceId} ${noun}`;
  }
  return `${deviceId}.${action} ${noun}`;
}

export type InterlockStatusLine = {
  tone: StatusTone;
  text: string;
  /** What an operator sees when this rule blocks, if configured. */
  blockText: string | null;
  /** Extra behavioural notes (transforms, fail-closed inputs). */
  notes: string[];
};

/** Plain-language description of a follower guard's limits. */
export function followerGuardSentence(
  row: InterlockRuleRow,
  currentValue: number | null = null
): string | null {
  const follower = row.follower;
  if (!follower) {
    return null;
  }
  const { hasRangeGuard, hasStepGuard, maxStepHz } = followerGuardKind(follower);
  const target = `${follower.device_id}.${follower.trigger_action}${
    follower.trigger_param ? ` (${follower.trigger_param})` : ""
  }`;
  const parts: string[] = [];
  if (hasRangeGuard) {
    parts.push(
      `outside ${formatFreqHz(follower.min_freq_hz)} – ${formatFreqHz(follower.max_freq_hz)}`
    );
  }
  if (hasStepGuard && maxStepHz !== null) {
    const source = `${follower.device_id}.${follower.current_freq_signal || "frequency"}`;
    const now = currentValue !== null ? `, now ${formatFreqHz(currentValue)}` : "";
    parts.push(`steps larger than ${formatFreqHz(maxStepHz)} from the current ${source}${now}`);
  }
  if (parts.length === 0) {
    return `Watches ${target} and applies its follow-up effects.`;
  }
  return `Rejects ${target} ${parts.join(", or ")}.`;
}

export function interlockStatusLine(row: InterlockRuleRow): InterlockStatusLine {
  const target = commandTargetNoun(row.matchDeviceId, row.matchAction);
  const rule = row.interlock;
  const blockMessage = String(rule?.on_block?.message ?? "").trim();
  const notes: string[] = [];
  if (rule?.has_allow_transform) {
    notes.push("Allowed commands may have their params rewritten by this rule.");
  }
  if ((rule?.telemetry ?? []).some((binding) => binding.required)) {
    notes.push("If a required input has no fresh data, the command is blocked.");
  }
  const base = { blockText: blockMessage || null, notes };
  if (!row.rulesetEnabled) {
    return {
      ...base,
      tone: "gray",
      text: `Ruleset ${row.rulesetLabel} is disabled: ${target} pass without this check.`,
    };
  }
  if (!row.enabled) {
    return { ...base, tone: "gray", text: `Disabled: ${target} pass without this check.` };
  }
  if (!row.processActive) {
    return {
      ...base,
      tone: "red",
      text: `Not enforced: ${row.processId} is ${row.process.state}, so ${target} are not checked by this rule.`,
    };
  }
  return {
    ...base,
    tone: "teal",
    text: `Active: checks every ${commandTargetNoun(row.matchDeviceId, row.matchAction, false)}.`,
  };
}

/** `${params.temperature}` → "temperature", collected from anywhere in a condition. */
export function conditionParamNames(condition: unknown): string[] {
  const names = new Set<string>();
  const visit = (value: unknown) => {
    if (typeof value === "string") {
      for (const match of value.matchAll(/\$\{\s*params\.([A-Za-z_][A-Za-z0-9_]*)/g)) {
        names.add(match[1]);
      }
    } else if (Array.isArray(value)) {
      value.forEach(visit);
    } else if (value && typeof value === "object") {
      Object.values(value).forEach(visit);
    }
  };
  visit(condition);
  return [...names];
}

export type CheckVerdict =
  | { kind: "allowed" }
  | { kind: "transformed"; rule: string | null; params: Record<string, unknown> | null }
  | { kind: "blocked"; rule: string | null; interceptorId: string | null; message: string | null; code: string | null }
  | { kind: "error"; message: string };

/** Interpret a `command_interceptor.check` reply. */
export function parseCheckReply(result: unknown): CheckVerdict {
  if (!result || typeof result !== "object") {
    return { kind: "error", message: "No reply from the interlock process." };
  }
  const reply = result as Record<string, unknown>;
  if (reply.ok === false) {
    const error = reply.error as Record<string, unknown> | undefined;
    return {
      kind: "error",
      message: String(error?.message ?? error?.code ?? "Check failed."),
    };
  }
  if (reply.allow === false) {
    const error = (reply.error ?? {}) as Record<string, unknown>;
    return {
      kind: "blocked",
      rule: typeof reply.rule === "string" ? reply.rule : null,
      interceptorId: typeof reply.interceptor_id === "string" ? reply.interceptor_id : null,
      message: typeof error.message === "string" ? error.message : null,
      code: typeof error.code === "string" ? error.code : null,
    };
  }
  if (reply.note === "transformed") {
    const command = reply.command as Record<string, unknown> | undefined;
    return {
      kind: "transformed",
      rule: typeof reply.rule === "string" ? reply.rule : null,
      params:
        command && typeof command.params === "object"
          ? (command.params as Record<string, unknown>)
          : null,
    };
  }
  return { kind: "allowed" };
}

/** Coerce a form string to the JSON value a rule condition would compare. */
export function coerceCheckParam(raw: string): unknown {
  const text = raw.trim();
  if (text === "") {
    return null;
  }
  if (text === "true" || text === "false") {
    return text === "true";
  }
  const number = Number(text);
  if (Number.isFinite(number)) {
    return number;
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export function formatParams(params: Record<string, unknown> | null): string {
  if (!params || Object.keys(params).length === 0) {
    return "no params";
  }
  return Object.entries(params)
    .map(([key, value]) => `${key}=${typeof value === "number" ? formatReading(value) : JSON.stringify(value)}`)
    .join(", ");
}
