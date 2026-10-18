import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Card,
  Group,
  ScrollArea,
  SimpleGrid,
  Stack,
  Table,
  Text,
} from "@mantine/core";
import { IconX } from "@tabler/icons-react";
import { useMemo, useState, type ReactNode } from "react";
import { resourceHealthRank } from "../features/devices/health";
import { useResourceListView } from "../features/devices/useDeviceListView";
import {
  buildWatchdogRuleRows,
  watchdogRuleAdapter,
  watchdogRuleHealth,
  type WatchdogRuleRow,
} from "../features/safety/safetyRows";
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
import { HealthMark } from "./HealthMark";
import { ResourceListToolbar } from "./ResourceListToolbar";
import { ConfirmDisableSwitch } from "./safety/ConfirmDisableSwitch";
import { SafetyProcessStrip } from "./safety/SafetyProcessStrip";
import { useSortableTable, type SortValue } from "./safety/useSortableTable";

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

function DetailField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Stack gap={0}>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text size="sm" component="div" style={{ wordBreak: "break-word" }}>
        {children}
      </Text>
    </Stack>
  );
}

const MONO = {
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
} as const;

function liveStateFor(row: WatchdogRuleRow) {
  return row.watchdogEnabled
    ? watchdogRuleLiveState(row.rule)
    : { label: "DISABLED", color: "gray" };
}

function ClearLatchButton({
  row,
  busy,
  onClearRuleLatch,
}: {
  row: WatchdogRuleRow;
  busy: boolean;
  onClearRuleLatch: Props["onClearRuleLatch"];
}) {
  return (
    <Button
      size="compact-xs"
      variant="light"
      color="orange"
      loading={busy}
      disabled={busy}
      onClick={(event) => {
        event.stopPropagation();
        void onClearRuleLatch(row.processId, row.watchdogId, row.ruleName);
      }}
    >
      Clear latch
    </Button>
  );
}

function WatchdogRuleDetail({
  row,
  clearBusy,
  onClearRuleLatch,
  onClose,
}: {
  row: WatchdogRuleRow;
  clearBusy: boolean;
  onClearRuleLatch: Props["onClearRuleLatch"];
  onClose: () => void;
}) {
  const rule = row.rule;
  const details: DetailedWatchdogRule = watchdogRuleDetails(rule);
  const state = liveStateFor(row);
  const confirmation = confirmationProgress(rule);
  const action = operationalAction(rule);
  const turbo = isBeamlineTurboRule(rule);
  const actionChain = actionChainPresentation(rule);
  return (
    <Card withBorder radius="lg" p="md">
      <Stack gap="sm">
        <Group justify="space-between" wrap="nowrap" align="flex-start">
          <Stack gap={4} style={{ minWidth: 0 }}>
            <Group gap={6} wrap="nowrap">
              <HealthMark health={watchdogRuleHealth(row)} />
              <Text fw={600} truncate>
                {row.ruleName}
              </Text>
            </Group>
            <Group gap={6}>
              <Badge size="sm" variant="light" color={state.color}>
                {state.label}
              </Badge>
              <Badge size="sm" variant="outline" color="gray">
                {rule.severity}
              </Badge>
              {actionChain ? (
                <Badge size="sm" variant="light" color={actionChain.color}>
                  {actionChain.label}
                </Badge>
              ) : null}
            </Group>
          </Stack>
          <Group gap="xs" wrap="nowrap">
            {rule.latched ? (
              <ClearLatchButton row={row} busy={clearBusy} onClearRuleLatch={onClearRuleLatch} />
            ) : null}
            <ActionIcon
              size="sm"
              variant="subtle"
              color="gray"
              aria-label="Close rule details"
              onClick={onClose}
            >
              <IconX size={14} />
            </ActionIcon>
          </Group>
        </Group>
        {!row.processActive && row.watchdogEnabled ? (
          <Text size="xs" c="red">
            {row.processId} is {row.process.state}: this rule is not being evaluated.
          </Text>
        ) : null}
        {rule.message ? <Text size="sm">{rule.message}</Text> : null}
        {action ? <DetailField label="Does">{action}</DetailField> : null}
        {actionChain && actionChain.failedTargets.length > 0 ? (
          <Text size="xs" c="red">
            Failed target{actionChain.failedTargets.length === 1 ? "" : "s"}:{" "}
            {actionChain.failedTargets.join(", ")}
          </Text>
        ) : null}
        {turbo ? (
          <>
            <DetailField label="Pressure">
              {formatValue(snapshotValue(rule, "p"))} Torr
            </DetailField>
            <Text size="xs" c="dimmed">
              Beamline turbo protection: each Hornet arms independently after pump-down. Any
              armed Hornet above 1e-2 Torr for 3 fresh samples stops all four beamline turbos.
            </Text>
            {hasIncompleteBeamlineTurboState(rule) ? (
              <Text size="xs" c="yellow">
                One or more turbo-state inputs are unavailable; this rule will not arm until at
                least one running turbo is positively observed. An already-armed rule is not
                affected.
              </Text>
            ) : null}
          </>
        ) : null}
        <SimpleGrid cols={2} spacing="xs" verticalSpacing="xs">
          <DetailField label="Ruleset">
            {row.watchdogId} · {row.processId}
          </DetailField>
          <DetailField label="Evaluated">{formatAge(rule.last_evaluated_age_s)}</DetailField>
          <DetailField label="Last triggered">
            {rule.last_trigger_age_s != null ? formatAge(rule.last_trigger_age_s) : "never"}
          </DetailField>
          {confirmation ? (
            <DetailField label="Confirmation">
              {confirmation.count}/{confirmation.target}
            </DetailField>
          ) : null}
          <DetailField label="Stable for">{formatDuration(rule.stable_for_s)}</DetailField>
          <DetailField label="Cooldown">{formatDuration(rule.cooldown_s)}</DetailField>
          <DetailField label="On unknown">{rule.on_unknown ?? "default"}</DetailField>
          <DetailField label="Latching">{rule.latch ? "yes" : "no"}</DetailField>
        </SimpleGrid>
        <Stack gap={4}>
          <Text size="xs" c="dimmed">
            Trip condition evaluation
          </Text>
          <ConditionTrace trace={rule.condition_evaluation} />
        </Stack>
        {details.arm != null ? (
          <DetailField label="Arming condition">
            <Text size="xs" style={MONO} component="div">
              {JSON.stringify(details.arm.condition, null, 2)}
            </Text>
          </DetailField>
        ) : null}
        {(rule.actions ?? []).length > 0 ? (
          <DetailField label="Actions">
            <Stack gap={2}>
              {(rule.actions ?? []).map((item, index) => (
                <Text key={`${row.key}:action:${index}`} size="xs" style={MONO}>
                  {formatActionSummary(item)}
                </Text>
              ))}
            </Stack>
          </DetailField>
        ) : null}
        <DetailField label="Raw trip condition">
          <Text size="xs" style={MONO} component="div">
            {JSON.stringify(rule.condition, null, 2)}
          </Text>
        </DetailField>
      </Stack>
    </Card>
  );
}

type Column = "state" | "rule" | "ruleset" | "severity" | "evaluated" | "triggered";

const COLUMNS: { key: Column; label: string; detail?: boolean }[] = [
  { key: "state", label: "State" },
  { key: "rule", label: "Rule" },
  // Hidden while a rule is selected: the detail panel shows them instead.
  { key: "ruleset", label: "Ruleset", detail: true },
  { key: "severity", label: "Severity", detail: true },
  { key: "evaluated", label: "Evaluated", detail: true },
  { key: "triggered", label: "Last triggered", detail: true },
];

function sortValue(row: WatchdogRuleRow, column: Column): SortValue {
  switch (column) {
    case "state":
      return resourceHealthRank(row, watchdogRuleAdapter);
    case "rule":
      return row.ruleName.toLowerCase();
    case "ruleset":
      return `${row.processId}/${row.watchdogId}`.toLowerCase();
    case "severity":
      return row.rule.severity === "critical" ? 0 : row.rule.severity === "warn" ? 1 : 2;
    case "evaluated":
      return row.rule.last_evaluated_age_s ?? null;
    case "triggered":
      return row.rule.last_trigger_age_s ?? null;
  }
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
  const rows = useMemo(
    () => buildWatchdogRuleRows(processes, watchdogStatusByProcessId),
    [processes, watchdogStatusByProcessId]
  );
  const view = useResourceListView(rows, watchdogRuleAdapter, {
    persistKey: "ecui.safety.watchdogs",
    initialSort: "health",
  });
  const { sorted, header } = useSortableTable(view.visible, sortValue);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const selectedRow = rows.find((row) => row.key === selectedKey) ?? null;
  const compact = selectedRow !== null;
  const showProcessName = processes.length > 1;
  const clearBusy = (row: WatchdogRuleRow) =>
    Boolean(watchdogBusyByKey[`${row.processId}:${row.watchdogId}:${row.ruleName}:clear`]) ||
    Boolean(watchdogLoadingByProcessId[row.processId]);

  return (
    <Stack gap="sm">
      {processes.length === 0 ? (
        <Text size="sm" c="dimmed">
          No watchdog-capable processes are available.
        </Text>
      ) : (
        <Stack gap={6}>
          {processes.map((process) => {
            const processId = process.process_id;
            const watchdogs = watchdogStatusByProcessId[processId] ?? [];
            const loading = Boolean(watchdogLoadingByProcessId[processId]);
            return (
              <Stack key={processId} gap={4}>
                <SafetyProcessStrip
                  process={process}
                  loading={loading}
                  error={watchdogErrorByProcessId[processId]}
                  onRefresh={() => {
                    void onRefreshProcess(processId);
                  }}
                />
                {watchdogs.map((watchdog, watchdogIdx) => {
                  const watchdogId =
                    String(watchdog.watchdog_id ?? "").trim() || `watchdog_${watchdogIdx}`;
                  const toggleBusy = Boolean(watchdogBusyByKey[`${processId}:${watchdogId}:toggle`]);
                  const summary = summarizeWatchdogRules(watchdog);
                  const turboSummary = beamlineTurboProtectionSummary(
                    watchdog.rules,
                    watchdog.enabled
                  );
                  return (
                    <Group
                      key={`${processId}:${watchdogId}`}
                      className="safety-ruleset-line"
                      justify="space-between"
                      wrap="nowrap"
                      gap="sm"
                    >
                      <Group gap="xs" wrap="wrap" style={{ minWidth: 0 }}>
                        <Text size="xs" fw={600}>
                          {watchdogId}
                        </Text>
                        <Text size="xs" c="dimmed">
                          {watchdog.rules.length} rule{watchdog.rules.length === 1 ? "" : "s"}
                        </Text>
                        <Badge size="xs" variant="light" color={watchdog.enabled ? summary.color : "gray"}>
                          {watchdog.enabled ? summary.label : "Disabled"}
                        </Badge>
                        {turboSummary ? (
                          <Badge size="xs" variant="light" color={turboSummary.color}>
                            {turboSummary.label}
                          </Badge>
                        ) : null}
                      </Group>
                      <ConfirmDisableSwitch
                        size="xs"
                        checked={Boolean(watchdog.enabled)}
                        disabled={toggleBusy || loading || !watchdogId}
                        subject={`watchdog "${watchdogId}" (all ${watchdog.rules.length} rules)`}
                        onChange={(enabled) => {
                          void onToggleWatchdog(processId, watchdogId, enabled);
                        }}
                      />
                    </Group>
                  );
                })}
                {watchdogs.length === 0 && !loading && !watchdogErrorByProcessId[processId] ? (
                  <Text size="xs" c="dimmed" pl="md">
                    No watchdog status available yet.
                  </Text>
                ) : null}
              </Stack>
            );
          })}
        </Stack>
      )}

      <Box style={{ maxWidth: 640 }}>
        <ResourceListToolbar
          view={view}
          noun="rules"
          healthyLabel="safe"
          healthyTitle="Evaluated and not triggered"
          customSortLabel="Default order"
        />
      </Box>

      <Group gap="md" align="flex-start" wrap="nowrap">
        <ScrollArea.Autosize
          style={{ flex: 1, minWidth: 0 }}
          mah="min(60vh, 40rem)"
          type="auto"
          offsetScrollbars
        >
          {sorted.length === 0 ? (
            <Text size="sm" c="dimmed" ta="center" py="lg">
              {rows.length === 0 ? "No watchdog rules reported." : "No rules match the current filter."}
            </Text>
          ) : (
            <Table
              className="resource-overview-table"
              highlightOnHover
              stickyHeader
              verticalSpacing={4}
              fz="xs"
            >
              <Table.Thead>
                <Table.Tr>
                  {COLUMNS.filter((c) => !compact || !c.detail).map((c) => header(c.key, c.label))}
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {sorted.map((row) => {
                  const state = liveStateFor(row);
                  return (
                    <Table.Tr
                      key={row.key}
                      className={`resource-overview-row${
                        row.key === selectedKey ? " resource-overview-row-selected" : ""
                      }`}
                      onClick={() =>
                        setSelectedKey((prev) => (prev === row.key ? null : row.key))
                      }
                    >
                      <Table.Td>
                        <Group gap={6} wrap="nowrap">
                          <HealthMark health={watchdogRuleHealth(row)} />
                          <Badge size="xs" variant="light" color={state.color}>
                            {state.label}
                          </Badge>
                        </Group>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" fw={600}>
                          {row.ruleName}
                        </Text>
                      </Table.Td>
                      {compact ? null : (
                        <>
                          <Table.Td>
                            <Text size="xs" c="dimmed">
                              {showProcessName ? `${row.processId} / ` : ""}
                              {row.watchdogId}
                            </Text>
                          </Table.Td>
                          <Table.Td>{row.rule.severity}</Table.Td>
                          <Table.Td>{formatAge(row.rule.last_evaluated_age_s)}</Table.Td>
                          <Table.Td>
                            {row.rule.last_trigger_age_s != null
                              ? formatAge(row.rule.last_trigger_age_s)
                              : "—"}
                          </Table.Td>
                        </>
                      )}
                      <Table.Td>
                        {row.rule.latched ? (
                          <ClearLatchButton
                            row={row}
                            busy={clearBusy(row)}
                            onClearRuleLatch={onClearRuleLatch}
                          />
                        ) : null}
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          )}
        </ScrollArea.Autosize>
        {selectedRow ? (
          <Box className="resource-overview-detail">
            <ScrollArea.Autosize mah="min(60vh, 40rem)" type="auto">
              <WatchdogRuleDetail
                row={selectedRow}
                clearBusy={clearBusy(selectedRow)}
                onClearRuleLatch={onClearRuleLatch}
                onClose={() => setSelectedKey(null)}
              />
            </ScrollArea.Autosize>
          </Box>
        ) : null}
      </Group>
    </Stack>
  );
}
