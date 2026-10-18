import { ActionIcon, Badge, Card, Group, Stack, Text } from "@mantine/core";
import { IconX } from "@tabler/icons-react";
import type { ReactNode } from "react";

import { followerGuardKind, interlockRuleHealth, type InterlockRuleRow } from "../../features/safety/safetyRows";
import { formatFreqHz } from "../../features/runtime/helpers";
import type { DeviceStatus } from "../../types";
import { DeviceNameInline } from "../DeviceNameInline";
import { HealthMark } from "../HealthMark";
import { ConfirmDisableSwitch } from "./ConfirmDisableSwitch";
import { InterlockConditionView, type ConditionTelemetryBinding } from "./InterlockConditionView";

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

type Props = {
  row: InterlockRuleRow;
  deviceById: Map<string, DeviceStatus>;
  busy: boolean;
  onToggle: (row: InterlockRuleRow, enabled: boolean) => void;
  onClose: () => void;
};

export function InterlockRuleDetail({ row, deviceById, busy, onToggle, onClose }: Props) {
  const health = interlockRuleHealth(row);
  const rule = row.interlock;
  const follower = row.follower;
  const device = (id: string, suffix: string) => (
    <DeviceNameInline
      deviceId={id}
      device={deviceById.get(id) ?? null}
      size="sm"
      suffix={suffix}
    />
  );
  return (
    <Card withBorder radius="lg" p="md">
      <Stack gap="sm">
        <Group justify="space-between" wrap="nowrap" align="flex-start">
          <Stack gap={4} style={{ minWidth: 0 }}>
            <Group gap={6} wrap="nowrap">
              <HealthMark health={health} />
              <Text fw={600} truncate>
                {row.name}
              </Text>
            </Group>
            <Group gap={6}>
              <Badge size="sm" variant="light" color={row.active ? "teal" : health === "failed" ? "red" : "gray"}>
                {row.active ? "Active" : health === "failed" ? "Not enforced" : "Inactive"}
              </Badge>
              <Badge size="sm" variant="outline" color="gray">
                {row.kindLabel}
              </Badge>
            </Group>
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
        {health === "failed" ? (
          <Text size="xs" c="red">
            {row.processId} is {row.process.state}: this rule is enabled but nothing is enforcing it.
          </Text>
        ) : null}
        {!row.rulesetEnabled ? (
          <Text size="xs" c="dimmed">
            Ruleset {row.rulesetLabel} is disabled.
          </Text>
        ) : null}

        <Field label={follower ? "Trigger" : "Match"}>
          {device(row.matchDeviceId, `.${row.matchAction}`)}
          {follower?.trigger_param ? ` (${follower.trigger_param})` : null}
        </Field>
        <Field label="Ruleset">
          {row.rulesetLabel} · {row.processId}
        </Field>

        {rule ? (
          <>
            <Stack gap={4}>
              <Text size="xs" c="dimmed">
                Condition (must pass for the command to go through)
              </Text>
              <InterlockConditionView
                condition={rule.condition}
                telemetry={rule.telemetry as ConditionTelemetryBinding[]}
                commandDeviceId={row.matchDeviceId}
                commandAction={row.matchAction}
              />
            </Stack>
            {rule.telemetry.length > 0 ? (
              <Field label="Telemetry inputs">
                <Stack gap={2}>
                  {rule.telemetry.map((binding) => (
                    <Text key={binding.as} size="xs">
                      <b>{binding.as}</b> ← {binding.device_id}.{binding.signal} · max age{" "}
                      {binding.max_age_s} s{binding.required ? "" : " · optional"}
                    </Text>
                  ))}
                </Stack>
              </Field>
            ) : null}
            {rule.on_block?.code || rule.on_block?.message ? (
              <Field label="On block">
                {[rule.on_block?.code, rule.on_block?.message].filter(Boolean).join(" · ")}
              </Field>
            ) : null}
            {rule.has_allow_transform ? (
              <Text size="xs" c="dimmed">
                Transforms command params when allowed.
              </Text>
            ) : null}
            {row.interceptor?.source ? (
              <Field label="Source">
                <Text size="xs" c="dimmed" component="span">
                  {row.interceptor.source}
                </Text>
              </Field>
            ) : null}
          </>
        ) : null}

        {follower ? (
          <FollowerDetail follower={follower} device={device} />
        ) : null}
      </Stack>
    </Card>
  );
}

function FollowerDetail({
  follower,
  device,
}: {
  follower: NonNullable<InterlockRuleRow["follower"]>;
  device: (id: string, suffix: string) => ReactNode;
}) {
  const { hasRangeGuard, hasStepGuard, maxStepHz } = followerGuardKind(follower);
  const effects = Array.isArray(follower.effects) ? follower.effects : [];
  return (
    <>
      {hasRangeGuard ? (
        <Field label="Allowed range">
          {formatFreqHz(follower.min_freq_hz)} to {formatFreqHz(follower.max_freq_hz)}
        </Field>
      ) : null}
      {hasStepGuard ? (
        <>
          <Field label="Step guard">
            Reject when |requested − current| &gt; {formatFreqHz(maxStepHz ?? Number.NaN)}
          </Field>
          <Field label="Current source">
            {device(follower.device_id, `.${follower.current_freq_signal || "telemetry"}`)}
          </Field>
        </>
      ) : null}
      {effects.length > 0 ? (
        <Field label="Follow-up effects">
          <Stack gap={2}>
            {effects.map((effect, idx) => (
              <Text key={idx} size="xs">
                {effect.device_id}.{effect.action} → {effect.param}
              </Text>
            ))}
          </Stack>
        </Field>
      ) : null}
      {follower.csv_path ? (
        <Field label="Table">
          <Text size="xs" c="dimmed" component="span">
            {follower.csv_path}
          </Text>
        </Field>
      ) : null}
    </>
  );
}
