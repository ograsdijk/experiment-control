import { callProcess } from "../../api";
import type { ConditionEvaluationTrace, WatchdogStatus } from "../../types";

export type WatchdogArmStatus = {
  condition?: unknown;
  disarm_condition?: unknown;
  disarm_on_trigger?: boolean;
};

export type WatchdogConfirmationProgress = {
  count: number;
  evidence?: unknown[];
};

export type WatchdogConfirmationStatus = {
  consecutive_samples: number;
  sample_aliases: string[];
  any?: unknown[] | null;
  progress: Record<string, WatchdogConfirmationProgress>;
};

export type DetailedWatchdogRule = WatchdogStatus["rules"][number] & {
  arm?: WatchdogArmStatus | null;
  armed?: boolean;
  confirmation?: WatchdogConfirmationStatus | null;
  active_trip_id?: string | null;
};

export function watchdogRuleDetails(
  rule: WatchdogStatus["rules"][number]
): DetailedWatchdogRule {
  return rule as DetailedWatchdogRule;
}

export function hasActiveWatchdogTrip(
  rule: WatchdogStatus["rules"][number]
): boolean {
  const details = watchdogRuleDetails(rule);
  // Latched rules intentionally retain active_trip_id after recovery so the
  // recovery/latch-clear events remain correlated to the original trip. Treat
  // the id as currently active only while the rule still has a live alarm or
  // fail-safe unknown condition.
  return Boolean(details.active_trip_id) && (Boolean(rule.alarm) || Boolean(rule.unknown));
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  if (typeof value !== "number" && typeof value !== "string") {
    return fallback;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

const CONDITION_KINDS = new Set([
  "always",
  "comparison",
  "group",
  "not",
  "value",
  "error",
]);

function normalizeConditionEvaluation(raw: unknown): ConditionEvaluationTrace | null {
  const obj = asRecord(raw);
  if (!obj) {
    return null;
  }
  const kind = asString(obj.kind, "value") as ConditionEvaluationTrace["kind"];
  if (!CONDITION_KINDS.has(kind)) {
    return null;
  }
  const operatorRaw = asString(obj.operator, "");
  const operator = operatorRaw
    ? (operatorRaw as NonNullable<ConditionEvaluationTrace["operator"]>)
    : undefined;
  const children = Array.isArray(obj.children)
    ? obj.children
        .map((child) => normalizeConditionEvaluation(child))
        .filter((child): child is ConditionEvaluationTrace => child !== null)
    : undefined;
  return {
    kind,
    operator,
    result:
      obj.result == null
        ? null
        : typeof obj.result === "boolean"
          ? obj.result
          : Boolean(obj.result),
    resolved: obj.resolved,
    left: obj.left,
    right: obj.right,
    children,
    error: asString(obj.error, "") || null,
  };
}

function normalizeArm(raw: unknown): WatchdogArmStatus | null {
  const obj = asRecord(raw);
  if (!obj) {
    return null;
  }
  return {
    condition: obj.condition,
    disarm_condition: obj.disarm_condition,
    disarm_on_trigger: asBoolean(obj.disarm_on_trigger, false),
  };
}

function normalizeConfirmation(raw: unknown): WatchdogConfirmationStatus | null {
  const obj = asRecord(raw);
  if (!obj) {
    return null;
  }
  const progressRaw = asRecord(obj.progress) ?? {};
  const progress: Record<string, WatchdogConfirmationProgress> = {};
  for (const [alias, value] of Object.entries(progressRaw)) {
    const item = asRecord(value);
    if (!item) {
      continue;
    }
    progress[alias] = {
      count: Math.max(0, Math.trunc(asNumber(item.count, 0))),
      evidence: Array.isArray(item.evidence) ? item.evidence : [],
    };
  }
  return {
    consecutive_samples: Math.max(1, Math.trunc(asNumber(obj.consecutive_samples, 1))),
    sample_aliases: Array.isArray(obj.sample_aliases)
      ? obj.sample_aliases.map((item) => String(item ?? "")).filter(Boolean)
      : [],
    any: Array.isArray(obj.any) ? obj.any : null,
    progress,
  };
}

export function normalizeWatchdogStatusDetailed(raw: unknown): WatchdogStatus | null {
  const obj = asRecord(raw);
  if (!obj) {
    return null;
  }
  const rulesRaw = Array.isArray(obj.rules) ? obj.rules : [];
  const rules = rulesRaw
    .map((ruleRaw) => {
      const ruleObj = asRecord(ruleRaw);
      if (!ruleObj) {
        return null;
      }
      const telemetryRaw = Array.isArray(ruleObj.telemetry) ? ruleObj.telemetry : [];
      const telemetry = telemetryRaw
        .map((itemRaw) => {
          const item = asRecord(itemRaw);
          if (!item) {
            return null;
          }
          return {
            as: asString(item.as),
            device_id: asString(item.device_id),
            signal: asString(item.signal),
            max_age_s: asNumber(item.max_age_s, 0),
            required: typeof item.required === "boolean" ? item.required : true,
          };
        })
        .filter((item): item is NonNullable<typeof item> => item !== null);
      const actionsRaw = Array.isArray(ruleObj.actions) ? ruleObj.actions : [];
      const actions = actionsRaw
        .map((actionRaw) => {
          const actionObj = asRecord(actionRaw);
          if (!actionObj) {
            return null;
          }
          const params = asRecord(actionObj.params) ?? {};
          const timeoutRaw = asNumber(actionObj.timeout_s, Number.NaN);
          const retriesRaw = asNumber(actionObj.retries, Number.NaN);
          return {
            device_id:
              actionObj.device_id != null ? asString(actionObj.device_id) : undefined,
            process_id:
              actionObj.process_id != null ? asString(actionObj.process_id) : undefined,
            action: asString(actionObj.action),
            params,
            timeout_s: Number.isFinite(timeoutRaw) ? timeoutRaw : null,
            retries: Number.isFinite(retriesRaw) ? Math.trunc(retriesRaw) : 0,
          };
        })
        .filter((item): item is NonNullable<typeof item> => item !== null);
      const numberOrNull = (value: unknown): number | null => {
        const parsed = asNumber(value, Number.NaN);
        return Number.isFinite(parsed) ? parsed : null;
      };
      const ageOrNull = (value: unknown): number | null => {
        const parsed = numberOrNull(value);
        return parsed == null ? null : Math.max(0, parsed);
      };
      const snapshot = asRecord(ruleObj.snapshot);
      const rule = {
        name: asString(ruleObj.name),
        severity: asString(ruleObj.severity, "info"),
        message: asString(ruleObj.message) || null,
        condition: Object.prototype.hasOwnProperty.call(ruleObj, "condition")
          ? ruleObj.condition
          : null,
        telemetry,
        actions,
        stable_for_s: numberOrNull(ruleObj.stable_for_s),
        cooldown_s: numberOrNull(ruleObj.cooldown_s),
        latch: asBoolean(ruleObj.latch, false),
        on_unknown: asString(ruleObj.on_unknown) || null,
        latched: asBoolean(ruleObj.latched, false),
        alarm: Object.prototype.hasOwnProperty.call(ruleObj, "alarm")
          ? asBoolean(ruleObj.alarm, false)
          : null,
        unknown: Object.prototype.hasOwnProperty.call(ruleObj, "unknown")
          ? asBoolean(ruleObj.unknown, false)
          : null,
        snapshot,
        condition_evaluation: normalizeConditionEvaluation(ruleObj.condition_evaluation),
        last_evaluated_mono: numberOrNull(ruleObj.last_evaluated_mono),
        last_evaluated_age_s: ageOrNull(ruleObj.last_evaluated_age_s),
        stable_since_mono: numberOrNull(ruleObj.stable_since_mono),
        stable_since_age_s: ageOrNull(ruleObj.stable_since_age_s),
        last_trigger_mono: numberOrNull(ruleObj.last_trigger_mono),
        last_trigger_age_s: ageOrNull(ruleObj.last_trigger_age_s),
        arm: normalizeArm(ruleObj.arm),
        armed: asBoolean(ruleObj.armed, false),
        confirmation: normalizeConfirmation(ruleObj.confirmation),
        active_trip_id: asString(ruleObj.active_trip_id, "") || null,
      } as DetailedWatchdogRule;
      return rule;
    })
    .filter((rule): rule is DetailedWatchdogRule => rule !== null);
  return {
    watchdog_id: asString(obj.watchdog_id),
    enabled: asBoolean(obj.enabled, true),
    rules,
  };
}

export async function fetchDetailedWatchdogStatus(
  processId: string
): Promise<WatchdogStatus[]> {
  const resp = await callProcess(processId, "watchdog.status", {});
  if (!resp.ok || !resp.result || typeof resp.result !== "object") {
    return [];
  }
  const watchdogsRaw = (resp.result as { watchdogs?: unknown }).watchdogs;
  if (!Array.isArray(watchdogsRaw)) {
    return [];
  }
  return watchdogsRaw
    .map((watchdog) => normalizeWatchdogStatusDetailed(watchdog))
    .filter((watchdog): watchdog is WatchdogStatus => watchdog !== null);
}
