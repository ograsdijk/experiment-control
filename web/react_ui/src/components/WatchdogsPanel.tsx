import { Badge, Button, Card, Group, Stack, Switch, Text } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import { isProcessRpcStateAvailable, processStateColor } from "../features/runtime/helpers";
import {
  hasActiveWatchdogTrip,
  hasWatchdogActionFailure,
  isWatchdogRuleConfirming,
  watchdogRuleDetails,
  type DetailedWatchdogRule,
  type WatchdogActionChainStatus,
} from "../features/watchdogs/watchdogStatusApi";
import type {
  ConditionEvaluationTrace,
  ProcessStatus,
  WatchdogStatus,
} from "../types";

type Props = {
  processes: ReadonlyArray<ProcessStatus>;
  watchdogStatusByProcessId: Record<string, WatchdogStatus[]>;
  watchdogLoadingByProcessId: Record<string, boolean>;
  watchdogErrorByProcessId: Record<string, string>;
  watchdogBusyByKey: Record<string, boolean>;
  onRefreshProcess: (processId: string) => Promise<unknown> | void;
  onToggleWatchdog: (
    processId: string,
    watchdogId: string,
    enabled: boolean
  ) => Promise<unknown> | void;
  onClearRuleLatch: (
    processId: string,
    watchdogId: string,
    ruleName: string
  ) => Promise<unknown> | void;
};

const BEAMLINE_TURBO_RULES = new Set([
  "rc_pressure_turbos_off",
  "eql_pressure_turbos_off",
  "det_pressure_turbos_off",
]);

const BEAMLINE_TURBO_STATE_ALIASES = ["rc_on", "eql_on", "det_on", "spb_on"];

function isBeamlineTurboRule(rule: WatchdogStatus["rules"][number]): boolean {
  return BEAMLINE_TURBO_RULES.has(String(rule.name ?? ""));
}

function snapshotEntry(
  rule: WatchdogStatus["rules"][number],
  alias: string
): Record<string, unknown> | null {
  const entry = rule.snapshot?.[alias];
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
    return null;
  }
  return entry as Record<string, unknown>;
}

export function hasIncompleteBeamlineTurboState(
  rule: WatchdogStatus["rules"][number]
): boolean {
  if (
    !isBeamlineTurboRule(rule) ||
    watchdogRuleDetails(rule).armed ||
    rule.unknown ||
    rule.last_evaluated_mono == null
  ) {
    return false;
  }
  return BEAMLINE_TURBO_STATE_ALIASES.some(
    (alias) => snapshotEntry(rule, alias)?.ok !== true
  );
}

function formatDuration(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "n/a";
  }
  return `${value.toFixed(2)} s`;
}

function formatAge(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "n/a";
  }
  if (value < 0.05) {
    return "just now";
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} s ago`;
}

function formatValue(value: unknown): string {
  if (typeof value === "number") {
    if (value !== 0 && (Math.abs(value) < 1e-3 || Math.abs(value) >= 1e4)) {
      return value.toExponential(3);
    }
    return String(value);
  }
  if (value == null) {
    return "n/a";
  }
  if (typeof value === "boolean" || typeof value === "string") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function snapshotValue(rule: WatchdogStatus["rules"][number], alias: string): unknown {
  return snapshotEntry(rule, alias)?.value ?? null;
}

export function watchdogRuleLiveState(rule: WatchdogStatus["rules"][number]): {
  label: string;
  color: string;
} {
  const details = watchdogRuleDetails(rule);
  if (hasActiveWatchdogTrip(rule)) {
    return {
      label: rule.unknown ? "TRIGGERED · INPUT UNAVAILABLE" : "TRIGGERED",
      color: "red",
    };
  }
  if (rule.latched) {
    return { label: "RECOVERED · LATCHED", color: "orange" };
  }
  if (rule.unknown) {
    return { label: "UNKNOWN", color: "yellow" };
  }
  if (rule.last_evaluated_mono == null) {
    return { label: "PENDING", color: "gray" };
  }
  if (details.arm != null && !details.armed) {
    return hasIncompleteBeamlineTurboState(rule)
      ? { label: "DISARMED · TURBO STATE INCOMPLETE", color: "yellow" }
      : { label: "DISARMED", color: "gray" };
  }
  if (isWatchdogRuleConfirming(rule)) {
    return details.arm != null
      ? { label: "ARMED · CONFIRMING", color: "orange" }
      : { label: "CONFIRMING", color: "orange" };
  }
  if (details.arm != null) {
    return { label: "ARMED · SAFE", color: "teal" };
  }
  return { label: "SAFE", color: "teal" };
}

export function confirmationProgress(rule: WatchdogStatus["rules"][number]): {
  count: number;
  target: number;
} | null {
  const confirmation = watchdogRuleDetails(rule).confirmation;
  if (!confirmation) {
    return null;
  }
  const counts = Object.values(confirmation.progress ?? {}).map((item) => item.count);
  return {
    count: counts.length > 0 ? Math.max(...counts) : 0,
    target: confirmation.consecutive_samples,
  };
}

function actionCommandTarget(command: Record<string, unknown>): string {
  const action = typeof command.action === "string" ? command.action : "action";
  if (typeof command.device_id === "string" && command.device_id) {
    return `${command.device_id}.${action}`;
  }
  if (typeof command.process_id === "string" && command.process_id) {
    return `${command.process_id}:${action}`;
  }
  return action;
}

export function actionChainPresentation(
  rule: WatchdogStatus["rules"][number]
): { label: string; color: string; failedTargets: string[] } | null {
  const chain: WatchdogActionChainStatus | null | undefined =
    watchdogRuleDetails(rule).last_action_chain;
  if (!chain) {
    return null;
  }
  const noun = isBeamlineTurboRule(rule) ? "SHUTDOWN" : "ACTION";
  if (chain.state === "running") {
    return { label: `${noun} RUNNING`, color: "blue", failedTargets: [] };
  }
  const failedTargets = chain.actions
    .filter((item) => !item.ok)
    .map((item) => actionCommandTarget(item.command));
  if (chain.state === "error") {
    return { label: `LAST ${noun} ERROR`, color: "red", failedTargets };
  }
  if (chain.success) {
    return {
      label: `LAST ${noun} ${chain.succeeded_actions}/${chain.action_count} OK`,
      color: "teal",
      failedTargets: [],
    };
  }
  return {
    label: `LAST ${noun} PARTIAL · ${chain.succeeded_actions}/${chain.action_count}`,
    color: "red",
    failedTargets,
  };
}


export function summarizeWatchdogRules(watchdog: WatchdogStatus): {
  label: string;
  color: string;
} {
  if (!watchdog.enabled) {
    return { label: "Disabled", color: "gray" };
  }
  let latched = 0;
  let unknown = 0;
  let triggered = 0;
  let confirming = 0;
  let actionFailures = 0;
  let pending = 0;
  for (const rule of watchdog.rules) {
    if (rule.latched) {
      latched += 1;
    }
    if (hasActiveWatchdogTrip(rule)) {
      triggered += 1;
    } else if (rule.unknown) {
      unknown += 1;
    } else if (rule.last_evaluated_mono == null) {
      pending += 1;
    } else if (isWatchdogRuleConfirming(rule)) {
      confirming += 1;
    }
    if (hasWatchdogActionFailure(rule)) {
      actionFailures += 1;
    }
  }
  if (triggered > 0) {
    return { label: `${triggered} triggered`, color: "red" };
  }
  if (latched > 0) {
    return { label: `${latched} latched`, color: "orange" };
  }
  if (unknown > 0) {
    return { label: `Protection degraded · ${unknown} unavailable`, color: "yellow" };
  }
  if (confirming > 0) {
    return { label: `${confirming} confirming`, color: "orange" };
  }
  if (actionFailures > 0) {
    return {
      label: `Last action incomplete · ${actionFailures}`,
      color: "orange",
    };
  }
  if (pending > 0) {
    return { label: `${pending} pending`, color: "gray" };
  }
  return { label: "All safe", color: "teal" };
}

export function beamlineTurboProtectionSummary(
  rules: ReadonlyArray<WatchdogStatus["rules"][number]>,
  enabled = true
): { label: string; color: string } | null {
  const turboRules = rules.filter(isBeamlineTurboRule);
  if (turboRules.length === 0) {
    return null;
  }
  if (!enabled) {
    return { label: "Beamline protection disabled", color: "gray" };
  }
  const triggered = turboRules.filter(hasActiveWatchdogTrip).length;
  const unknown = turboRules.filter(
    (rule) => !hasActiveWatchdogTrip(rule) && Boolean(rule.unknown)
  ).length;
  const incompleteArming = turboRules.filter(hasIncompleteBeamlineTurboState).length;
  const confirming = turboRules.filter(isWatchdogRuleConfirming).length;
  const actionFailures = turboRules.filter(hasWatchdogActionFailure).length;
  const armed = turboRules.filter(
    (rule) => !rule.unknown && Boolean(watchdogRuleDetails(rule).armed)
  ).length;
  if (triggered > 0) {
    return { label: "Beamline protection TRIGGERED", color: "red" };
  }
  if (unknown > 0) {
    return {
      label: `Beamline protection degraded · ${armed}/${turboRules.length} sensors armed`,
      color: "yellow",
    };
  }
  if (confirming > 0) {
    return {
      label: `Beamline protection confirming · ${confirming} sensor${confirming === 1 ? "" : "s"}`,
      color: "orange",
    };
  }
  if (incompleteArming > 0) {
    return {
      label: `Beamline arming degraded · ${armed}/${turboRules.length} sensors armed`,
      color: "yellow",
    };
  }
  if (actionFailures > 0) {
    return {
      label: `Beamline last shutdown incomplete · ${actionFailures}`,
      color: "orange",
    };
  }
  if (armed === turboRules.length) {
    return {
      label: `Beamline protection armed · ${armed}/${turboRules.length}`,
      color: "teal",
    };
  }
  return {
    label: `Beamline protection arming · ${armed}/${turboRules.length}`,
    color: "gray",
  };
}

function evaluationBadge(trace: ConditionEvaluationTrace | null | undefined) {
  if (!trace || trace.result == null) {
    return (
      <Badge size="xs" variant="light" color="yellow">
        UNAVAILABLE
      </Badge>
    );
  }
  return (
    <Badge size="xs" variant="light" color={trace.result ? "teal" : "gray"}>
      {trace.result ? "MET" : "CLEAR"}
    </Badge>
  );
}

function ConditionTrace({
  trace,
  depth = 0,
}: {
  trace: ConditionEvaluationTrace | null | undefined;
  depth?: number;
}) {
  if (!trace) {
    return <Text size="xs" c="dimmed">No evaluation trace.</Text>;
  }
  const label =
    trace.kind === "group"
      ? String(trace.operator ?? "group").toUpperCase()
      : trace.kind === "comparison"
        ? `${formatValue(trace.left)} ${trace.operator ?? "?"} ${formatValue(trace.right)}`
        : trace.kind;
  return (
    <Stack gap={2} ml={depth > 0 ? "sm" : 0}>
      <Group gap="xs" wrap="wrap">
        <Text
          size="xs"
          style={{
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
          }}
        >
          {label}
        </Text>
        {evaluationBadge(trace)}
      </Group>
      {trace.children?.map((child, index) => (
        <ConditionTrace key={`${depth}:${index}`} trace={child} depth={depth + 1} />
      ))}
    </Stack>
  );
}

function formatActionSummary(
  action: NonNullable<WatchdogStatus["rules"][number]["actions"]>[number]
): string {
  const params = action.params ?? {};
  const entries = Object.entries(params);
  const paramLabel =
    entries.length > 0
      ? entries.map(([key, value]) => `${key}=${formatValue(value)}`).join(", ")
      : "no params";
  const timeoutLabel =
    typeof action.timeout_s === "number" && Number.isFinite(action.timeout_s)
      ? ` | timeout ${action.timeout_s}s`
      : "";
  const retriesLabel =
    typeof action.retries === "number" && Number.isFinite(action.retries)
      ? ` | retries ${Math.max(0, Math.trunc(action.retries))}`
      : "";
  const target =
    action.process_id != null ? action.action : `${action.device_id}.${action.action}`;
  return `${target}(${paramLabel})${timeoutLabel}${retriesLabel}`;
}

function operationalAction(rule: WatchdogStatus["rules"][number]): string | null {
  const actions = rule.actions ?? [];
  const turboStops = new Set(
    actions
      .filter((action) => action.action === "stop" && action.device_id)
      .map((action) => action.device_id)
  );
  if (
    ["hipace_rc", "hipace_eql", "hipace_det", "hipace_spb"].every((id) =>
      turboStops.has(id)
    )
  ) {
    return "Action: stop all beamline turbos";
  }
  if (actions.length === 1) {
    return `Action: ${formatActionSummary(actions[0])}`;
  }
  return actions.length > 0 ? `Actions: ${actions.length}` : null;
}

function RuleCard({
  processId,
  watchdogId,
  watchdogEnabled,
  rule,
  ruleIdx,
  loading,
  watchdogBusyByKey,
  onClearRuleLatch,
}: {
  processId: string;
  watchdogId: string;
  watchdogEnabled: boolean;
  rule: WatchdogStatus["rules"][number];
  ruleIdx: number;
  loading: boolean;
  watchdogBusyByKey: Record<string, boolean>;
  onClearRuleLatch: Props["onClearRuleLatch"];
}) {
  const ruleName = String(rule.name ?? "").trim() || `rule_${ruleIdx}`;
  const clearBusyKey = `${processId}:${watchdogId}:${ruleName}:clear`;
  const clearBusy = Boolean(watchdogBusyByKey[clearBusyKey]);
  const latched = Boolean(rule.latched);
  const details: DetailedWatchdogRule = watchdogRuleDetails(rule);
  const ruleState = watchdogEnabled
    ? watchdogRuleLiveState(rule)
    : { label: "DISABLED", color: "gray" };
  const confirmation = confirmationProgress(rule);
  const action = operationalAction(rule);
  const pressure = isBeamlineTurboRule(rule) ? snapshotValue(rule, "p") : null;
  const turboStateIncomplete = hasIncompleteBeamlineTurboState(rule);
  const actionChain = actionChainPresentation(rule);

  return (
    <Card p={6} radius="sm" style={{ border: "1px solid var(--card-border)" }}>
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Stack gap={3} style={{ minWidth: 0, flex: 1 }}>
          <Group gap="xs" wrap="wrap">
            <Text size="xs" fw={600}>{ruleName}</Text>
            <Badge variant="light" color={ruleState.color}>{ruleState.label}</Badge>
            {actionChain && (
              <Badge variant="light" color={actionChain.color}>{actionChain.label}</Badge>
            )}
            {latched && (
              <Badge variant="light" color="orange">Latched · acknowledge</Badge>
            )}
          </Group>

          <Group gap="md" wrap="wrap">
            {isBeamlineTurboRule(rule) && (
              <Text size="xs">
                Pressure: <b>{formatValue(pressure)} Torr</b>
              </Text>
            )}
            {confirmation && (
              <Text size="xs">
                High-pressure confirmation: <b>{confirmation.count}/{confirmation.target}</b>
              </Text>
            )}
            <Text size="xs" c="dimmed">evaluated {formatAge(rule.last_evaluated_age_s)}</Text>
          </Group>

          {turboStateIncomplete && (
            <Text size="xs" c="yellow">
              One or more turbo-state inputs are unavailable; this rule will not arm until at least one running turbo is positively observed. An already-armed rule is not affected.
            </Text>
          )}
          {actionChain && actionChain.failedTargets.length > 0 && (
            <Text size="xs" c="red">
              Failed target{actionChain.failedTargets.length === 1 ? "" : "s"}: {actionChain.failedTargets.join(", ")}
            </Text>
          )}
          {action && <Text size="xs" fw={500}>{action}</Text>}
          {rule.message && <Text size="xs" c="dimmed">{rule.message}</Text>}

          <details>
            <summary style={{ cursor: "pointer", fontSize: "var(--mantine-font-size-xs)" }}>
              Details
            </summary>
            <Stack gap={4} mt={4}>
              {details.arm != null && (
                <>
                  <Text size="xs" c="dimmed">Arming condition</Text>
                  <Text
                    size="xs"
                    style={{
                      whiteSpace: "pre-wrap",
                      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                    }}
                  >
                    {JSON.stringify(details.arm.condition, null, 2)}
                  </Text>
                </>
              )}
              <Text size="xs" c="dimmed">Trip condition evaluation</Text>
              <ConditionTrace trace={rule.condition_evaluation} />
              <Text size="xs" c="dimmed">Raw trip condition</Text>
              <Text
                size="xs"
                style={{
                  whiteSpace: "pre-wrap",
                  fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                }}
              >
                {JSON.stringify(rule.condition, null, 2)}
              </Text>
              <Text size="xs" c="dimmed">
                stable for {formatDuration(rule.stable_for_s)} · cooldown {formatDuration(rule.cooldown_s)} · on unknown {rule.on_unknown ?? "n/a"}
              </Text>
              {rule.last_trigger_age_s != null && (
                <Text size="xs" c="dimmed">last triggered {formatAge(rule.last_trigger_age_s)}</Text>
              )}
              {(rule.actions ?? []).map((item, index) => (
                <Text
                  key={`${ruleName}:action:${index}`}
                  size="xs"
                  style={{
                    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                    wordBreak: "break-word",
                  }}
                >
                  {formatActionSummary(item)}
                </Text>
              ))}
            </Stack>
          </details>
        </Stack>
        <Button
          size="compact-xs"
          variant="light"
          color="orange"
          disabled={clearBusy || loading || !latched}
          loading={clearBusy}
          onClick={() => { void onClearRuleLatch(processId, watchdogId, ruleName); }}
        >
          Clear latch
        </Button>
      </Group>
    </Card>
  );
}

export function WatchdogsPanel({
  processes,
  watchdogStatusByProcessId,
  watchdogLoadingByProcessId,
  watchdogErrorByProcessId,
  watchdogBusyByKey,
  onRefreshProcess,
  onToggleWatchdog,
  onClearRuleLatch,
}: Props) {
  return (
    <Stack gap="md">
      <Text size="sm" c="dimmed">
        Watchdog rules monitor telemetry and execute safety actions when a rule trips.
      </Text>
      {processes.length === 0 && (
        <Text size="sm" c="dimmed">No watchdog-capable processes are available.</Text>
      )}
      {processes.map((process) => {
        const processId = process.process_id;
        const watchdogs = watchdogStatusByProcessId[processId] ?? [];
        const loading = Boolean(watchdogLoadingByProcessId[processId]);
        const error = watchdogErrorByProcessId[processId];
        const processActive = isProcessRpcStateAvailable(process);
        return (
          <Card
            key={processId}
            radius="md"
            p="sm"
            style={{ border: "1px solid var(--card-border)" }}
          >
            <Stack gap="xs">
              <Group justify="space-between" align="flex-start">
                <Stack gap={2}>
                  <Group gap="xs" wrap="wrap">
                    <Text fw={600}>{processId}</Text>
                    <Badge variant="light" color={processStateColor(process.state)}>{process.state}</Badge>
                    <Badge variant="outline" color={processActive ? "teal" : "gray"}>
                      {processActive ? "RPC active" : "RPC inactive"}
                    </Badge>
                  </Group>
                  <Text size="xs" c="dimmed">
                    pid {process.pid ?? "n/a"} | hb age {process.hb_age_s != null ? `${process.hb_age_s.toFixed(2)} s` : "n/a"}
                  </Text>
                </Stack>
                <Button
                  size="xs"
                  variant="light"
                  loading={loading}
                  leftSection={<IconRefresh size={14} />}
                  onClick={() => { void onRefreshProcess(processId); }}
                >
                  Refresh process
                </Button>
              </Group>

              {error && <Text size="xs" c="red">{error}</Text>}

              {watchdogs.length > 0 ? (
                <Stack gap={6}>
                  {watchdogs.map((watchdog, watchdogIdx) => {
                    const watchdogId =
                      String(watchdog.watchdog_id ?? "").trim() || `watchdog_${watchdogIdx}`;
                    const toggleBusyKey = `${processId}:${watchdogId}:toggle`;
                    const toggleBusy = Boolean(watchdogBusyByKey[toggleBusyKey]);
                    const watchdogSummary = summarizeWatchdogRules(watchdog);
                    const turboSummary = beamlineTurboProtectionSummary(
                      watchdog.rules,
                      watchdog.enabled
                    );
                    const turboRules = watchdog.rules.filter(isBeamlineTurboRule);
                    const otherRules = watchdog.rules.filter((rule) => !isBeamlineTurboRule(rule));
                    return (
                      <Card
                        key={`${processId}:${watchdogId}`}
                        p="xs"
                        radius="sm"
                        style={{ border: "1px solid var(--card-border)" }}
                      >
                        <Stack gap={6}>
                          <Group justify="space-between" align="flex-start">
                            <Group gap="xs" wrap="wrap">
                              <Text size="sm" fw={600}>{watchdogId}</Text>
                              <Badge variant="light" color={watchdog.enabled ? "teal" : "gray"}>
                                {watchdog.enabled ? "Enabled" : "Disabled"}
                              </Badge>
                              <Badge variant="outline" color="gray">{watchdog.rules.length} rules</Badge>
                              <Badge variant="light" color={watchdogSummary.color}>{watchdogSummary.label}</Badge>
                            </Group>
                            <Switch
                              checked={Boolean(watchdog.enabled)}
                              disabled={toggleBusy || loading || !watchdogId}
                              onChange={(event) => {
                                void onToggleWatchdog(
                                  processId,
                                  watchdogId,
                                  event.currentTarget.checked
                                );
                              }}
                            />
                          </Group>

                          {turboRules.length > 0 && (
                            <Stack gap={4}>
                              <Group gap="xs" wrap="wrap">
                                <Text size="xs" fw={700}>Beamline turbo protection</Text>
                                {turboSummary && (
                                  <Badge variant="light" color={turboSummary.color}>
                                    {turboSummary.label}
                                  </Badge>
                                )}
                              </Group>
                              <Text size="xs" c="dimmed">
                                Each Hornet arms independently after pump-down. Any armed Hornet above 1e-2 Torr for 3 fresh samples stops all four beamline turbos.
                              </Text>
                              {turboRules.map((rule, index) => (
                                <RuleCard
                                  key={`${processId}:${watchdogId}:${rule.name}`}
                                  processId={processId}
                                  watchdogId={watchdogId}
                                  watchdogEnabled={watchdog.enabled}
                                  rule={rule}
                                  ruleIdx={index}
                                  loading={loading}
                                  watchdogBusyByKey={watchdogBusyByKey}
                                  onClearRuleLatch={onClearRuleLatch}
                                />
                              ))}
                            </Stack>
                          )}

                          {otherRules.length > 0 && (
                            <Stack gap={4}>
                              {turboRules.length > 0 && (
                                <Text size="xs" fw={700}>Other protection rules</Text>
                              )}
                              {otherRules.map((rule, index) => (
                                <RuleCard
                                  key={`${processId}:${watchdogId}:${rule.name}`}
                                  processId={processId}
                                  watchdogId={watchdogId}
                                  watchdogEnabled={watchdog.enabled}
                                  rule={rule}
                                  ruleIdx={index + turboRules.length}
                                  loading={loading}
                                  watchdogBusyByKey={watchdogBusyByKey}
                                  onClearRuleLatch={onClearRuleLatch}
                                />
                              ))}
                            </Stack>
                          )}

                          {watchdog.rules.length === 0 && (
                            <Text size="xs" c="dimmed">No watchdog rules reported.</Text>
                          )}
                        </Stack>
                      </Card>
                    );
                  })}
                </Stack>
              ) : (
                !loading && !error && (
                  <Text size="xs" c="dimmed">No watchdog status available yet.</Text>
                )
              )}
            </Stack>
          </Card>
        );
      })}
    </Stack>
  );
}
