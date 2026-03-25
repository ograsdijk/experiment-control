import {
  Badge,
  Button,
  Card,
  Group,
  Modal,
  Select,
  Stack,
  Switch,
  Text,
} from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";
import {
  formatFreqHz,
  interlockRuleKey,
  isProcessRpcStateAvailable,
  processStateColor,
} from "../features/runtime/helpers";
import type {
  CommandInterceptorRoute,
  DeviceStatus,
  FollowerRuleStatus,
  InterlockInterceptorStatus,
  ProcessStatus,
} from "../types";
import { DeviceNameInline } from "./DeviceNameInline";

type Props = {
  opened: boolean;
  onClose: () => void;
  onRefresh: () => Promise<unknown> | void;
  devices: ReadonlyArray<DeviceStatus>;
  processes: ReadonlyArray<ProcessStatus>;
  followerRulesByProcessId: Record<string, FollowerRuleStatus[]>;
  interlockStatusByProcessId: Record<string, InterlockInterceptorStatus[]>;
  interlocksLoadingByProcessId: Record<string, boolean>;
  interlocksErrorByProcessId: Record<string, string>;
  interlockRuleBusyByKey: Record<string, boolean>;
  commandInterceptorRoutes: CommandInterceptorRoute[];
  commandInterceptorRoutesLoading: boolean;
  commandInterceptorRoutesError: string | null;
  onRefreshProcess: (processId: string) => Promise<unknown> | void;
  onToggleFollowerRule: (
    processId: string,
    ruleId: string,
    enabled: boolean
  ) => Promise<unknown> | void;
  onToggleInterlockRule: (
    processId: string,
    interceptorId: string,
    ruleId: string,
    enabled: boolean
  ) => Promise<unknown> | void;
};

export function InterlocksModal({
  opened,
  onClose,
  onRefresh,
  devices,
  processes,
  followerRulesByProcessId,
  interlockStatusByProcessId,
  interlocksLoadingByProcessId,
  interlocksErrorByProcessId,
  interlockRuleBusyByKey,
  commandInterceptorRoutes,
  commandInterceptorRoutesLoading,
  commandInterceptorRoutesError,
  onRefreshProcess,
  onToggleFollowerRule,
  onToggleInterlockRule,
}: Props) {
  const [chainDeviceId, setChainDeviceId] = useState("");
  const [chainAction, setChainAction] = useState("");
  const deviceById = useMemo(
    () => new Map(devices.map((device) => [device.device_id, device])),
    [devices]
  );
  const processById = useMemo(
    () => new Map(processes.map((process) => [process.process_id, process])),
    [processes]
  );
  const hasRunningInterlockProcess = useMemo(
    () => processes.some((process) => isProcessRpcStateAvailable(process)),
    [processes]
  );
  const chainDeviceOptions = useMemo(() => {
    const ids = new Set<string>();
    for (const route of commandInterceptorRoutes) {
      const deviceId = String(route.device_id ?? "").trim();
      if (deviceId) {
        ids.add(deviceId);
      }
    }
    return [...ids]
      .sort((a, b) => a.localeCompare(b))
      .map((deviceId) => ({
        value: deviceId,
        label:
          deviceId === "*"
            ? "* (wildcard)"
            : `${
                deviceById.get(deviceId)?.is_remote ||
                deviceById.get(deviceId)?.source_kind === "federated"
                  ? "⇄ "
                  : ""
              }${deviceId}`,
      }));
  }, [commandInterceptorRoutes, deviceById]);
  const chainActionOptions = useMemo(() => {
    const selectedDevice = chainDeviceId.trim();
    if (!selectedDevice) {
      return [];
    }
    const actions = new Set<string>();
    for (const route of commandInterceptorRoutes) {
      const routeDevice = String(route.device_id ?? "").trim();
      const routeAction = String(route.action ?? "").trim();
      if (!routeAction) {
        continue;
      }
      if (selectedDevice === "*") {
        if (routeDevice !== "*") {
          continue;
        }
      } else if (routeDevice !== selectedDevice && routeDevice !== "*") {
        continue;
      }
      actions.add(routeAction);
    }
    return [...actions]
      .sort((a, b) => a.localeCompare(b))
      .map((action) => ({
        value: action,
        label: action === "*" ? "* (wildcard)" : action,
      }));
  }, [commandInterceptorRoutes, chainDeviceId]);
  useEffect(() => {
    if (!chainAction) {
      return;
    }
    if (!chainActionOptions.some((option) => option.value === chainAction)) {
      setChainAction("");
    }
  }, [chainAction, chainActionOptions]);
  const effectiveChain = useMemo(() => {
    const deviceId = chainDeviceId.trim();
    const action = chainAction.trim();
    if (!deviceId || !action) {
      return [];
    }
    const ordered = [...commandInterceptorRoutes].sort((a, b) => a.order - b.order);
    const out: Array<{
      route: CommandInterceptorRoute;
      process: ProcessStatus | null;
      matchedInterlockRules: Array<{
        interceptorId: string;
        ruleId: string;
        name: string;
        hasAllowTransform: boolean;
      }>;
      matchedFollowerRules: Array<{
        ruleId: string;
        name: string;
        hasRangeGuard: boolean;
        minFreqHz: number | null;
        maxFreqHz: number | null;
        hasStepGuard: boolean;
        maxStepHz: number | null;
        currentSourceLabel: string;
        effectsCount: number;
        effectsSummary: string;
      }>;
    }> = [];
    const seen = new Set<string>();
    for (const route of ordered) {
      const deviceMatches = route.device_id === "*" || route.device_id === deviceId;
      const actionMatches = route.action === "*" || route.action === action;
      if (!deviceMatches || !actionMatches) {
        continue;
      }
      if (seen.has(route.process_id)) {
        continue;
      }
      seen.add(route.process_id);
      const process = processById.get(route.process_id) ?? null;
      const processActive = process ? isProcessRpcStateAvailable(process) : false;
      const matchedInterlockRules: Array<{
        interceptorId: string;
        ruleId: string;
        name: string;
        hasAllowTransform: boolean;
      }> = [];
      if (processActive) {
        const interceptors = interlockStatusByProcessId[route.process_id] ?? [];
        for (const interceptor of interceptors) {
          if (!interceptor.enabled) {
            continue;
          }
          const interceptorId = String(interceptor.interceptor_id ?? "").trim();
          for (const rule of interceptor.rules ?? []) {
            if (!rule.enabled) {
              continue;
            }
            const matchDevice = String(rule.match?.device_id ?? "").trim();
            const matchAction = String(rule.match?.action ?? "").trim();
            const ruleDeviceMatches = matchDevice === "*" || matchDevice === deviceId;
            const ruleActionMatches = matchAction === "*" || matchAction === action;
            if (!ruleDeviceMatches || !ruleActionMatches) {
              continue;
            }
            matchedInterlockRules.push({
              interceptorId,
              ruleId: String(rule.rule_id ?? "").trim(),
              name: String(rule.name ?? "").trim() || String(rule.rule_id ?? "rule"),
              hasAllowTransform: Boolean(rule.has_allow_transform),
            });
          }
        }
      }
      const matchedFollowerRules: Array<{
        ruleId: string;
        name: string;
        hasRangeGuard: boolean;
        minFreqHz: number | null;
        maxFreqHz: number | null;
        hasStepGuard: boolean;
        maxStepHz: number | null;
        currentSourceLabel: string;
        effectsCount: number;
        effectsSummary: string;
      }> = [];
      if (processActive) {
        const followers = followerRulesByProcessId[route.process_id] ?? [];
        for (const rule of followers) {
          if (!rule.enabled) {
            continue;
          }
          const triggerDevice = String(rule.device_id ?? "").trim();
          const triggerAction = String(rule.trigger_action ?? "").trim();
          const followerDeviceMatches =
            triggerDevice === "*" || triggerDevice === deviceId;
          const followerActionMatches =
            triggerAction === "*" || triggerAction === action;
          if (!followerDeviceMatches || !followerActionMatches) {
            continue;
          }
          const hasRangeGuard =
            Number.isFinite(rule.min_freq_hz) && Number.isFinite(rule.max_freq_hz);
          const maxStepHz =
            typeof rule.max_step_hz === "number" && Number.isFinite(rule.max_step_hz)
              ? rule.max_step_hz
              : null;
          const hasStepGuard = maxStepHz !== null;
          const effects = Array.isArray(rule.effects) ? rule.effects : [];
          const effectsSummary = effects
            .map((effect) => `${effect.device_id}.${effect.action} -> ${effect.param}`)
            .join(", ");
          matchedFollowerRules.push({
            ruleId: String(rule.rule_id ?? "").trim(),
            name: String(rule.name ?? "").trim() || String(rule.rule_id ?? "follower"),
            hasRangeGuard,
            minFreqHz: hasRangeGuard ? rule.min_freq_hz : null,
            maxFreqHz: hasRangeGuard ? rule.max_freq_hz : null,
            hasStepGuard,
            maxStepHz,
            currentSourceLabel: `${triggerDevice || deviceId}.${
              String(rule.current_freq_signal ?? "").trim() || "telemetry"
            }`,
            effectsCount: effects.length,
            effectsSummary,
          });
        }
      }
      out.push({
        route,
        process,
        matchedInterlockRules,
        matchedFollowerRules,
      });
    }
    return out;
  }, [
    commandInterceptorRoutes,
    chainDeviceId,
    chainAction,
    processById,
    interlockStatusByProcessId,
    followerRulesByProcessId,
  ]);

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Interlocks"
      size="clamp(56rem, 92vw, 96rem)"
      centered
      zIndex={420}
    >
      <Stack gap="md">
        <Group justify="space-between">
          <Text size="sm" c="dimmed">
            Rule controls discovered via process capabilities.
          </Text>
          <Button
            size="xs"
            variant="light"
            leftSection={<IconRefresh size={14} />}
            onClick={() => {
              void onRefresh();
            }}
          >
            Refresh
          </Button>
        </Group>
        {hasRunningInterlockProcess && (
          <Card
            radius="md"
            p="sm"
            style={{ border: "1px solid var(--card-border)" }}
          >
            <Stack gap="xs">
              <Group justify="space-between" align="center">
                <Text size="sm" fw={600}>
                  Interceptor chain
                </Text>
                {commandInterceptorRoutesLoading ? (
                  <Badge variant="light" color="gray">
                    Loading
                  </Badge>
                ) : (
                  <Badge variant="light" color="gray">
                    {commandInterceptorRoutes.length} routes
                  </Badge>
                )}
              </Group>
              <Text size="xs" c="dimmed">
                Registered order from <code>manager.interceptors.list</code>.
              </Text>
              {commandInterceptorRoutesError && (
                <Text size="xs" c="red">
                  {commandInterceptorRoutesError}
                </Text>
              )}
              {commandInterceptorRoutes.length > 0 ? (
                <Stack gap={4}>
                  {commandInterceptorRoutes.map((route) => {
                    const process = processById.get(route.process_id) ?? null;
                    const processColor = process
                      ? processStateColor(process.state)
                      : "gray";
                    return (
                      <Group
                        key={`${route.order}:${route.process_id}:${route.device_id}:${route.action}`}
                        gap="xs"
                        wrap="wrap"
                      >
                        <Badge variant="outline" color="gray">
                          #{route.order}
                        </Badge>
                        <Badge variant="light" color={processColor}>
                          {route.process_id}
                        </Badge>
                        <Text size="xs" c="dimmed">
                          <DeviceNameInline
                            deviceId={route.device_id}
                            device={deviceById.get(route.device_id) ?? null}
                            size="xs"
                            c="dimmed"
                            suffix={`.${route.action}`}
                          />
                        </Text>
                      </Group>
                    );
                  })}
                </Stack>
              ) : (
                <Text size="xs" c="dimmed">
                  No registered interceptor routes.
                </Text>
              )}
              <Group gap="xs" align="flex-end" wrap="wrap" mt={4}>
                <Select
                  size="xs"
                  label="Device"
                  placeholder={
                    chainDeviceOptions.length > 0
                      ? "Select device"
                      : "No chain devices"
                  }
                  data={chainDeviceOptions}
                  value={chainDeviceId || null}
                  onChange={(value) => setChainDeviceId(String(value ?? ""))}
                  w={180}
                  clearable
                  searchable
                  comboboxProps={{ zIndex: 500 }}
                />
                <Select
                  size="xs"
                  label="Action"
                  placeholder={
                    !chainDeviceId
                      ? "Select device first"
                      : chainActionOptions.length > 0
                      ? "Select action"
                      : "No chain actions"
                  }
                  data={chainActionOptions}
                  value={chainAction || null}
                  onChange={(value) => setChainAction(String(value ?? ""))}
                  w={280}
                  clearable
                  searchable
                  comboboxProps={{ zIndex: 500 }}
                  disabled={!chainDeviceId}
                />
              </Group>
              <Text size="xs" c="dimmed">
                Effective chain (wildcard matching + process deduplication).
              </Text>
              {chainDeviceId.trim() && chainAction.trim() ? (
                effectiveChain.length > 0 ? (
                  <Stack gap={4}>
                    {effectiveChain.map((entry, idx) => {
                      const processColor = entry.process
                        ? processStateColor(entry.process.state)
                        : "gray";
                      return (
                        <Stack
                          key={`effective:${entry.route.order}:${entry.route.process_id}:${idx}`}
                          gap={2}
                        >
                          <Group gap="xs" wrap="wrap">
                            <Badge variant="outline" color="teal">
                              {idx + 1}
                            </Badge>
                            <Badge variant="light" color={processColor}>
                              {entry.route.process_id}
                            </Badge>
                            <Text size="xs" c="dimmed">
                              route #{entry.route.order} (
                              <DeviceNameInline
                                deviceId={entry.route.device_id}
                                device={deviceById.get(entry.route.device_id) ?? null}
                                size="xs"
                                c="dimmed"
                                suffix={`.${entry.route.action}`}
                              />
                              )
                            </Text>
                          </Group>
                          {entry.matchedFollowerRules.length > 0 && (
                            <Stack gap={4}>
                              {entry.matchedFollowerRules.map((rule, ruleIdx) => (
                                <Card
                                  key={`effective-rule:${entry.route.process_id}:${rule.ruleId || rule.name}:${ruleIdx}`}
                                  p={6}
                                  radius="sm"
                                  style={{ border: "1px solid var(--card-border)" }}
                                >
                                  <Stack gap={2}>
                                    <Group gap="xs" wrap="wrap">
                                      <Text size="xs" fw={600}>
                                        {rule.name}
                                      </Text>
                                      {rule.hasRangeGuard && (
                                        <Badge variant="outline" color="blue">
                                          Range guard
                                        </Badge>
                                      )}
                                      {rule.hasStepGuard && (
                                        <Badge variant="outline" color="orange">
                                          Step guard
                                        </Badge>
                                      )}
                                      {!rule.hasRangeGuard && !rule.hasStepGuard && (
                                        <Badge variant="outline" color="gray">
                                          Trigger guard
                                        </Badge>
                                      )}
                                      {rule.effectsCount > 0 && (
                                        <Badge variant="outline" color="grape">
                                          Follow-up effects
                                        </Badge>
                                      )}
                                    </Group>
                                    {rule.hasRangeGuard && (
                                      <Text size="xs" c="dimmed">
                                        Allowed range: {formatFreqHz(rule.minFreqHz ?? Number.NaN)}{" "}
                                        to {formatFreqHz(rule.maxFreqHz ?? Number.NaN)}
                                      </Text>
                                    )}
                                    {rule.hasStepGuard && (
                                      <Text size="xs" c="dimmed">
                                        Step rule: reject when |requested - current| &gt;{" "}
                                        {formatFreqHz(rule.maxStepHz ?? Number.NaN)}
                                      </Text>
                                    )}
                                    {rule.hasStepGuard && (
                                      <Text size="xs" c="dimmed">
                                        Current source: {rule.currentSourceLabel}
                                      </Text>
                                    )}
                                    {rule.effectsSummary && (
                                      <Text size="xs" c="dimmed">
                                        Effects: {rule.effectsSummary}
                                      </Text>
                                    )}
                                  </Stack>
                                </Card>
                              ))}
                            </Stack>
                          )}
                        </Stack>
                      );
                    })}
                  </Stack>
                ) : (
                  <Text size="xs" c="dimmed">
                    No matching interceptors for{" "}
                    <DeviceNameInline
                      deviceId={chainDeviceId.trim()}
                      device={deviceById.get(chainDeviceId.trim()) ?? null}
                      size="xs"
                      c="dimmed"
                      suffix={`.${chainAction.trim()}`}
                    />
                    .
                  </Text>
                )
              ) : (
                <Text size="xs" c="dimmed">
                  Enter both device and action to resolve effective chain.
                </Text>
              )}
            </Stack>
          </Card>
        )}
        {processes.length === 0 && (
          <Text size="sm" c="dimmed">
            No interlock-capable processes are available.
          </Text>
        )}
        {processes.map((process) => {
          const processId = process.process_id;
          const followerRules = followerRulesByProcessId[processId] ?? [];
          const interceptors = interlockStatusByProcessId[processId] ?? [];
          const loading = Boolean(interlocksLoadingByProcessId[processId]);
          const error = interlocksErrorByProcessId[processId];
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
                      <Badge
                        variant="outline"
                        color={processActive ? "teal" : "gray"}
                      >
                        {processActive ? "RPC active" : "RPC inactive"}
                      </Badge>
                    </Group>
                    <Text size="xs" c="dimmed">
                      pid {process.pid ?? "n/a"} | hb age{" "}
                      {process.hb_age_s != null
                        ? `${process.hb_age_s.toFixed(2)} s`
                        : "n/a"}
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
                {followerRules.length > 0 && (
                  <Stack gap={6}>
                    <Text size="sm" fw={600}>
                      Follower rules
                    </Text>
                    {followerRules.map((rule, ruleIdx) => {
                      const ruleId = String(rule.rule_id ?? `r${ruleIdx}`);
                      const effects = Array.isArray(rule.effects) ? rule.effects : [];
                      const hasRangeGuard =
                        Number.isFinite(rule.min_freq_hz) &&
                        Number.isFinite(rule.max_freq_hz);
                      const maxStepHz =
                        typeof rule.max_step_hz === "number" &&
                        Number.isFinite(rule.max_step_hz)
                          ? rule.max_step_hz
                          : null;
                      const hasStepGuard = maxStepHz !== null;
                      const busyKey = interlockRuleKey(processId, "follower", ruleId);
                      const busy = Boolean(interlockRuleBusyByKey[busyKey]);
                      const isActive = processActive && Boolean(rule.enabled);
                      const effectsSummary = effects
                        .map(
                          (effect) =>
                            `${effect.device_id}.${effect.action} -> ${effect.param}`
                        )
                        .join(", ");
                      return (
                        <Card
                          key={`${processId}:${ruleId}`}
                          p="xs"
                          radius="sm"
                          style={{ border: "1px solid var(--card-border)" }}
                        >
                          <Group justify="space-between" align="flex-start">
                            <Stack gap={4}>
                              <Group gap="xs" wrap="wrap">
                                <Text size="sm" fw={600}>
                                  {rule.name}
                                </Text>
                                <Badge variant="light" color={isActive ? "teal" : "gray"}>
                                  {isActive ? "Active" : "Inactive"}
                                </Badge>
                                {hasRangeGuard && (
                                  <Badge variant="outline" color="blue">
                                    Range guard
                                  </Badge>
                                )}
                                {hasStepGuard && (
                                  <Badge variant="outline" color="orange">
                                    Step guard
                                  </Badge>
                                )}
                                {!hasRangeGuard && !hasStepGuard && (
                                  <Badge variant="outline" color="gray">
                                    Trigger guard
                                  </Badge>
                                )}
                                {effects.length > 0 && (
                                  <Badge variant="outline" color="grape">
                                    Follow-up effects
                                  </Badge>
                                )}
                              </Group>
                              <Text size="xs" c="dimmed">
                                Trigger:{" "}
                                <DeviceNameInline
                                  deviceId={rule.device_id}
                                  device={deviceById.get(rule.device_id) ?? null}
                                  size="xs"
                                  c="dimmed"
                                  suffix={`.${rule.trigger_action}`}
                                />{" "}
                                (
                                {rule.trigger_param})
                              </Text>
                              {hasRangeGuard && (
                                <Text size="xs" c="dimmed">
                                  Allowed range: {formatFreqHz(rule.min_freq_hz)} to{" "}
                                  {formatFreqHz(rule.max_freq_hz)}
                                </Text>
                              )}
                              {hasStepGuard && (
                                <Text size="xs" c="dimmed">
                                  Step rule: reject when |requested - current| &gt;{" "}
                                  {formatFreqHz(maxStepHz)}
                                </Text>
                              )}
                              {hasStepGuard && (
                                <Text size="xs" c="dimmed">
                                  Current source:{" "}
                                  <DeviceNameInline
                                    deviceId={rule.device_id}
                                    device={deviceById.get(rule.device_id) ?? null}
                                    size="xs"
                                    c="dimmed"
                                    suffix={`.${rule.current_freq_signal || "telemetry"}`}
                                  />
                                </Text>
                              )}
                              {effects.length > 0 && (
                                <Text size="xs" c="dimmed">
                                  Effects: {effectsSummary}
                                </Text>
                              )}
                            </Stack>
                            <Switch
                              checked={Boolean(rule.enabled)}
                              disabled={busy || loading || !ruleId}
                              onChange={(event) => {
                                void onToggleFollowerRule(
                                  processId,
                                  ruleId,
                                  event.currentTarget.checked
                                );
                              }}
                            />
                          </Group>
                        </Card>
                      );
                    })}
                  </Stack>
                )}
                {interceptors.length > 0 && (
                  <Stack gap={6}>
                    <Text size="sm" fw={600}>
                      Interlock rulesets
                    </Text>
                    {interceptors.map((interceptor, interceptorIdx) => {
                      const interceptorId = String(
                        interceptor.interceptor_id ?? `interceptor_${interceptorIdx}`
                      );
                      const rules = Array.isArray(interceptor.rules)
                        ? interceptor.rules
                        : [];
                      return (
                        <Card
                          key={`${processId}:${interceptorId}`}
                          p="xs"
                          radius="sm"
                          style={{ border: "1px solid var(--card-border)" }}
                        >
                          <Stack gap={6}>
                            <Group justify="space-between" align="flex-start">
                              <Stack gap={2}>
                                <Group gap="xs" wrap="wrap">
                                  <Text size="sm" fw={600}>
                                    {interceptorId}
                                  </Text>
                                  <Badge
                                    variant="light"
                                    color={Boolean(interceptor.enabled) ? "teal" : "gray"}
                                  >
                                    {Boolean(interceptor.enabled) ? "Enabled" : "Disabled"}
                                  </Badge>
                                  <Badge variant="outline" color="gray">
                                    {interceptor.enabled_rule_count ?? 0}/
                                    {interceptor.rule_count} rules enabled
                                  </Badge>
                                </Group>
                                {interceptor.source && (
                                  <Text size="xs" c="dimmed">
                                    Source: {interceptor.source}
                                  </Text>
                                )}
                              </Stack>
                            </Group>
                            {rules.map((rule, ruleIdx) => {
                              const ruleId = String(rule.rule_id ?? `r${ruleIdx}`);
                              const busyKey = interlockRuleKey(
                                processId,
                                interceptorId,
                                ruleId
                              );
                              const busy = Boolean(interlockRuleBusyByKey[busyKey]);
                              const isActive =
                                processActive &&
                                Boolean(interceptor.enabled) &&
                                Boolean(rule.enabled);
                              return (
                                <Card
                                  key={`${processId}:${interceptorId}:${ruleId}`}
                                  p="xs"
                                  radius="sm"
                                  style={{ border: "1px solid var(--card-border)" }}
                                >
                                  <Group justify="space-between" align="flex-start">
                                    <Stack gap={4}>
                                      <Group gap="xs" wrap="wrap">
                                        <Text size="sm" fw={600}>
                                          {rule.name}
                                        </Text>
                                        <Badge
                                          variant="light"
                                          color={isActive ? "teal" : "gray"}
                                        >
                                          {isActive ? "Active" : "Inactive"}
                                        </Badge>
                                        <Badge
                                          variant="outline"
                                          color={rule.has_allow_transform ? "grape" : "gray"}
                                        >
                                          {rule.has_allow_transform
                                            ? "Transforms params"
                                            : "No transform"}
                                        </Badge>
                                      </Group>
                                      <Text size="xs" c="dimmed">
                                        Match:{" "}
                                        <DeviceNameInline
                                          deviceId={rule.match.device_id}
                                          device={deviceById.get(rule.match.device_id) ?? null}
                                          size="xs"
                                          c="dimmed"
                                          suffix={`.${rule.match.action}`}
                                        />
                                      </Text>
                                      <Text size="xs" c="dimmed">
                                        Telemetry bindings: {rule.telemetry.length}
                                      </Text>
                                      {rule.on_block?.code && (
                                        <Text size="xs" c="dimmed">
                                          On block: {rule.on_block.code}
                                        </Text>
                                      )}
                                    </Stack>
                                    <Switch
                                      checked={Boolean(rule.enabled)}
                                      disabled={busy || loading || !ruleId || !interceptorId}
                                      onChange={(event) => {
                                        void onToggleInterlockRule(
                                          processId,
                                          interceptorId,
                                          ruleId,
                                          event.currentTarget.checked
                                        );
                                      }}
                                    />
                                  </Group>
                                </Card>
                              );
                            })}
                          </Stack>
                        </Card>
                      );
                    })}
                  </Stack>
                )}
                {followerRules.length === 0 &&
                  interceptors.length === 0 &&
                  !loading &&
                  !error && (
                    <Text size="xs" c="dimmed">
                      No interlock rule data available yet.
                    </Text>
                  )}
              </Stack>
            </Card>
          );
        })}
      </Stack>
    </Modal>
  );
}
