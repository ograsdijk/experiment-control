import { Badge, Group, Stack, Text } from "@mantine/core";
import type { ReactNode } from "react";

import { formatAge, processHealth } from "../../features/devices/health";
import { isProcessRpcStateAvailable, processStateColor } from "../../features/runtime/helpers";
import type { ProcessStatus } from "../../types";
import { HealthMark } from "../HealthMark";
import { RefreshIconButton } from "../RefreshIconButton";

type Props = {
  process: ProcessStatus;
  loading: boolean;
  error?: string | null;
  onRefresh: () => void;
  /** Right-hand extras, e.g. rule counts or ruleset enable switches. */
  children?: ReactNode;
};

/** One compact line per safety process, above the rule table. */
export function SafetyProcessStrip({ process, loading, error, onRefresh, children }: Props) {
  const rpcActive = isProcessRpcStateAvailable(process);
  return (
    <Stack gap={2} className="safety-process-strip">
      <Group justify="space-between" wrap="nowrap" gap="sm">
        <Group gap="xs" wrap="wrap" style={{ minWidth: 0 }}>
          <HealthMark health={processHealth(process)} />
          <Text size="sm" fw={600}>
            {process.process_id}
          </Text>
          <Badge size="xs" variant="light" color={processStateColor(process.state)}>
            {process.state}
          </Badge>
          {rpcActive ? null : (
            <Badge size="xs" variant="outline" color="gray">
              RPC inactive
            </Badge>
          )}
          <Text size="xs" c="dimmed">
            pid {process.pid ?? "—"} · hb {formatAge(process.hb_age_s)}
          </Text>
        </Group>
        <Group gap="xs" wrap="nowrap">
          {children}
          <RefreshIconButton
            size="sm"
            label={`Refresh ${process.process_id}`}
            loading={loading}
            onClick={onRefresh}
          />
        </Group>
      </Group>
      {error ? (
        <Text size="xs" c="red">
          {error}
        </Text>
      ) : null}
    </Stack>
  );
}
