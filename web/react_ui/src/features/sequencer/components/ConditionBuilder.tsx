import {
  ActionIcon,
  Button,
  Card,
  Group,
  Menu,
  SegmentedControl,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import {
  IconChevronDown,
  IconChevronRight,
  IconCopy,
  IconPlus,
  IconTrash,
} from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";
import {
  CONDITION_COMPARE_OPERATORS,
  conditionAstToEntries,
  defaultConditionAst,
  parseConditionEntries,
  validateConditionAst,
  type ConditionAst,
  type ConditionAstIssue,
  type ConditionCompareOp,
  type ConditionLogicalOp,
} from "../condition_ast";
import type { SequencerOutlineMetadataEntry } from "../types";
import { KeyValueChipList } from "./KeyValueChipList";

type ConditionTemplate = {
  id: string;
  label: string;
  ast: ConditionAst;
};

const CONDITION_TEMPLATES: ConditionTemplate[] = [
  {
    id: "gt",
    label: "Greater than",
    ast: {
      kind: "compare",
      op: "gt",
      left: "${sample_reduced}",
      right: "0.0",
    },
  },
  {
    id: "lt",
    label: "Less than",
    ast: {
      kind: "compare",
      op: "lt",
      left: "${sample_reduced}",
      right: "0.0",
    },
  },
  {
    id: "abs_lt",
    label: "Absolute tolerance",
    ast: {
      kind: "compare",
      op: "abs_lt",
      left: "${sample_reduced - target}",
      right: "0.1",
    },
  },
  {
    id: "not_gt",
    label: "NOT greater-than",
    ast: {
      kind: "not",
      item: {
        kind: "compare",
        op: "gt",
        left: "${sample_reduced}",
        right: "0.0",
      },
    },
  },
  {
    id: "and_band",
    label: "Band (AND)",
    ast: {
      kind: "and",
      items: [
        { kind: "compare", op: "gt", left: "${sample_reduced}", right: "0.0" },
        { kind: "compare", op: "lt", left: "${sample_reduced}", right: "10.0" },
      ],
    },
  },
  {
    id: "or_union",
    label: "Union (OR)",
    ast: {
      kind: "or",
      items: [
        { kind: "compare", op: "lt", left: "${sample_reduced}", right: "-1.0" },
        { kind: "compare", op: "gt", left: "${sample_reduced}", right: "1.0" },
      ],
    },
  },
];

type BuilderOperator = ConditionCompareOp | ConditionLogicalOp;
type EditableNode = Exclude<ConditionAst, { kind: "empty" } | { kind: "raw" }>;

const OP_LABEL: Record<BuilderOperator, string> = {
  eq: "eq",
  ne: "ne",
  gt: "gt",
  ge: "ge",
  lt: "lt",
  le: "le",
  abs_lt: "abs_lt",
  and: "and",
  or: "or",
  not: "not",
};

const OPERATOR_OPTIONS: Array<{ value: BuilderOperator; label: string }> = [
  ...CONDITION_COMPARE_OPERATORS.map((op) => ({
    value: op,
    label: OP_LABEL[op],
  })),
  { value: "and", label: OP_LABEL.and },
  { value: "or", label: OP_LABEL.or },
  { value: "not", label: OP_LABEL.not },
];

const DEPTH_ACCENTS = [
  "var(--mantine-color-blue-5)",
  "var(--mantine-color-teal-5)",
  "var(--mantine-color-orange-5)",
  "var(--mantine-color-violet-5)",
] as const;

function depthAccent(depth: number): string {
  return DEPTH_ACCENTS[Math.max(0, Math.min(depth, DEPTH_ACCENTS.length - 1))];
}

function normalizeNode(node: ConditionAst): EditableNode {
  if (node.kind === "empty" || node.kind === "raw") {
    return defaultConditionAst();
  }
  return node;
}

function defaultForOperator(op: BuilderOperator): EditableNode {
  if (op === "and") {
    return { kind: "and", items: [defaultConditionAst()] };
  }
  if (op === "or") {
    return { kind: "or", items: [defaultConditionAst()] };
  }
  if (op === "not") {
    return { kind: "not", item: defaultConditionAst() };
  }
  return defaultConditionAst(op);
}

function operatorForNode(node: EditableNode): BuilderOperator {
  if (node.kind === "compare") {
    return node.op;
  }
  return node.kind;
}

function cloneNode(node: EditableNode): EditableNode {
  if (node.kind === "compare") {
    return { ...node };
  }
  if (node.kind === "not") {
    return { kind: "not", item: cloneNode(normalizeNode(node.item)) };
  }
  return {
    kind: node.kind,
    items: node.items.map((item) => cloneNode(normalizeNode(item))),
  };
}

function pathMatches(path: string, issuePath: string): boolean {
  return (
    issuePath === path ||
    issuePath.startsWith(`${path}[`) ||
    issuePath.startsWith(`${path}.`)
  );
}

function ConditionNodeEditor({
  node,
  onChange,
  onDelete,
  onDuplicate,
  depth,
  path,
  collapsedByPath,
  onToggleCollapsed,
  issues,
}: {
  node: EditableNode;
  onChange: (next: EditableNode) => void;
  onDelete?: () => void;
  onDuplicate?: () => void;
  depth: number;
  path: string;
  collapsedByPath: Record<string, boolean>;
  onToggleCollapsed: (path: string) => void;
  issues: ConditionAstIssue[];
}) {
  const operator = operatorForNode(node);
  const accent = depthAccent(depth);
  const childAccent = depthAccent(depth + 1);
  const isCollapsible =
    node.kind === "and" || node.kind === "or" || node.kind === "not";
  const collapsed = isCollapsible ? Boolean(collapsedByPath[path]) : false;
  const directIssues = issues.filter((issue) => issue.path === path);
  const errorCount = directIssues.filter((issue) => issue.severity === "error").length;
  const warningCount = directIssues.filter((issue) => issue.severity === "warning").length;

  return (
    <Card
      radius="sm"
      p="xs"
      style={{
        border: "1px solid var(--card-border)",
        borderLeft: `3px solid ${accent}`,
        background: "rgba(148, 163, 184, 0.04)",
        marginLeft: depth > 0 ? 6 : 0,
      }}
    >
      <Stack gap={8}>
        <Group align="flex-end" wrap="nowrap">
          {isCollapsible ? (
            <ActionIcon
              size="sm"
              variant="subtle"
              color="gray"
              aria-label={collapsed ? "Expand clause" : "Collapse clause"}
              onClick={() => onToggleCollapsed(path)}
              style={{ marginBottom: 2 }}
            >
              {collapsed ? <IconChevronRight size={14} /> : <IconChevronDown size={14} />}
            </ActionIcon>
          ) : null}
          <Select
            size="xs"
            label="Operator"
            data={OPERATOR_OPTIONS}
            value={operator}
            allowDeselect={false}
            searchable={false}
            comboboxProps={{ withinPortal: false }}
            style={{ flex: 1 }}
            onChange={(value) => {
              const next = value as BuilderOperator | null;
              if (!next) {
                return;
              }
              onChange(defaultForOperator(next));
            }}
          />
          {onDuplicate ? (
            <ActionIcon
              size="sm"
              variant="subtle"
              color="gray"
              aria-label="Duplicate clause"
              onClick={onDuplicate}
            >
              <IconCopy size={14} />
            </ActionIcon>
          ) : null}
          {onDelete ? (
            <ActionIcon
              size="sm"
              variant="subtle"
              color="red"
              aria-label="Remove clause"
              onClick={onDelete}
            >
              <IconTrash size={14} />
            </ActionIcon>
          ) : null}
        </Group>

        {errorCount > 0 || warningCount > 0 ? (
          <Stack gap={2}>
            {directIssues.map((issue, index) => (
              <Text key={`${path}-issue-${index}`} size="xs" c={issue.severity === "error" ? "red" : "orange"}>
                {issue.message}
              </Text>
            ))}
          </Stack>
        ) : null}

        {collapsed ? (
          <Text size="xs" c="dimmed">
            {node.kind === "and" || node.kind === "or"
              ? `${node.items.length} clause${node.items.length === 1 ? "" : "s"}`
              : "1 clause"}
          </Text>
        ) : null}

        {!collapsed && node.kind === "compare" ? (
          <>
            <TextInput
              size="xs"
              label={node.op === "abs_lt" ? "Value expression" : "Left expression"}
              value={node.left}
              onChange={(event) =>
                onChange({
                  ...node,
                  left: event.currentTarget.value,
                })
              }
            />
            <TextInput
              size="xs"
              label={node.op === "abs_lt" ? "Tolerance" : "Right expression"}
              value={node.right}
              onChange={(event) =>
                onChange({
                  ...node,
                  right: event.currentTarget.value,
                })
              }
            />
          </>
        ) : null}

        {!collapsed && node.kind === "not" ? (
          <Stack
            gap={6}
            style={{
              paddingLeft: 8,
              borderLeft: `1px dashed ${childAccent}`,
            }}
          >
            <Text size="xs" c="dimmed" fw={600}>
              Negated clause
            </Text>
            <ConditionNodeEditor
              node={normalizeNode(node.item)}
              onChange={(nextChild) => onChange({ kind: "not", item: nextChild })}
              path={`${path}.not`}
              collapsedByPath={collapsedByPath}
              onToggleCollapsed={onToggleCollapsed}
              issues={issues.filter((issue) => pathMatches(`${path}.not`, issue.path))}
              depth={depth + 1}
            />
          </Stack>
        ) : null}

        {!collapsed && (node.kind === "and" || node.kind === "or") ? (
          <Stack
            gap={6}
            style={{
              paddingLeft: 8,
              borderLeft: `1px dashed ${childAccent}`,
            }}
          >
            <Group justify="space-between" align="center">
              <Text size="xs" c="dimmed" fw={600}>
                Clauses ({node.items.length})
              </Text>
              <Button
                size="compact-xs"
                variant="light"
                leftSection={<IconPlus size={14} />}
                onClick={() =>
                  onChange({
                    kind: node.kind,
                    items: [...node.items, defaultConditionAst()],
                  })
                }
              >
                Add clause
              </Button>
            </Group>
            {node.items.length <= 0 ? (
              <Text size="xs" c="dimmed">
                No clauses.
              </Text>
            ) : (
              <Stack gap={6}>
                {node.items.map((item, index) => {
                  const itemPath = `${path}[${index}]`;
                  return (
                    <Card
                      key={`${node.kind}-${index}`}
                      radius="sm"
                      p={6}
                      style={{
                        border: "1px dashed var(--card-border)",
                        background: "rgba(148, 163, 184, 0.02)",
                      }}
                    >
                      <Stack gap={6}>
                        <Group justify="space-between" align="center">
                          <Text size="xs" c="dimmed" fw={600}>
                            Clause {index + 1}
                          </Text>
                          <Group gap={4}>
                            <ActionIcon
                              size="sm"
                              variant="subtle"
                              color="gray"
                              aria-label="Duplicate clause"
                              onClick={() => {
                                const duplicate = cloneNode(normalizeNode(item));
                                onChange({
                                  kind: node.kind,
                                  items: [
                                    ...node.items.slice(0, index + 1),
                                    duplicate,
                                    ...node.items.slice(index + 1),
                                  ],
                                });
                              }}
                            >
                              <IconCopy size={14} />
                            </ActionIcon>
                            {node.items.length > 1 ? (
                              <ActionIcon
                                size="sm"
                                variant="subtle"
                                color="red"
                                aria-label="Remove clause"
                                onClick={() =>
                                  onChange({
                                    kind: node.kind,
                                    items: node.items.filter(
                                      (_, itemIndex) => itemIndex !== index
                                    ),
                                  })
                                }
                              >
                                <IconTrash size={14} />
                              </ActionIcon>
                            ) : null}
                          </Group>
                        </Group>
                        <ConditionNodeEditor
                          node={normalizeNode(item)}
                          depth={depth + 1}
                          path={itemPath}
                          collapsedByPath={collapsedByPath}
                          onToggleCollapsed={onToggleCollapsed}
                          issues={issues.filter((issue) => pathMatches(itemPath, issue.path))}
                          onChange={(nextChild) =>
                            onChange({
                              kind: node.kind,
                              items: node.items.map((current, itemIndex) =>
                                itemIndex === index ? nextChild : current
                              ),
                            })
                          }
                        />
                      </Stack>
                    </Card>
                  );
                })}
              </Stack>
            )}
          </Stack>
        ) : null}
      </Stack>
    </Card>
  );
}

type Props = {
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>;
  onChange: (entries: SequencerOutlineMetadataEntry[]) => void;
  title?: string;
  addLabel?: string;
  emptyLabel?: string;
  nameLabel?: string;
  valueLabel?: string;
  removeLabel?: string;
  nextNamePrefix?: string;
};

export function ConditionBuilder({
  entries,
  onChange,
  title = "Condition",
  addLabel = "Add field",
  emptyLabel = "No condition entries.",
  nameLabel = "Condition field name",
  valueLabel = "Condition field value",
  removeLabel = "Remove condition field",
  nextNamePrefix = "field",
}: Props) {
  const parsed = useMemo(() => parseConditionEntries(entries), [entries]);
  const [mode, setMode] = useState<"builder" | "raw">(
    parsed.kind === "raw" ? "raw" : "builder"
  );
  const [collapsedByPath, setCollapsedByPath] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (parsed.kind === "raw") {
      setMode("raw");
    }
  }, [parsed.kind]);

  const rootNode = normalizeNode(parsed);
  const builderIssues = useMemo(() => validateConditionAst(parsed), [parsed]);

  return (
    <Stack gap={6}>
      <Group justify="space-between" align="center">
        <Text size="xs" fw={600}>
          {title}
        </Text>
        <Group gap={6} align="center">
          <Menu withinPortal position="bottom-end" withArrow shadow="md" zIndex={1000}>
            <Menu.Target>
              <Button size="compact-xs" variant="light">
                Templates
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              {CONDITION_TEMPLATES.map((template) => (
                <Menu.Item
                  key={template.id}
                  onClick={() => {
                    onChange(conditionAstToEntries(template.ast));
                    setMode("builder");
                  }}
                >
                  {template.label}
                </Menu.Item>
              ))}
            </Menu.Dropdown>
          </Menu>
          <SegmentedControl
            size="xs"
            value={mode}
            onChange={(value) => setMode(value as "builder" | "raw")}
            data={[
              { value: "builder", label: "Builder" },
              { value: "raw", label: "Raw" },
            ]}
          />
        </Group>
      </Group>

      {mode === "builder" ? (
        <Stack gap={8}>
          {parsed.kind === "raw" ? (
            <Text size="xs" c="orange">
              Switched to raw mode automatically because this condition is not representable by
              the builder ({parsed.reason}).
            </Text>
          ) : null}
          {builderIssues.length > 0 ? (
            <Text size="xs" c="dimmed">
              {builderIssues.length} condition issue
              {builderIssues.length === 1 ? "" : "s"} detected.
            </Text>
          ) : null}
          <ConditionNodeEditor
            node={rootNode}
            onChange={(next) => onChange(conditionAstToEntries(next))}
            depth={0}
            path="root"
            collapsedByPath={collapsedByPath}
            onToggleCollapsed={(path) =>
              setCollapsedByPath((prev) => ({
                ...prev,
                [path]: !Boolean(prev[path]),
              }))
            }
            issues={builderIssues}
          />
          <Group justify="flex-end">
            <Button size="compact-xs" variant="subtle" color="gray" onClick={() => onChange([])}>
              Clear
            </Button>
          </Group>
        </Stack>
      ) : (
        <>
          {parsed.kind === "raw" ? (
            <Text size="xs" c="dimmed">
              Raw mode: {parsed.reason}.
            </Text>
          ) : null}
          <KeyValueChipList
            entries={entries}
            onChange={onChange}
            title={title}
            addLabel={addLabel}
            emptyLabel={emptyLabel}
            nameLabel={nameLabel}
            valueLabel={valueLabel}
            removeLabel={removeLabel}
            nextNamePrefix={nextNamePrefix}
          />
        </>
      )}
    </Stack>
  );
}
