import type {
  FollowerRuleStatus,
  InterlockInterceptorStatus,
  InterlockRuleStatus,
  ProcessStatus,
  WatchdogStatus,
} from "../../types";
import { interlockRuleKey, isProcessRpcStateAvailable } from "../runtime/helpers";
import {
  hasActiveWatchdogTrip,
  hasWatchdogActionFailure,
  isWatchdogRuleConfirming,
} from "../watchdogs/watchdogStatusApi";
import {
  isRemoteProcess,
  stateName,
  type ResourceAdapter,
  type ResourceHealth,
} from "../devices/health";

// ---------------------------------------------------------------------------
// Interlocks
// ---------------------------------------------------------------------------

export type InterlockRuleRow = {
  /** Unique across processes/rulesets; also the toggle busy key. */
  key: string;
  processId: string;
  process: ProcessStatus;
  processActive: boolean;
  /** Interceptor id for ruleset rules, "follower" for frequency-follower guards. */
  scopeId: string;
  rulesetLabel: string;
  rulesetEnabled: boolean;
  ruleId: string;
  name: string;
  enabled: boolean;
  /** Process reachable, ruleset enabled and rule enabled: it is protecting. */
  active: boolean;
  matchDeviceId: string;
  matchAction: string;
  inputCount: number;
  kindLabel: string;
  interlock: InterlockRuleStatus | null;
  interceptor: InterlockInterceptorStatus | null;
  follower: FollowerRuleStatus | null;
};

export function followerGuardKind(rule: FollowerRuleStatus): {
  hasRangeGuard: boolean;
  hasStepGuard: boolean;
  maxStepHz: number | null;
} {
  const hasRangeGuard =
    Number.isFinite(rule.min_freq_hz) && Number.isFinite(rule.max_freq_hz);
  const maxStepHz =
    typeof rule.max_step_hz === "number" && Number.isFinite(rule.max_step_hz)
      ? rule.max_step_hz
      : null;
  return { hasRangeGuard, hasStepGuard: maxStepHz !== null, maxStepHz };
}

function followerKindLabel(rule: FollowerRuleStatus): string {
  const { hasRangeGuard, hasStepGuard } = followerGuardKind(rule);
  if (hasRangeGuard && hasStepGuard) {
    return "range + step guard";
  }
  if (hasRangeGuard) {
    return "range guard";
  }
  if (hasStepGuard) {
    return "step guard";
  }
  return "trigger guard";
}

/** Flatten every interlock ruleset rule and follower guard into one list. */
export function buildInterlockRuleRows(
  processes: ReadonlyArray<ProcessStatus>,
  interlockStatusByProcessId: Record<string, InterlockInterceptorStatus[]>,
  followerRulesByProcessId: Record<string, FollowerRuleStatus[]>
): InterlockRuleRow[] {
  const rows: InterlockRuleRow[] = [];
  for (const process of processes) {
    const processId = process.process_id;
    const processActive = isProcessRpcStateAvailable(process);
    (followerRulesByProcessId[processId] ?? []).forEach((rule, idx) => {
      const ruleId = String(rule.rule_id ?? "").trim() || `follower_${idx}`;
      const enabled = Boolean(rule.enabled);
      rows.push({
        key: interlockRuleKey(processId, "follower", ruleId),
        processId,
        process,
        processActive,
        scopeId: "follower",
        rulesetLabel: "frequency follower",
        rulesetEnabled: true,
        ruleId,
        name: String(rule.name ?? "").trim() || ruleId,
        enabled,
        active: processActive && enabled,
        matchDeviceId: String(rule.device_id ?? "").trim(),
        matchAction: String(rule.trigger_action ?? "").trim(),
        inputCount: rule.current_freq_signal ? 1 : 0,
        kindLabel: followerKindLabel(rule),
        interlock: null,
        interceptor: null,
        follower: rule,
      });
    });
    (interlockStatusByProcessId[processId] ?? []).forEach((interceptor, interceptorIdx) => {
      const interceptorId =
        String(interceptor.interceptor_id ?? "").trim() || `interceptor_${interceptorIdx}`;
      const rulesetEnabled = Boolean(interceptor.enabled);
      (Array.isArray(interceptor.rules) ? interceptor.rules : []).forEach((rule, ruleIdx) => {
        const ruleId = String(rule.rule_id ?? "").trim() || `r${ruleIdx}`;
        const enabled = Boolean(rule.enabled);
        rows.push({
          key: interlockRuleKey(processId, interceptorId, ruleId),
          processId,
          process,
          processActive,
          scopeId: interceptorId,
          rulesetLabel: interceptorId,
          rulesetEnabled,
          ruleId,
          name: String(rule.name ?? "").trim() || ruleId,
          enabled,
          active: processActive && rulesetEnabled && enabled,
          matchDeviceId: String(rule.match?.device_id ?? "").trim(),
          matchAction: String(rule.match?.action ?? "").trim(),
          inputCount: Array.isArray(rule.telemetry) ? rule.telemetry.length : 0,
          kindLabel: rule.has_allow_transform ? "transform" : "condition",
          interlock: rule,
          interceptor,
          follower: null,
        });
      });
    });
  }
  return rows;
}

/**
 * Active rules are healthy and disabled ones neutral. A rule that is meant to
 * protect (rule + ruleset enabled) but whose process is not reachable is
 * failed: nothing is enforcing it.
 */
export function interlockRuleHealth(row: InterlockRuleRow): ResourceHealth {
  if (row.active) {
    return "healthy";
  }
  if (row.enabled && row.rulesetEnabled && !row.processActive) {
    return "failed";
  }
  return "neutral";
}

export const interlockRuleAdapter: ResourceAdapter<InterlockRuleRow> = {
  id: (row) => row.name,
  health: interlockRuleHealth,
  remoteLink: (row) =>
    isRemoteProcess(row.process) ? stateName(row.process.liveness) || "UNKNOWN" : null,
  searchText: (row) =>
    [
      row.name,
      row.ruleId,
      row.rulesetLabel,
      row.processId,
      `${row.matchDeviceId}.${row.matchAction}`,
      row.kindLabel,
      row.enabled ? "enabled" : "disabled",
      interlockRuleHealth(row),
      row.interlock?.on_block?.code ?? "",
      row.interlock?.on_block?.message ?? "",
      ...(row.interlock?.telemetry ?? []).map((t) => `${t.device_id}.${t.signal}`),
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase(),
};

// ---------------------------------------------------------------------------
// Watchdogs
// ---------------------------------------------------------------------------

export type WatchdogRule = WatchdogStatus["rules"][number];

export type WatchdogRuleRow = {
  key: string;
  processId: string;
  process: ProcessStatus;
  processActive: boolean;
  watchdogId: string;
  watchdogEnabled: boolean;
  ruleName: string;
  rule: WatchdogRule;
};

export function buildWatchdogRuleRows(
  processes: ReadonlyArray<ProcessStatus>,
  watchdogStatusByProcessId: Record<string, WatchdogStatus[]>
): WatchdogRuleRow[] {
  const rows: WatchdogRuleRow[] = [];
  for (const process of processes) {
    const processId = process.process_id;
    const processActive = isProcessRpcStateAvailable(process);
    (watchdogStatusByProcessId[processId] ?? []).forEach((watchdog, watchdogIdx) => {
      const watchdogId =
        String(watchdog.watchdog_id ?? "").trim() || `watchdog_${watchdogIdx}`;
      (watchdog.rules ?? []).forEach((rule, ruleIdx) => {
        const ruleName = String(rule.name ?? "").trim() || `rule_${ruleIdx}`;
        rows.push({
          key: `${processId}:${watchdogId}:${ruleName}`,
          processId,
          process,
          processActive,
          watchdogId,
          watchdogEnabled: Boolean(watchdog.enabled),
          ruleName,
          rule,
        });
      });
    });
  }
  return rows;
}

/**
 * Triggered or a failed action chain is failed; latched or confirming is
 * degraded (needs attention); unavailable inputs are stale; never evaluated
 * is a transition. A disabled ruleset is neutral, and an enabled one whose
 * process is unreachable is failed.
 */
export function watchdogRuleHealth(row: WatchdogRuleRow): ResourceHealth {
  if (!row.watchdogEnabled) {
    return "neutral";
  }
  if (!row.processActive) {
    return "failed";
  }
  const rule = row.rule;
  if (hasActiveWatchdogTrip(rule) || hasWatchdogActionFailure(rule)) {
    return "failed";
  }
  if (rule.latched || isWatchdogRuleConfirming(rule)) {
    return "degraded";
  }
  if (rule.unknown) {
    return "stale";
  }
  if (rule.last_evaluated_mono == null) {
    return "transition";
  }
  return "healthy";
}

export function watchdogRuleNeedsAttention(row: WatchdogRuleRow): boolean {
  const health = watchdogRuleHealth(row);
  return health === "failed" || health === "degraded";
}

export const watchdogRuleAdapter: ResourceAdapter<WatchdogRuleRow> = {
  id: (row) => row.ruleName,
  health: watchdogRuleHealth,
  remoteLink: (row) =>
    isRemoteProcess(row.process) ? stateName(row.process.liveness) || "UNKNOWN" : null,
  searchText: (row) =>
    [
      row.ruleName,
      row.watchdogId,
      row.processId,
      row.rule.severity,
      row.rule.message ?? "",
      watchdogRuleHealth(row),
      ...(row.rule.telemetry ?? []).map((t) => `${t.device_id}.${t.signal}`),
      ...(row.rule.actions ?? []).map(
        (a) => `${a.device_id ?? a.process_id ?? ""}.${a.action}`
      ),
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase(),
};
