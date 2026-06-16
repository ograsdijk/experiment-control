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
import type { ReactNode } from "react";
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
  panelOnly?: boolean;
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

type ConditionNode =
  | { kind: "always"; value: unknown }
  | {
      kind: "comparison";
      op: "eq" | "ne" | "gt" | "ge" | "lt" | "le" | "abs_lt";
      left: unknown;
      right: unknown;
    }
  | { kind: "group"; op: "and" | "or"; items: ConditionNode[] }
  | { kind: "not"; item: ConditionNode }
  | { kind: "leaf"; value: unknown };

export type ConditionTelemetryBinding = {
  as: string;
  device_id: string;
  signal: string;
  max_age_s: number;
};

const TEMPLATE_TOKEN_RE = /\$\{([^}]+)\}/g;
const SYMBOL_TOKEN_RE = /[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*/g;
const CONDITION_RESERVED = new Set([
  "and",
  "or",
  "not",
  "true",
  "false",
  "none",
  "len",
  "min",
  "max",
  "abs",
  "round",
  "int",
  "float",
  "bool",
  "str",
]);

function formatInterlockConditionRaw(condition: unknown): string {
  if (condition === undefined) {
    return "n/a";
  }
  if (typeof condition === "string") {
    return condition;
  }
  try {
    const rendered = JSON.stringify(condition, null, 2);
    return rendered ?? String(condition);
  } catch {
    return String(condition);
  }
}

function parseConditionNode(condition: unknown): ConditionNode {
  if (
    condition === null ||
    condition === undefined ||
    typeof condition === "string" ||
    typeof condition === "number" ||
    typeof condition === "boolean"
  ) {
    return { kind: "leaf", value: condition };
  }
  if (Array.isArray(condition)) {
    return { kind: "leaf", value: condition };
  }
  if (typeof condition !== "object") {
    return { kind: "leaf", value: condition };
  }
  const obj = condition as Record<string, unknown>;
  const keys = Object.keys(obj);
  if (keys.length !== 1) {
    return { kind: "leaf", value: condition };
  }
  if ("always" in obj) {
    return { kind: "always", value: obj.always };
  }
  for (const op of ["eq", "ne", "gt", "ge", "lt", "le", "abs_lt"] as const) {
    if (!(op in obj)) {
      continue;
    }
    const pair = Array.isArray(obj[op]) ? (obj[op] as unknown[]) : [];
    return {
      kind: "comparison",
      op,
      left: pair.length > 0 ? pair[0] : undefined,
      right: pair.length > 1 ? pair[1] : undefined,
    };
  }
  if ("and" in obj) {
    const items = Array.isArray(obj.and) ? obj.and : [];
    return {
      kind: "group",
      op: "and",
      items: items.map((item) => parseConditionNode(item)),
    };
  }
  if ("or" in obj) {
    const items = Array.isArray(obj.or) ? obj.or : [];
    return {
      kind: "group",
      op: "or",
      items: items.map((item) => parseConditionNode(item)),
    };
  }
  if ("not" in obj) {
    return { kind: "not", item: parseConditionNode(obj.not) };
  }
  return { kind: "leaf", value: condition };
}

function formatConditionValue(value: unknown): string {
  if (value === undefined) {
    return "n/a";
  }
  if (typeof value === "string") {
    return value;
  }
  if (
    value === null ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function extractSymbolsFromTemplateExpression(expr: string): string[] {
  const symbols: string[] = [];
  for (const match of expr.matchAll(SYMBOL_TOKEN_RE)) {
    const token = String(match[0] ?? "").trim();
    if (!token) {
      continue;
    }
    const root = token.split(".", 1)[0]?.toLowerCase() ?? "";
    if (CONDITION_RESERVED.has(root)) {
      continue;
    }
    symbols.push(token);
  }
  return symbols;
}

function rewriteSymbolToken(
  token: string,
  telemetryByAlias: Map<string, ConditionTelemetryBinding>,
  commandDeviceId?: string,
  commandAction?: string
): string {
  const trimmed = String(token ?? "").trim();
  const commandDevice = String(commandDeviceId ?? "").trim() || "*";
  const action = String(commandAction ?? "").trim() || "*";
  const commandTarget = `${commandDevice}.${action}`;
  if (!trimmed) {
    return trimmed;
  }
  if (trimmed === "params") {
    return `${commandTarget}(params)`;
  }
  if (trimmed === "command.params") {
    return `${commandTarget}(params)`;
  }
  if (trimmed.startsWith("params.")) {
    const param = trimmed.slice("params.".length);
    return `${commandTarget}(${param || "param"})`;
  }
  if (trimmed.startsWith("command.params.")) {
    const param = trimmed.slice("command.params.".length);
    return `${commandTarget}(${param || "param"})`;
  }
  if (trimmed === "device_id") {
    return commandDevice;
  }
  if (trimmed === "command.device_id") {
    return commandDevice;
  }
  if (trimmed === "action") {
    return commandTarget;
  }
  if (trimmed === "command.action") {
    return commandTarget;
  }
  const parts = trimmed.split(".");
  const alias = parts[0] ?? "";
  const binding = telemetryByAlias.get(alias);
  if (!binding) {
    return trimmed;
  }
  const tailParts = parts.slice(1);
  const normalizedTailParts =
    tailParts.length === 1 && tailParts[0] === "value" ? [] : tailParts;
  const tail = normalizedTailParts.length > 0 ? `.${normalizedTailParts.join(".")}` : "";
  return `${binding.device_id}.${binding.signal}${tail}`;
}

function rewriteExpressionText(
  text: string,
  telemetryByAlias: Map<string, ConditionTelemetryBinding>,
  commandDeviceId?: string,
  commandAction?: string
): string {
  return text.replace(SYMBOL_TOKEN_RE, (rawToken) => {
    const root = String(rawToken ?? "")
      .split(".", 1)[0]
      ?.toLowerCase();
    if (root && CONDITION_RESERVED.has(root)) {
      return rawToken;
    }
    return rewriteSymbolToken(
      rawToken,
      telemetryByAlias,
      commandDeviceId,
      commandAction
    );
  });
}

function rewriteConditionString(
  value: string,
  telemetryByAlias: Map<string, ConditionTelemetryBinding>,
  commandDeviceId?: string,
  commandAction?: string
): string {
  const wholeTemplateMatch = value.match(/^\$\{([^}]+)\}$/);
  if (wholeTemplateMatch) {
    return rewriteExpressionText(
      String(wholeTemplateMatch[1] ?? ""),
      telemetryByAlias,
      commandDeviceId,
      commandAction
    );
  }
  if (value.includes("${")) {
    return value.replace(TEMPLATE_TOKEN_RE, (_full, exprRaw) => {
      const expr = String(exprRaw ?? "");
      return rewriteExpressionText(
        expr,
        telemetryByAlias,
        commandDeviceId,
        commandAction
      );
    });
  }
  return rewriteExpressionText(
    value,
    telemetryByAlias,
    commandDeviceId,
    commandAction
  );
}

export function InterlockConditionView({
  condition,
  telemetry,
  commandDeviceId,
  commandAction,
}: {
  condition: unknown;
  telemetry?: ReadonlyArray<ConditionTelemetryBinding>;
  commandDeviceId?: string;
  commandAction?: string;
}) {
  const [showRaw, setShowRaw] = useState(false);
  const node = useMemo(() => parseConditionNode(condition), [condition]);
  const raw = useMemo(
    () => formatInterlockConditionRaw(condition),
    [condition]
  );
  const telemetryByAlias = useMemo(() => {
    const byAlias = new Map<string, ConditionTelemetryBinding>();
    for (const binding of telemetry ?? []) {
      const alias = String(binding.as ?? "").trim();
      if (!alias || byAlias.has(alias)) {
        continue;
      }
      byAlias.set(alias, binding);
    }
    return byAlias;
  }, [telemetry]);

  const resolveRenderedValue = (value: unknown): string => {
    const rawText = formatConditionValue(value);
    return typeof value === "string"
      ? rewriteConditionString(
          value,
          telemetryByAlias,
          commandDeviceId,
          commandAction
        )
      : rawText;
  };

  const renderValue = (value: unknown) => {
    const text = resolveRenderedValue(value);
    const looksTemplate = typeof value === "string" && value.includes("${");
    return (
      <Text
        size="xs"
        style={{
          fontFamily:
            "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
          wordBreak: "break-word",
        }}
        c={looksTemplate ? "teal" : undefined}
      >
        {text}
      </Text>
    );
  };

  const withCommandParamArrow = (symbolText: string, valueText: string): string => {
    const match = symbolText.match(/^(.*)\(([^()]+)\)$/);
    if (!match) {
      return symbolText;
    }
    const prefix = String(match[1] ?? "");
    const param = String(match[2] ?? "").trim() || "param";
    return `${prefix}(${param} -> ${valueText})`;
  };

  const isComparableLiteral = (value: unknown): boolean =>
    value === null ||
    typeof value === "boolean" ||
    typeof value === "number" ||
    typeof value === "string";

  const valueColor = (value: unknown): string | undefined => {
    if (typeof value === "boolean") {
      return value ? "teal" : "red";
    }
    if (typeof value === "string" && value.includes("${")) {
      return "teal";
    }
    return undefined;
  };

  const renderNode = (current: ConditionNode): ReactNode => {
    if (current.kind === "always") {
      return (
        <Group gap="xs" wrap="wrap">
          <Badge size="xs" variant="light" color="gray">
            Always
          </Badge>
          {renderValue(current.value)}
        </Group>
      );
    }
    if (current.kind === "comparison") {
      const symbolByOp: Record<Extract<ConditionNode, { kind: "comparison" }>["op"], string> = {
        eq: "=",
        ne: "!=",
        gt: ">",
        ge: ">=",
        lt: "<",
        le: "<=",
        abs_lt: "|x| <",
      };
      const colorByOp: Record<Extract<ConditionNode, { kind: "comparison" }>["op"], string> = {
        eq: "blue",
        ne: "blue",
        gt: "violet",
        ge: "violet",
        lt: "violet",
        le: "violet",
        abs_lt: "orange",
      };
      const leftText = resolveRenderedValue(current.left);
      const rightText = resolveRenderedValue(current.right);
      const leftIsCommandParam = /\.[^.]+\([^)]+\)$/.test(leftText);
      const rightIsCommandParam = /\.[^.]+\([^)]+\)$/.test(rightText);
      const canArrow =
        (current.op === "eq" || current.op === "ne") &&
        ((leftIsCommandParam && isComparableLiteral(current.right)) ||
          (rightIsCommandParam && isComparableLiteral(current.left)));
      const leftDisplay = canArrow && leftIsCommandParam
        ? withCommandParamArrow(leftText, rightText)
        : leftText;
      const rightDisplay = canArrow && rightIsCommandParam
        ? withCommandParamArrow(rightText, leftText)
        : rightText;
      const simplifyEq = current.op === "eq" && canArrow;
      const simplifiedText =
        simplifyEq && leftIsCommandParam
          ? leftDisplay
          : simplifyEq && rightIsCommandParam
            ? rightDisplay
            : null;
      const simplifiedColor =
        simplifyEq && leftIsCommandParam
          ? valueColor(current.right)
          : simplifyEq && rightIsCommandParam
            ? valueColor(current.left)
            : undefined;
      if (simplifiedText) {
        return (
          <Group gap="xs" wrap="wrap">
            <Text
              size="xs"
              c={simplifiedColor}
              style={{
                fontFamily:
                  "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                wordBreak: "break-word",
              }}
            >
              {simplifiedText}
            </Text>
          </Group>
        );
      }
      return (
        <Group gap="xs" wrap="wrap">
          <Text
            size="xs"
            c={valueColor(current.left)}
            style={{
              fontFamily:
                "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
              wordBreak: "break-word",
            }}
          >
            {leftDisplay}
          </Text>
          <Badge size="xs" variant="outline" color={colorByOp[current.op]}>
            {symbolByOp[current.op]}
          </Badge>
          <Text
            size="xs"
            c={valueColor(current.right)}
            style={{
              fontFamily:
                "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
              wordBreak: "break-word",
            }}
          >
            {rightDisplay}
          </Text>
        </Group>
      );
    }
    if (current.kind === "group") {
      return (
        <Stack
          gap={4}
          style={{
            border: "1px solid var(--card-border)",
            borderRadius: "0.375rem",
            padding: "0.35rem 0.45rem",
          }}
        >
          <Group gap="xs" wrap="wrap">
            <Badge
              size="xs"
              variant="light"
              color={current.op === "and" ? "teal" : "blue"}
            >
              {current.op === "and" ? "ALL" : "ANY"}
            </Badge>
            <Text size="xs" c="dimmed">
              {current.op === "and"
                ? "All conditions must pass"
                : "Any condition can pass"}
            </Text>
          </Group>
          {current.items.length > 0 ? (
            <Stack gap={3}>
              {current.items.map((item, idx) => (
                <Group key={`condition-item:${idx}`} gap="xs" wrap="nowrap" align="flex-start">
                  <Badge size="xs" variant="outline" color="gray">
                    {idx + 1}
                  </Badge>
                  <Stack gap={2} style={{ flex: 1 }}>
                    {renderNode(item)}
                  </Stack>
                </Group>
              ))}
            </Stack>
          ) : (
            <Text size="xs" c="dimmed">
              (empty)
            </Text>
          )}
        </Stack>
      );
    }
    if (current.kind === "not") {
      return (
        <Stack gap={4}>
          <Badge size="xs" variant="light" color="orange" style={{ width: "fit-content" }}>
            NOT
          </Badge>
          <Stack gap={2} style={{ paddingLeft: "0.25rem" }}>
            {renderNode(current.item)}
          </Stack>
        </Stack>
      );
    }
    return renderValue(current.value);
  };

  return (
    <Stack gap={4}>
      {renderNode(node)}
      <Button
        size="compact-xs"
        variant="subtle"
        color="gray"
        style={{ alignSelf: "flex-start" }}
        onClick={() => setShowRaw((prev) => !prev)}
      >
        {showRaw ? "Hide raw" : "Show raw"}
      </Button>
      {showRaw && (
        <Text
          size="xs"
          style={{
            fontFamily:
              "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {raw}
        </Text>
      )}
    </Stack>
  );
}

export function InterlocksModal({
  opened,
  onClose,
  panelOnly = false,
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
        condition: unknown;
        telemetry: ConditionTelemetryBinding[];
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
        condition: unknown;
        telemetry: ConditionTelemetryBinding[];
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
              condition: rule.condition,
              telemetry: Array.isArray(rule.telemetry)
                ? (rule.telemetry as ConditionTelemetryBinding[])
                : [],
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

  const panel = (
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
                          {entry.matchedInterlockRules.length > 0 && (
                            <Stack gap={4}>
                              {entry.matchedInterlockRules.map((rule, ruleIdx) => (
                                <Card
                                  key={`effective-interlock-rule:${entry.route.process_id}:${rule.interceptorId}:${rule.ruleId || rule.name}:${ruleIdx}`}
                                  p={6}
                                  radius="sm"
                                  style={{ border: "1px solid var(--card-border)" }}
                                >
                                  <Stack gap={2}>
                                    <Group gap="xs" wrap="wrap">
                                      <Text size="xs" fw={600}>
                                        {rule.name}
                                      </Text>
                                      <Badge variant="outline" color="gray">
                                        {rule.interceptorId || "interlock"}
                                      </Badge>
                                      <Badge
                                        variant="outline"
                                        color={rule.hasAllowTransform ? "grape" : "gray"}
                                      >
                                        {rule.hasAllowTransform
                                          ? "Transforms params"
                                          : "No transform"}
                                      </Badge>
                                    </Group>
                                    <Text size="xs" c="dimmed">
                                      Condition:
                                    </Text>
                                    <InterlockConditionView
                                      condition={rule.condition}
                                      telemetry={rule.telemetry}
                                      commandDeviceId={chainDeviceId.trim()}
                                      commandAction={chainAction.trim()}
                                    />
                                  </Stack>
                                </Card>
                              ))}
                            </Stack>
                          )}
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
                                      <Text size="xs" c="dimmed">
                                        Condition:
                                      </Text>
                                      <InterlockConditionView
                                        condition={rule.condition}
                                        telemetry={rule.telemetry}
                                        commandDeviceId={rule.match.device_id}
                                        commandAction={rule.match.action}
                                      />
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
  );

  if (panelOnly) {
    return panel;
  }

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Interlocks"
      size="clamp(56rem, 92vw, 96rem)"
      centered
      zIndex={420}
    >
      {panel}
    </Modal>
  );
}
