import { Badge, Button, Card, Group, Stack, Switch, Text } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import { isProcessRpcStateAvailable, processStateColor } from "../features/runtime/helpers";
import type { ProcessStatus, WatchdogStatus } from "../types";
import { InterlockConditionView } from "./InterlocksModal";

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

function formatMonoSeconds(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "n/a";
  }
  return `${value.toFixed(2)} s`;
}

function watchdogRuleState(rule: WatchdogStatus["rules"][number]): {
  label: string;
  color: string;
} {
  if (rule.latched) {
    return { label: "Latched", color: "orange" };
  }
  if (rule.alarm) {
    return { label: "Alarm", color: "red" };
  }
  if (rule.unknown) {
    return { label: "Unknown", color: "yellow" };
  }
  if (rule.last_evaluated_mono == null) {
    return { label: "Pending", color: "gray" };
  }
  return { label: "Clear", color: "teal" };
}

function summarizeWatchdogRules(watchdog: WatchdogStatus): {
  label: string;
  color: string;
} {
  if (!watchdog.enabled) {
    return { label: "Disabled", color: "gray" };
  }
  let latched = 0;
  let unknown = 0;
  let alarm = 0;
  let pending = 0;
  for (const rule of watchdog.rules) {
    if (rule.latched) {
      latched += 1;
    } else if (rule.alarm) {
      alarm += 1;
    } else if (rule.unknown) {
      unknown += 1;
    } else if (rule.last_evaluated_mono == null) {
      pending += 1;
    }
  }
  if (latched > 0) {
    return { label: `${latched} latched`, color: "orange" };
  }
  if (alarm > 0) {
    return { label: `${alarm} alarm`, color: "red" };
  }
  if (unknown > 0) {
    return { label: `${unknown} unknown`, color: "yellow" };
  }
  if (pending > 0) {
    return { label: `${pending} pending`, color: "gray" };
  }
  return { label: "All clear", color: "teal" };
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
  const formatActionSummary = (action: {
    device_id: string;
    action: string;
    params?: Record<string, unknown>;
    timeout_s?: number | null;
    retries?: number;
  }): string => {
    const params = action.params ?? {};
    const entries = Object.entries(params);
    const paramLabel =
      entries.length > 0
        ? entries
            .map(([key, value]) => {
              if (
                value === null ||
                typeof value === "number" ||
                typeof value === "boolean"
              ) {
                return `${key}=${String(value)}`;
              }
              if (typeof value === "string") {
                return `${key}=${value}`;
              }
              try {
                return `${key}=${JSON.stringify(value)}`;
              } catch {
                return `${key}=${String(value)}`;
              }
            })
            .join(", ")
        : "no params";
    const timeoutLabel =
      typeof action.timeout_s === "number" && Number.isFinite(action.timeout_s)
        ? ` | timeout ${action.timeout_s}s`
        : "";
    const retriesLabel =
      typeof action.retries === "number" && Number.isFinite(action.retries)
        ? ` | retries ${Math.max(0, Math.trunc(action.retries))}`
        : "";
    return `${action.device_id}.${action.action}(${paramLabel})${timeoutLabel}${retriesLabel}`;
  };

  return (
    <Stack gap="md">
      <Text size="sm" c="dimmed">
        Watchdog rules monitor telemetry and can latch alarms / trigger actions.
      </Text>
      {processes.length === 0 && (
        <Text size="sm" c="dimmed">
          No watchdog-capable processes are available.
        </Text>
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
                    <Badge variant="light" color={processStateColor(process.state)}>
                      {process.state}
                    </Badge>
                    <Badge variant="outline" color={processActive ? "teal" : "gray"}>
                      {processActive ? "RPC active" : "RPC inactive"}
                    </Badge>
                  </Group>
                  <Text size="xs" c="dimmed">
                    pid {process.pid ?? "n/a"} | hb age{" "}
                    {process.hb_age_s != null ? `${process.hb_age_s.toFixed(2)} s` : "n/a"}
                  </Text>
                </Stack>
                <Button
                  size="xs"
                  variant="light"
                  loading={loading}
                  leftSection={<IconRefresh size={14} />}
                  onClick={() => {
                    void onRefreshProcess(processId);
                  }}
                >
                  Refresh process
                </Button>
              </Group>
              {error && (
                <Text size="xs" c="red">
                  {error}
                </Text>
              )}
              {watchdogs.length > 0 ? (
                <Stack gap={6}>
                  {watchdogs.map((watchdog, watchdogIdx) => {
                    const watchdogId =
                      String(watchdog.watchdog_id ?? "").trim() || `watchdog_${watchdogIdx}`;
                    const toggleBusyKey = `${processId}:${watchdogId}:toggle`;
                    const toggleBusy = Boolean(watchdogBusyByKey[toggleBusyKey]);
                    const watchdogSummary = summarizeWatchdogRules(watchdog);
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
                              <Text size="sm" fw={600}>
                                {watchdogId}
                              </Text>
                              <Badge
                                variant="light"
                                color={watchdog.enabled ? "teal" : "gray"}
                              >
                                {watchdog.enabled ? "Enabled" : "Disabled"}
                              </Badge>
                              <Badge variant="outline" color="gray">
                                {watchdog.rules.length} rules
                              </Badge>
                              <Badge variant="light" color={watchdogSummary.color}>
                                {watchdogSummary.label}
                              </Badge>
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
                          {watchdog.rules.length > 0 ? (
                            <Stack gap={4}>
                              {watchdog.rules.map((rule, ruleIdx) => {
                                const ruleName =
                                  String(rule.name ?? "").trim() || `rule_${ruleIdx}`;
                                const clearBusyKey =
                                  `${processId}:${watchdogId}:${ruleName}:clear`;
                                const clearBusy = Boolean(watchdogBusyByKey[clearBusyKey]);
                                const latched = Boolean(rule.latched);
                                const ruleState = watchdog.enabled
                                  ? watchdogRuleState(rule)
                                  : { label: "Disabled", color: "gray" };
                                const severity = String(rule.severity ?? "info").toLowerCase();
                                const severityColor =
                                  severity === "critical" || severity === "error"
                                    ? "red"
                                    : severity === "warning"
                                      ? "orange"
                                      : "gray";
                                return (
                                  <Card
                                    key={`${processId}:${watchdogId}:${ruleName}`}
                                    p={6}
                                    radius="sm"
                                    style={{ border: "1px solid var(--card-border)" }}
                                  >
                                    <Group justify="space-between" align="flex-start">
                                      <Stack gap={2}>
                                        <Group gap="xs" wrap="wrap">
                                          <Text size="xs" fw={600}>
                                            {ruleName}
                                          </Text>
                                          <Badge variant="light" color={ruleState.color}>
                                            {ruleState.label}
                                          </Badge>
                                          <Badge variant="light" color={severityColor}>
                                            {severity}
                                          </Badge>
                                        </Group>
                                        <Text size="xs" c="dimmed">
                                          last evaluation:{" "}
                                          {formatMonoSeconds(rule.last_evaluated_mono)}
                                        </Text>
                                        <Text size="xs" c="dimmed">
                                          stable since: {formatMonoSeconds(rule.stable_since_mono)}
                                        </Text>
                                        <Text size="xs" c="dimmed">
                                          last trigger: {formatMonoSeconds(rule.last_trigger_mono)}
                                        </Text>
                                        <Text size="xs" c="dimmed">
                                          stable for: {formatMonoSeconds(rule.stable_for_s)}
                                        </Text>
                                        <Text size="xs" c="dimmed">
                                          cooldown: {formatMonoSeconds(rule.cooldown_s)}
                                        </Text>
                                        <Text size="xs" c="dimmed">
                                          on unknown: {rule.on_unknown ?? "n/a"}
                                        </Text>
                                        {rule.message && (
                                          <Text size="xs" c="dimmed">
                                            Message: {rule.message}
                                          </Text>
                                        )}
                                        <Text size="xs" c="dimmed">
                                          Condition:
                                        </Text>
                                        <InterlockConditionView
                                          condition={rule.condition}
                                          telemetry={rule.telemetry}
                                        />
                                        {Array.isArray(rule.actions) && rule.actions.length > 0 && (
                                          <Stack gap={2}>
                                            <Text size="xs" c="dimmed">
                                              Actions:
                                            </Text>
                                            {rule.actions.map((action, actionIdx) => (
                                              <Text
                                                key={`${processId}:${watchdogId}:${ruleName}:action:${actionIdx}`}
                                                size="xs"
                                                style={{
                                                  fontFamily:
                                                    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                                                  wordBreak: "break-word",
                                                }}
                                              >
                                                {formatActionSummary(action)}
                                              </Text>
                                            ))}
                                          </Stack>
                                        )}
                                      </Stack>
                                      <Button
                                        size="compact-xs"
                                        variant="light"
                                        color="orange"
                                        disabled={clearBusy || loading || !latched}
                                        loading={clearBusy}
                                        onClick={() => {
                                          void onClearRuleLatch(processId, watchdogId, ruleName);
                                        }}
                                      >
                                        Clear latch
                                      </Button>
                                    </Group>
                                  </Card>
                                );
                              })}
                            </Stack>
                          ) : (
                            <Text size="xs" c="dimmed">
                              No watchdog rules reported.
                            </Text>
                          )}
                        </Stack>
                      </Card>
                    );
                  })}
                </Stack>
              ) : (
                !loading &&
                !error && (
                  <Text size="xs" c="dimmed">
                    No watchdog status available yet.
                  </Text>
                )
              )}
            </Stack>
          </Card>
        );
      })}
    </Stack>
  );
}
