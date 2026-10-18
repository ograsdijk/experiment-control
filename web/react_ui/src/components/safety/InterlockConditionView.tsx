import { Badge, Button, Group, Stack, Text } from "@mantine/core";
import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { ConditionEvaluationTrace } from "../../types";

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
  evaluation,
  resultMode = "allow",
}: {
  condition: unknown;
  telemetry?: ReadonlyArray<ConditionTelemetryBinding>;
  commandDeviceId?: string;
  commandAction?: string;
  evaluation?: ConditionEvaluationTrace | null;
  resultMode?: "allow" | "trigger";
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

  const renderEvaluationResult = (
    currentEvaluation: ConditionEvaluationTrace | null | undefined,
    group = false
  ) => {
    if (!currentEvaluation) {
      return null;
    }
    if (currentEvaluation.result == null) {
      return (
        <Badge size="xs" variant="light" color="yellow">
          Unknown
        </Badge>
      );
    }
    if (resultMode === "trigger") {
      return (
        <Badge
          size="xs"
          variant="light"
          color={currentEvaluation.result ? "red" : "teal"}
        >
          {currentEvaluation.result
            ? group
              ? "Triggered"
              : "Met"
            : group
              ? "Not triggered"
              : "Clear"}
        </Badge>
      );
    }
    return (
      <Badge
        size="xs"
        variant="light"
        color={currentEvaluation.result ? "teal" : "red"}
      >
        {currentEvaluation.result ? "Pass" : "Fail"}
      </Badge>
    );
  };

  const renderResolvedValue = (
    original: unknown,
    rendered: string,
    resolved: unknown,
    hasResolved: boolean
  ) => {
    const resolvedText = formatConditionValue(resolved);
    const display =
      hasResolved && resolvedText !== formatConditionValue(original)
        ? `${rendered} = ${resolvedText}`
        : rendered;
    return (
      <Text
        size="xs"
        style={{
          fontFamily:
            "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
          wordBreak: "break-word",
        }}
      >
        {display}
      </Text>
    );
  };

  const renderNode = (
    current: ConditionNode,
    currentEvaluation?: ConditionEvaluationTrace | null
  ): ReactNode => {
    if (current.kind === "always") {
      return (
        <Group gap="xs" wrap="wrap">
          <Badge size="xs" variant="light" color="gray">
            Always
          </Badge>
          {renderValue(current.value)}
          {renderEvaluationResult(currentEvaluation)}
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
            {renderEvaluationResult(currentEvaluation)}
          </Group>
        );
      }
      const hasResolvedLeft = Boolean(
        currentEvaluation && Object.prototype.hasOwnProperty.call(currentEvaluation, "left")
      );
      const hasResolvedRight = Boolean(
        currentEvaluation && Object.prototype.hasOwnProperty.call(currentEvaluation, "right")
      );
      return (
        <Group gap="xs" wrap="wrap">
          {renderResolvedValue(
            current.left,
            leftDisplay,
            currentEvaluation?.left,
            hasResolvedLeft
          )}
          <Badge size="xs" variant="outline" color={colorByOp[current.op]}>
            {symbolByOp[current.op]}
          </Badge>
          {renderResolvedValue(
            current.right,
            rightDisplay,
            currentEvaluation?.right,
            hasResolvedRight
          )}
          {renderEvaluationResult(currentEvaluation)}
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
            {renderEvaluationResult(currentEvaluation, true)}
          </Group>
          {current.items.length > 0 ? (
            <Stack gap={3}>
              {current.items.map((item, idx) => (
                <Group key={`condition-item:${idx}`} gap="xs" wrap="nowrap" align="flex-start">
                  <Badge size="xs" variant="outline" color="gray">
                    {idx + 1}
                  </Badge>
                  <Stack gap={2} style={{ flex: 1 }}>
                    {renderNode(item, currentEvaluation?.children?.[idx])}
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
            {renderNode(current.item, currentEvaluation?.children?.[0])}
          </Stack>
          {renderEvaluationResult(currentEvaluation)}
        </Stack>
      );
    }
    return (
      <Group gap="xs" wrap="wrap">
        {renderValue(current.value)}
        {renderEvaluationResult(currentEvaluation)}
      </Group>
    );
  };

  return (
    <Stack gap={4}>
      {renderNode(node, evaluation)}
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
