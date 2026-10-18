import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Collapse,
  Group,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  UnstyledButton,
} from "@mantine/core";
import { IconChevronDown, IconChevronRight, IconX } from "@tabler/icons-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { callProcess } from "../../api";
import {
  coerceCheckParam,
  commandTargetNoun,
  conditionParamNames,
  followerGuardSentence,
  formatParams,
  interlockStatusLine,
  parseCheckReply,
  type CheckVerdict,
} from "../../features/safety/interlockNarrative";
import { interlockRuleHealth, type InterlockRuleRow } from "../../features/safety/safetyRows";
import { formatInputAge, formatReading } from "../../features/safety/watchdogNarrative";
import { useDeviceTelemetry } from "../../features/telemetry/TelemetryLatestStore";
import type { DeviceStatus, InterlockTelemetryStatus } from "../../types";
import { DeviceNameInline } from "../DeviceNameInline";
import { HealthMark } from "../HealthMark";
import { ConfirmDisableSwitch } from "./ConfirmDisableSwitch";
import { InterlockConditionView, type ConditionTelemetryBinding } from "./InterlockConditionView";

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <Text size="xs" c="dimmed" fw={600} tt="uppercase" style={{ letterSpacing: "0.04em" }}>
      {children}
    </Text>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
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

function wallAgeS(ts: { t_wall?: number } | undefined): number | null {
  return typeof ts?.t_wall === "number" ? Math.max(0, Date.now() / 1000 - ts.t_wall) : null;
}

// ---------------------------------------------------------------------------
// Live inputs (from the UI's own telemetry stream; interlocks report no state)
// ---------------------------------------------------------------------------

type BindingWithProcess = InterlockTelemetryStatus & { process_id?: string | null };

function LiveInputRow({ binding }: { binding: BindingWithProcess }) {
  const deviceId = binding.device_id ?? "";
  const snapshot = useDeviceTelemetry(deviceId);
  if (!deviceId) {
    return (
      <Table.Tr>
        <Table.Td>
          <Text size="xs" span fw={500}>
            {binding.process_id ?? "process"}.{binding.signal}
          </Text>
        </Table.Td>
        <Table.Td colSpan={2}>
          <Text size="xs" c="dimmed" span>
            process signal (not shown here)
          </Text>
        </Table.Td>
      </Table.Tr>
    );
  }
  const signal = snapshot[binding.signal];
  const ageS = wallAgeS(signal?.ts);
  const missing = signal == null || signal.value == null;
  const stale = !missing && ageS != null && ageS > binding.max_age_s;
  return (
    <Table.Tr>
      <Table.Td>
        <Text size="xs" span c={missing || stale ? "yellow" : undefined}>
          {missing || stale ? "△" : "●"}
        </Text>{" "}
        <Text size="xs" span fw={500}>
          {deviceId}.{binding.signal}
        </Text>
      </Table.Td>
      <Table.Td style={{ fontVariantNumeric: "tabular-nums" }}>
        {missing ? (
          <Text size="xs" span c="yellow">
            no data
          </Text>
        ) : typeof signal.value === "number" ? (
          `${formatReading(signal.value)}${signal.units ? ` ${signal.units}` : ""}`
        ) : (
          String(signal.value)
        )}
      </Table.Td>
      <Table.Td>
        <Text size="xs" span c={stale ? "orange" : "dimmed"}>
          {ageS != null
            ? `${formatInputAge(ageS)} (max ${binding.max_age_s} s)`
            : `max age ${binding.max_age_s} s`}
          {binding.required ? "" : " · optional"}
        </Text>
      </Table.Td>
    </Table.Tr>
  );
}

function FollowerSentence({ row }: { row: InterlockRuleRow }) {
  const follower = row.follower;
  const snapshot = useDeviceTelemetry(follower?.device_id ?? "");
  const signalName = follower?.current_freq_signal || "frequency";
  const value = snapshot[signalName]?.value;
  const sentence = followerGuardSentence(row, typeof value === "number" ? value : null);
  return sentence ? <Text size="sm">{sentence}</Text> : null;
}

// ---------------------------------------------------------------------------
// Check a command (dry run through the interlock process; nothing is executed)
// ---------------------------------------------------------------------------

function CheckCommand({
  row,
  deviceById,
  rowsByName,
  onSelectRule,
}: {
  row: InterlockRuleRow;
  deviceById: Map<string, DeviceStatus>;
  rowsByName: Map<string, InterlockRuleRow>;
  onSelectRule: (key: string) => void;
}) {
  const fixedDevice = row.matchDeviceId && row.matchDeviceId !== "*" ? row.matchDeviceId : null;
  const fixedAction = row.matchAction && row.matchAction !== "*" ? row.matchAction : null;
  const paramNames = useMemo(() => conditionParamNames(row.interlock?.condition), [row.interlock]);
  const deviceOptions = useMemo(() => [...deviceById.keys()].sort(), [deviceById]);
  const [deviceId, setDeviceId] = useState<string | null>(fixedDevice ?? deviceOptions[0] ?? null);
  const [action, setAction] = useState(fixedAction ?? "");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [verdict, setVerdict] = useState<CheckVerdict | null>(null);

  useEffect(() => {
    setVerdict(null);
  }, [deviceId, action, values]);

  const run = async () => {
    if (!deviceId || !action.trim()) {
      return;
    }
    setBusy(true);
    try {
      const params = Object.fromEntries(
        paramNames
          .filter((name) => (values[name] ?? "").trim() !== "")
          .map((name) => [name, coerceCheckParam(values[name] ?? "")])
      );
      const resp = await callProcess(row.processId, "command_interceptor.check", {
        command: { device_id: deviceId, action: action.trim(), params },
      });
      setVerdict(parseCheckReply(resp));
    } catch (error) {
      setVerdict({ kind: "error", message: error instanceof Error ? error.message : String(error) });
    } finally {
      setBusy(false);
    }
  };

  const blockingRow =
    verdict?.kind === "blocked" && verdict.rule ? rowsByName.get(verdict.rule) ?? null : null;

  return (
    <Stack gap={6}>
      <SectionLabel>Check a command</SectionLabel>
      <Text size="xs" c="dimmed">
        Asks {row.processId} whether a command would pass its rules right now. Nothing is sent to
        the device; other interceptors in the chain are not consulted.
      </Text>
      <Group gap="xs" align="flex-end" wrap="wrap">
        {fixedDevice ? (
          <Field label="Device">{fixedDevice}</Field>
        ) : (
          <Select
            size="xs"
            label="Device"
            data={deviceOptions}
            value={deviceId}
            onChange={setDeviceId}
            searchable
            w={150}
          />
        )}
        {fixedAction ? (
          <Field label="Action">{fixedAction}</Field>
        ) : (
          <TextInput
            size="xs"
            label="Action"
            value={action}
            onChange={(event) => setAction(event.currentTarget.value)}
            w={150}
          />
        )}
        {paramNames.map((name) => (
          <TextInput
            key={name}
            size="xs"
            label={name}
            value={values[name] ?? ""}
            onChange={(event) => {
              const next = event.currentTarget.value;
              setValues((prev) => ({ ...prev, [name]: next }));
            }}
            w={110}
          />
        ))}
        <Button size="xs" variant="light" loading={busy} disabled={!deviceId || !action.trim()} onClick={() => void run()}>
          Check
        </Button>
      </Group>
      {verdict ? (
        <div
          className={`watchdog-status-box watchdog-status-${
            verdict.kind === "allowed"
              ? "teal"
              : verdict.kind === "transformed"
                ? "orange"
                : verdict.kind === "blocked"
                  ? "red"
                  : "gray"
          }`}
        >
          {verdict.kind === "allowed" ? (
            <Text size="sm">Allowed: the command would go through.</Text>
          ) : verdict.kind === "transformed" ? (
            <Text size="sm">
              Allowed with changes{verdict.rule ? ` by "${verdict.rule}"` : ""}: params become{" "}
              {formatParams(verdict.params)}.
            </Text>
          ) : verdict.kind === "blocked" ? (
            <Group gap={6} wrap="wrap">
              <Text size="sm">
                Blocked by "{verdict.rule ?? "unknown rule"}"
                {verdict.message ? `: ${verdict.message}` : verdict.code ? ` (${verdict.code})` : ""}
              </Text>
              {blockingRow && blockingRow.key !== row.key ? (
                <Button size="compact-xs" variant="subtle" onClick={() => onSelectRule(blockingRow.key)}>
                  Show that rule
                </Button>
              ) : null}
            </Group>
          ) : (
            <Text size="sm">Could not check: {verdict.message}</Text>
          )}
        </div>
      ) : null}
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

type Props = {
  row: InterlockRuleRow;
  rows: InterlockRuleRow[];
  deviceById: Map<string, DeviceStatus>;
  busy: boolean;
  onToggle: (row: InterlockRuleRow, enabled: boolean) => void;
  onClose: () => void;
  onSelectRule: (key: string) => void;
};

function overlaps(a: string, b: string): boolean {
  return a === "*" || b === "*" || a === b;
}

export function InterlockRuleDetail({
  row,
  rows,
  deviceById,
  busy,
  onToggle,
  onClose,
  onSelectRule,
}: Props) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const health = interlockRuleHealth(row);
  const status = interlockStatusLine(row);
  const rule = row.interlock;
  const follower = row.follower;
  const badge = row.active
    ? { label: "Active", color: "teal" }
    : health === "failed"
      ? { label: "Not enforced", color: "red" }
      : { label: "Disabled", color: "gray" };
  const alsoChecked = rows.filter(
    (other) =>
      other.key !== row.key &&
      other.active &&
      overlaps(other.matchDeviceId, row.matchDeviceId) &&
      overlaps(other.matchAction, row.matchAction)
  );
  const rowsByName = useMemo(() => {
    const byName = new Map<string, InterlockRuleRow>();
    for (const r of rows) {
      if (r.processId === row.processId && !byName.has(r.name)) {
        byName.set(r.name, r);
      }
    }
    return byName;
  }, [rows, row.processId]);

  return (
    <Card withBorder radius="lg" p="md">
      <Stack gap="sm">
        <Group justify="space-between" wrap="nowrap" align="flex-start">
          <Stack gap={2} style={{ minWidth: 0 }}>
            <Group gap={6} wrap="nowrap">
              <HealthMark health={health} />
              <Text fw={600} truncate>
                {row.name}
              </Text>
            </Group>
            <Badge size="sm" variant="light" color={badge.color} style={{ alignSelf: "flex-start" }}>
              {badge.label}
            </Badge>
            <Text size="xs" c="dimmed">
              {row.kindLabel} · {row.rulesetLabel} · {row.processId}
            </Text>
          </Stack>
          <Group gap="xs" wrap="nowrap">
            <ConfirmDisableSwitch
              checked={row.enabled}
              disabled={busy || !row.ruleId}
              subject={`rule "${row.name}"`}
              onChange={(enabled) => onToggle(row, enabled)}
            />
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

        <div className={`watchdog-status-box watchdog-status-${status.tone}`}>
          <Text size="sm">{status.text}</Text>
          {status.blockText ? (
            <Text size="sm" mt={4}>
              Blocks with: “{status.blockText}”
            </Text>
          ) : null}
          {status.notes.map((note) => (
            <Text key={note} size="xs" c="dimmed" mt={4}>
              {note}
            </Text>
          ))}
        </div>

        {follower ? <FollowerSentence row={row} /> : null}

        {rule && rule.telemetry.length > 0 ? (
          <Stack gap={2}>
            <SectionLabel>Inputs (live)</SectionLabel>
            <Table verticalSpacing={2} horizontalSpacing={6} fz="xs" className="watchdog-inputs-table">
              <Table.Tbody>
                {rule.telemetry.map((binding) => (
                  <LiveInputRow key={binding.as} binding={binding as BindingWithProcess} />
                ))}
              </Table.Tbody>
            </Table>
          </Stack>
        ) : null}

        {rule ? (
          <Stack gap={4}>
            <SectionLabel>Allows the command when</SectionLabel>
            <InterlockConditionView
              condition={rule.condition}
              telemetry={rule.telemetry as ConditionTelemetryBinding[]}
              commandDeviceId={row.matchDeviceId}
              commandAction={row.matchAction}
            />
          </Stack>
        ) : null}

        {follower && (follower.effects ?? []).length > 0 ? (
          <Stack gap={2}>
            <SectionLabel>Follow-up effects</SectionLabel>
            {follower.effects.map((effect, idx) => (
              <Text key={idx} size="xs">
                {effect.device_id}.{effect.action} → {effect.param}
              </Text>
            ))}
          </Stack>
        ) : null}

        {alsoChecked.length > 0 ? (
          <Stack gap={4}>
            <SectionLabel>Also checked for these commands</SectionLabel>
            <Group gap={6} wrap="wrap">
              {alsoChecked.map((other) => (
                <UnstyledButton
                  key={other.key}
                  className="safety-rule-link"
                  onClick={() => onSelectRule(other.key)}
                  title={commandTargetNoun(other.matchDeviceId, other.matchAction)}
                >
                  <Text size="xs" component="span">
                    {other.name}
                  </Text>
                </UnstyledButton>
              ))}
            </Group>
          </Stack>
        ) : null}

        {rule && row.active ? (
          <CheckCommand
            key={row.key}
            row={row}
            deviceById={deviceById}
            rowsByName={rowsByName}
            onSelectRule={onSelectRule}
          />
        ) : null}

        <Stack gap={4}>
          <UnstyledButton onClick={() => setDetailsOpen((prev) => !prev)} aria-expanded={detailsOpen}>
            <Group gap={4}>
              {detailsOpen ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}
              <SectionLabel>Details</SectionLabel>
            </Group>
          </UnstyledButton>
          <Collapse in={detailsOpen}>
            <SimpleGrid cols={2} spacing="xs" verticalSpacing="xs">
              <Field label="Rule id">{row.ruleId}</Field>
              <Field label="Matches">
                <DeviceNameInline
                  deviceId={row.matchDeviceId}
                  device={deviceById.get(row.matchDeviceId) ?? null}
                  size="sm"
                  suffix={`.${row.matchAction}`}
                />
              </Field>
              {rule ? <Field label="On block code">{rule.on_block?.code || "—"}</Field> : null}
              {rule ? <Field label="Transforms params">{rule.has_allow_transform ? "yes" : "no"}</Field> : null}
              {follower?.csv_path ? <Field label="Table">{follower.csv_path}</Field> : null}
            </SimpleGrid>
            {row.interceptor?.source ? (
              <Field label="Source">
                <Text size="xs" c="dimmed" component="span">
                  {row.interceptor.source}
                </Text>
              </Field>
            ) : null}
          </Collapse>
        </Stack>
      </Stack>
    </Card>
  );
}
