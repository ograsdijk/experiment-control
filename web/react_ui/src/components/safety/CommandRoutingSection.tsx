import { Badge, Card, Collapse, Group, Select, Stack, Text, UnstyledButton } from "@mantine/core";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";

import type { InterlockRuleRow } from "../../features/safety/safetyRows";
import { processStateColor } from "../../features/runtime/helpers";
import type { CommandInterceptorRoute, DeviceStatus, ProcessStatus } from "../../types";
import { DeviceNameInline } from "../DeviceNameInline";

const OPEN_KEY = "ecui.safety.routingOpen";

function loadOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) === "1";
  } catch {
    return false;
  }
}

function wildcardMatch(pattern: string, value: string): boolean {
  return pattern === "*" || pattern === value;
}

type Props = {
  routes: CommandInterceptorRoute[];
  loading: boolean;
  error: string | null;
  rows: InterlockRuleRow[];
  processById: Map<string, ProcessStatus>;
  deviceById: Map<string, DeviceStatus>;
  onSelectRule: (key: string) => void;
};

/**
 * Diagnostic view of command interception: the registered route order and,
 * for a chosen device.action, which interceptors fire and which of their
 * active rules apply. Collapsed by default; the open state is remembered.
 */
export function CommandRoutingSection({
  routes,
  loading,
  error,
  rows,
  processById,
  deviceById,
  onSelectRule,
}: Props) {
  const [open, setOpen] = useState(loadOpen);
  const [chainDeviceId, setChainDeviceId] = useState("");
  const [chainAction, setChainAction] = useState("");

  useEffect(() => {
    try {
      localStorage.setItem(OPEN_KEY, open ? "1" : "0");
    } catch {
      // Storage unavailable; state stays in memory.
    }
  }, [open]);

  const deviceOptions = useMemo(() => {
    const ids = new Set<string>();
    for (const route of routes) {
      const id = String(route.device_id ?? "").trim();
      if (id) {
        ids.add(id);
      }
    }
    return [...ids]
      .sort((a, b) => a.localeCompare(b))
      .map((id) => ({
        value: id,
        label:
          id === "*"
            ? "* (wildcard)"
            : `${
                deviceById.get(id)?.is_remote || deviceById.get(id)?.source_kind === "federated"
                  ? "⇄ "
                  : ""
              }${id}`,
      }));
  }, [routes, deviceById]);

  const actionOptions = useMemo(() => {
    const device = chainDeviceId.trim();
    if (!device) {
      return [];
    }
    const actions = new Set<string>();
    for (const route of routes) {
      const routeDevice = String(route.device_id ?? "").trim();
      const routeAction = String(route.action ?? "").trim();
      if (!routeAction) {
        continue;
      }
      if (device === "*" ? routeDevice !== "*" : routeDevice !== device && routeDevice !== "*") {
        continue;
      }
      actions.add(routeAction);
    }
    return [...actions]
      .sort((a, b) => a.localeCompare(b))
      .map((action) => ({ value: action, label: action === "*" ? "* (wildcard)" : action }));
  }, [routes, chainDeviceId]);

  useEffect(() => {
    if (chainAction && !actionOptions.some((option) => option.value === chainAction)) {
      setChainAction("");
    }
  }, [chainAction, actionOptions]);

  // Wildcard matching + one entry per process, in registered order.
  const effectiveChain = useMemo(() => {
    const device = chainDeviceId.trim();
    const action = chainAction.trim();
    if (!device || !action) {
      return [];
    }
    const seen = new Set<string>();
    const out: Array<{ route: CommandInterceptorRoute; rules: InterlockRuleRow[] }> = [];
    for (const route of [...routes].sort((a, b) => a.order - b.order)) {
      if (!wildcardMatch(route.device_id, device) || !wildcardMatch(route.action, action)) {
        continue;
      }
      if (seen.has(route.process_id)) {
        continue;
      }
      seen.add(route.process_id);
      out.push({
        route,
        rules: rows.filter(
          (row) =>
            row.processId === route.process_id &&
            row.active &&
            wildcardMatch(row.matchDeviceId, device) &&
            wildcardMatch(row.matchAction, action)
        ),
      });
    }
    return out;
  }, [routes, rows, chainDeviceId, chainAction]);

  const routeLabel = (route: CommandInterceptorRoute) => (
    <DeviceNameInline
      deviceId={route.device_id}
      device={deviceById.get(route.device_id) ?? null}
      size="xs"
      c="dimmed"
      suffix={`.${route.action}`}
    />
  );

  return (
    <Card withBorder radius="md" p="xs">
      <UnstyledButton
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        style={{ width: "100%" }}
      >
        <Group gap="xs" wrap="nowrap">
          {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
          <Text size="sm" fw={600}>
            Command routing
          </Text>
          <Text size="xs" c={error ? "red" : "dimmed"}>
            {loading
              ? "· loading"
              : error
                ? "· routes unavailable"
                : `· ${routes.length} route${routes.length === 1 ? "" : "s"}`}
          </Text>
        </Group>
      </UnstyledButton>
      <Collapse in={open}>
        <Stack gap="sm" mt="xs">
          {error ? (
            <Text size="xs" c="red">
              {error}
            </Text>
          ) : null}
          <Stack gap={4}>
            <Text size="xs" c="dimmed">
              Registered order (manager.interceptors.list)
            </Text>
            {routes.length === 0 ? (
              <Text size="xs" c="dimmed">
                No registered interceptor routes.
              </Text>
            ) : (
              [...routes]
                .sort((a, b) => a.order - b.order)
                .map((route) => {
                  const process = processById.get(route.process_id) ?? null;
                  return (
                    <Group
                      key={`${route.order}:${route.process_id}:${route.device_id}:${route.action}`}
                      gap="xs"
                      wrap="wrap"
                    >
                      <Badge size="xs" variant="outline" color="gray">
                        #{route.order}
                      </Badge>
                      <Badge
                        size="xs"
                        variant="light"
                        color={process ? processStateColor(process.state) : "gray"}
                      >
                        {route.process_id}
                      </Badge>
                      <Text size="xs" c="dimmed" component="span">
                        {routeLabel(route)}
                      </Text>
                    </Group>
                  );
                })
            )}
          </Stack>
          <Stack gap={4}>
            <Text size="xs" c="dimmed">
              Resolve the effective chain for a command (wildcards matched, one entry per
              process)
            </Text>
            <Group gap="xs" align="flex-end" wrap="wrap">
              <Select
                size="xs"
                label="Device"
                placeholder={deviceOptions.length > 0 ? "Select device" : "No chain devices"}
                data={deviceOptions}
                value={chainDeviceId || null}
                onChange={(value) => setChainDeviceId(String(value ?? ""))}
                w={180}
                clearable
                searchable
              />
              <Select
                size="xs"
                label="Action"
                placeholder={
                  !chainDeviceId
                    ? "Select device first"
                    : actionOptions.length > 0
                      ? "Select action"
                      : "No chain actions"
                }
                data={actionOptions}
                value={chainAction || null}
                onChange={(value) => setChainAction(String(value ?? ""))}
                w={260}
                clearable
                searchable
                disabled={!chainDeviceId}
              />
            </Group>
            {chainDeviceId && chainAction ? (
              effectiveChain.length === 0 ? (
                <Text size="xs" c="dimmed">
                  No interceptors match {chainDeviceId}.{chainAction}.
                </Text>
              ) : (
                <Stack gap={6}>
                  {effectiveChain.map(({ route, rules }, idx) => {
                    const process = processById.get(route.process_id) ?? null;
                    return (
                      <Stack key={`${route.order}:${route.process_id}`} gap={2}>
                        <Group gap="xs" wrap="wrap">
                          <Badge size="xs" variant="outline" color="teal">
                            {idx + 1}
                          </Badge>
                          <Badge
                            size="xs"
                            variant="light"
                            color={process ? processStateColor(process.state) : "gray"}
                          >
                            {route.process_id}
                          </Badge>
                          <Text size="xs" c="dimmed" component="span">
                            route #{route.order} ({routeLabel(route)})
                          </Text>
                        </Group>
                        <Group gap={6} wrap="wrap" pl="lg">
                          {rules.length === 0 ? (
                            <Text size="xs" c="dimmed">
                              no active rules match
                            </Text>
                          ) : (
                            rules.map((row) => (
                              <UnstyledButton
                                key={row.key}
                                className="safety-rule-link"
                                onClick={() => onSelectRule(row.key)}
                                title="Show this rule"
                              >
                                <Text size="xs" component="span">
                                  {row.name}
                                </Text>
                                <Text size="xs" c="dimmed" component="span">
                                  {" "}
                                  · {row.kindLabel}
                                </Text>
                              </UnstyledButton>
                            ))
                          )}
                        </Group>
                      </Stack>
                    );
                  })}
                </Stack>
              )
            ) : null}
          </Stack>
        </Stack>
      </Collapse>
    </Card>
  );
}
