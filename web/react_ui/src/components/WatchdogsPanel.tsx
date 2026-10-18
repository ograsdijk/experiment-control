import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Card,
  Collapse,
  Group,
  ScrollArea,
  SimpleGrid,
  Stack,
  Table,
  Text,
  UnstyledButton,
} from "@mantine/core";
import { IconChevronDown, IconChevronRight, IconX } from "@tabler/icons-react";
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
  formatAgo,
  formatInputAge,
  formatReading,
  inputUnavailable,
  lastActionOutcomes,
  watchdogInputs,
  watchdogStatusLine,
  type BlockingInterlock,
  type WatchdogInput,
} from "../features/safety/watchdogNarrative";
import {
  InterlockConditionView,
  type ConditionTelemetryBinding,
} from "./safety/InterlockConditionView";
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
  /** Jump to the interlock rule that blocked a watchdog action. */
  onShowInterlockRule?: (blocked: BlockingInterlock) => void;
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

const formatAge = formatAgo;

function formatValue(value: unknown): string {
  if (typeof value === "number") {
    return formatReading(value);
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

function formatSettingSeconds(value: number | null | undefined, zeroLabel: string): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "default";
  }
  return value === 0 ? zeroLabel : `${formatReading(value)} s`;
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <Text size="xs" c="dimmed" fw={600} tt="uppercase" style={{ letterSpacing: "0.04em" }}>
      {children}
    </Text>
  );
}

function InputsTable({ inputs }: { inputs: WatchdogInput[] }) {
  return (
    <Table verticalSpacing={2} horizontalSpacing={6} fz="xs" className="watchdog-inputs-table">
      <Table.Tbody>
        {inputs.map((input) => {
          const missing = inputUnavailable(input);
          const tooOld =
            input.ageS != null && input.maxAgeS != null && input.ageS > input.maxAgeS;
          return (
            <Table.Tr key={input.alias}>
              <Table.Td>
                <Text size="xs" span c={missing ? "yellow" : undefined}>
                  {missing ? "△" : "●"}
                </Text>{" "}
                <Text size="xs" span fw={500}>
                  {input.label}
                </Text>
              </Table.Td>
              <Table.Td style={{ fontVariantNumeric: "tabular-nums" }}>
                {missing ? (
                  <Text size="xs" span c="yellow">
                    no data
                  </Text>
                ) : typeof input.value === "number" ? (
                  formatReading(input.value)
                ) : (
                  String(input.value)
                )}
              </Table.Td>
              <Table.Td>
                <Text size="xs" span c={tooOld ? "orange" : "dimmed"}>
                  {input.ageS != null ? formatInputAge(input.ageS) : "—"}
                  {input.maxAgeS != null ? ` (max ${input.maxAgeS} s)` : ""}
                </Text>
              </Table.Td>
            </Table.Tr>
          );
        })}
      </Table.Tbody>
    </Table>
  );
}

function WatchdogRuleDetail({
  row,
  clearBusy,
  onClearRuleLatch,
  onClose,
  onShowInterlockRule,
}: {
  row: WatchdogRuleRow;
  clearBusy: boolean;
  onClearRuleLatch: Props["onClearRuleLatch"];
  onClose: () => void;
  onShowInterlockRule?: (blocked: BlockingInterlock) => void;
}) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const rule = row.rule;
  const details: DetailedWatchdogRule = watchdogRuleDetails(rule);
  const state = liveStateFor(row);
  const status = watchdogStatusLine(row);
  const inputs = watchdogInputs(rule);
  const chain = details.last_action_chain;
  const outcomes = lastActionOutcomes(rule);
  const confirmation = confirmationProgress(rule);
  const turbo = isBeamlineTurboRule(rule);
  const telemetry = (rule.telemetry ?? []) as ConditionTelemetryBinding[];
  const interlockLink = (blocked: BlockingInterlock) =>
    onShowInterlockRule ? (
      <Button
        size="compact-xs"
        variant="subtle"
        onClick={() => onShowInterlockRule(blocked)}
      >
        Show interlock rule
      </Button>
    ) : null;

  return (
    <Card withBorder radius="lg" p="md">
      <Stack gap="sm">
        <Group justify="space-between" wrap="nowrap" align="flex-start">
          <Stack gap={2} style={{ minWidth: 0 }}>
            <Group gap={6} wrap="nowrap">
              <HealthMark health={watchdogRuleHealth(row)} />
              <Text fw={600} truncate>
                {row.ruleName}
              </Text>
            </Group>
            <Badge size="sm" variant="light" color={state.color} style={{ alignSelf: "flex-start" }}>
              {state.label}
            </Badge>
            <Text size="xs" c="dimmed">
              {rule.severity} · {row.watchdogId} · evaluated {formatAge(rule.last_evaluated_age_s)}
              {rule.last_trigger_age_s != null
                ? ` · last tripped ${formatAge(rule.last_trigger_age_s)}`
                : ""}
            </Text>
          </Stack>
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

        <div className={`watchdog-status-box watchdog-status-${status.tone}`}>
          <Text size="sm">{status.text}</Text>
          {status.actionText ? (
            <Group gap={6} wrap="wrap" mt={4}>
              <Text size="sm">{status.actionText}</Text>
              {status.blockedBy ? interlockLink(status.blockedBy) : null}
            </Group>
          ) : null}
          {rule.latched ? (
            <Group mt={6}>
              <ClearLatchButton row={row} busy={clearBusy} onClearRuleLatch={onClearRuleLatch} />
            </Group>
          ) : null}
        </div>

        {rule.message ? (
          <Text size="xs" c="dimmed">
            {rule.message}
          </Text>
        ) : null}

        {inputs.length > 0 ? (
          <Stack gap={2}>
            <SectionLabel>Inputs</SectionLabel>
            <InputsTable inputs={inputs} />
          </Stack>
        ) : null}

        <Stack gap={4}>
          <SectionLabel>Trips when</SectionLabel>
          <InterlockConditionView
            condition={rule.condition}
            telemetry={telemetry}
            evaluation={rule.condition_evaluation}
            resultMode="trigger"
          />
          {confirmation ? (
            <Text size="xs" c="dimmed">
              Needs {confirmation.target} consecutive samples (now {confirmation.count}).
            </Text>
          ) : null}
        </Stack>

        {details.arm != null ? (
          <Stack gap={4}>
            <SectionLabel>Arms when ({details.armed ? "armed" : "disarmed"})</SectionLabel>
            <InterlockConditionView condition={details.arm.condition} telemetry={telemetry} />
          </Stack>
        ) : null}

        {turbo ? (
          <Stack gap={4}>
            <SectionLabel>Beamline turbo protection</SectionLabel>
            <Text size="xs">
              Pressure: <b>{formatValue(snapshotValue(rule, "p"))} Torr</b>
            </Text>
            <Text size="xs" c="dimmed">
              Each Hornet arms independently after pump-down. Any armed Hornet above 1e-2 Torr
              for 3 fresh samples stops all four beamline turbos.
            </Text>
            {hasIncompleteBeamlineTurboState(rule) ? (
              <Text size="xs" c="yellow">
                One or more turbo-state inputs are unavailable; this rule will not arm until at
                least one running turbo is positively observed. An already-armed rule is not
                affected.
              </Text>
            ) : null}
          </Stack>
        ) : null}

        <Stack gap={4}>
          <SectionLabel>
            {chain
              ? `Last action run${chain.duration_ms != null ? ` · ${formatReading(chain.duration_ms)} ms` : ""}`
              : "Actions on trip"}
          </SectionLabel>
          {chain ? (
            outcomes.map((outcome, index) => (
              <Stack key={`${row.key}:outcome:${index}`} gap={0}>
                <Group gap={6} wrap="nowrap">
                  <Text size="xs" c={outcome.ok ? "teal" : "red"} fw={700}>
                    {outcome.ok ? "✓" : "✗"}
                  </Text>
                  <Text size="xs" style={MONO}>
                    {outcome.target}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {outcome.attempts} attempt{outcome.attempts === 1 ? "" : "s"}
                  </Text>
                </Group>
                {outcome.message ? (
                  <Text size="xs" c="red" pl="md">
                    {outcome.blockedBy
                      ? `Blocked by interlock rule "${outcome.blockedBy.rule}": ${outcome.message}`
                      : outcome.message}
                  </Text>
                ) : null}
              </Stack>
            ))
          ) : (rule.actions ?? []).length > 0 ? (
            (rule.actions ?? []).map((item, index) => (
              <Text key={`${row.key}:action:${index}`} size="xs" style={MONO}>
                {formatActionSummary(item)}
              </Text>
            ))
          ) : (
            <Text size="xs" c="dimmed">
              No actions configured.
            </Text>
          )}
        </Stack>

        <Stack gap={4}>
          <UnstyledButton onClick={() => setSettingsOpen((prev) => !prev)} aria-expanded={settingsOpen}>
            <Group gap={4}>
              {settingsOpen ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}
              <SectionLabel>Settings</SectionLabel>
            </Group>
          </UnstyledButton>
          <Collapse in={settingsOpen}>
            <SimpleGrid cols={2} spacing="xs" verticalSpacing="xs">
              <DetailField label="Must hold for">
                {formatSettingSeconds(rule.stable_for_s, "trips immediately")}
              </DetailField>
              <DetailField label="Cooldown">
                {formatSettingSeconds(rule.cooldown_s, "none")}
              </DetailField>
              <DetailField label="On unknown">{rule.on_unknown ?? "ignore"}</DetailField>
              <DetailField label="Latching">{rule.latch ? "yes" : "no"}</DetailField>
              <DetailField label="Ruleset">{row.watchdogId}</DetailField>
              <DetailField label="Process">{row.processId}</DetailField>
            </SimpleGrid>
          </Collapse>
        </Stack>
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
  onShowInterlockRule,
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
                onShowInterlockRule={onShowInterlockRule}
              />
            </ScrollArea.Autosize>
          </Box>
        ) : null}
      </Group>
    </Stack>
  );
}
