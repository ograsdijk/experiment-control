import { ActionIcon, Badge, Button, Card, Group, Menu, ScrollArea, Stack, Text } from "@mantine/core";
import {
  IconArrowDown,
  IconArrowDownRight,
  IconArrowUp,
  IconChevronDown,
  IconChevronRight,
  IconCopy,
  IconPlus,
  IconTrash,
} from "@tabler/icons-react";
import {
  listChildInsertionTargets,
  type BasicSequencerStepTemplate,
  type SequencerChildContainer,
} from "../editing";
import { countStepIssues } from "../editor_helpers";
import type { SequencerStepOutlineNode } from "../types";

const STEP_TEMPLATE_OPTIONS: Array<{ kind: BasicSequencerStepTemplate; label: string }> = [
  { kind: "call", label: "Add call" },
  { kind: "sleep", label: "Add sleep" },
  { kind: "repeat", label: "Add repeat" },
  { kind: "adaptive", label: "Add adaptive" },
  { kind: "for", label: "Add for" },
  { kind: "wait_until", label: "Add wait_until" },
  { kind: "set", label: "Add set" },
  { kind: "assign", label: "Add assign" },
  { kind: "set_context", label: "Add set_context" },
  { kind: "if", label: "Add if" },
  { kind: "try", label: "Add try" },
  { kind: "while", label: "Add while" },
];

function insertLabel(kind: BasicSequencerStepTemplate, scope: "below" | "child"): string {
  const prefix = scope === "below" ? "Insert" : "Insert";
  switch (kind) {
    case "call":
      return `${prefix} call${scope === "below" ? " below" : ""}`;
    case "sleep":
      return `${prefix} sleep${scope === "below" ? " below" : ""}`;
    case "repeat":
      return `${prefix} repeat${scope === "below" ? " below" : ""}`;
    case "adaptive":
      return `${prefix} adaptive${scope === "below" ? " below" : ""}`;
    case "for":
      return `${prefix} for${scope === "below" ? " below" : ""}`;
    case "wait_until":
      return `${prefix} wait_until${scope === "below" ? " below" : ""}`;
    case "set":
      return `${prefix} set${scope === "below" ? " below" : ""}`;
    case "assign":
      return `${prefix} assign${scope === "below" ? " below" : ""}`;
    case "set_context":
      return `${prefix} set_context${scope === "below" ? " below" : ""}`;
    case "if":
      return `${prefix} if${scope === "below" ? " below" : ""}`;
    case "try":
      return `${prefix} try${scope === "below" ? " below" : ""}`;
    case "while":
      return `${prefix} while${scope === "below" ? " below" : ""}`;
    default:
      return `${prefix} step${scope === "below" ? " below" : ""}`;
  }
}

function kindColor(kind: string): string {
  switch (kind) {
    case "call":
      return "blue";
    case "sleep":
      return "gray";
    case "for":
    case "repeat":
      return "cyan";
    case "adaptive":
      return "orange";
    case "wait_until":
      return "teal";
    case "set_context":
      return "violet";
    case "assign":
    case "set":
      return "indigo";
    default:
      return "gray";
  }
}

type SiblingInfoMap = Record<
  string,
  { prev: SequencerStepOutlineNode | null; next: SequencerStepOutlineNode | null }
>;

type OutlineRowProps = {
  node: SequencerStepOutlineNode;
  depth: number;
  selectedId: string | null;
  onSelect: (id: string) => void;
  collapsedById: Record<string, boolean>;
  onToggleCollapse: (id: string) => void;
  onDuplicate: (node: SequencerStepOutlineNode) => void;
  onDelete: (node: SequencerStepOutlineNode) => void;
  onInsertBelow: (node: SequencerStepOutlineNode, kind: BasicSequencerStepTemplate) => void;
  onInsertChild: (
    node: SequencerStepOutlineNode,
    kind: BasicSequencerStepTemplate,
    containerKey: SequencerChildContainer
  ) => void;
  siblingInfoById: SiblingInfoMap;
  onMoveUp: (node: SequencerStepOutlineNode) => void;
  onMoveDown: (node: SequencerStepOutlineNode) => void;
};

function OutlineRow({
  node,
  depth,
  selectedId,
  onSelect,
  collapsedById,
  onToggleCollapse,
  onDuplicate,
  onDelete,
  onInsertBelow,
  onInsertChild,
  siblingInfoById,
  onMoveUp,
  onMoveDown,
}: OutlineRowProps) {
  const selected = node.id === selectedId;
  const collapsible = node.children.length > 0;
  const collapsed = collapsible ? Boolean(collapsedById[node.id]) : false;
  const childTargets = listChildInsertionTargets(node);
  const siblingInfo = siblingInfoById[node.id] ?? { prev: null, next: null };
  const issueCount = countStepIssues(node);

  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "stretch",
          gap: 6,
          marginLeft: depth * 14,
        }}
      >
        <ActionIcon
          size="sm"
          variant="subtle"
          color="gray"
          aria-label={collapsed ? "Expand step" : "Collapse step"}
          style={{ visibility: collapsible ? "visible" : "hidden", marginTop: 6, flexShrink: 0 }}
          onClick={() => {
            if (collapsible) {
              onToggleCollapse(node.id);
            }
          }}
        >
          {collapsed ? <IconChevronRight size={14} /> : <IconChevronDown size={14} />}
        </ActionIcon>
        <button
          type="button"
          onClick={() => onSelect(node.id)}
          style={{
            display: "block",
            width: "100%",
            padding: "8px 10px",
            borderRadius: 8,
            border: selected ? "1px solid var(--mantine-color-blue-5)" : "1px solid var(--card-border)",
            background: selected ? "rgba(59, 130, 246, 0.08)" : "transparent",
            textAlign: "left",
            cursor: "pointer",
          }}
        >
          <Stack gap={4}>
            <Group gap={6} wrap="wrap">
              <Badge size="xs" variant="light" color={kindColor(node.kind)}>
                {node.kind}
              </Badge>
              {node.branchLabel ? (
                <Badge size="xs" variant="outline" color="gray">
                  {node.branchLabel}
                </Badge>
              ) : null}
              {issueCount > 0 ? (
                <Badge size="xs" variant="light" color="red">
                  {issueCount} issue{issueCount === 1 ? "" : "s"}
                </Badge>
              ) : null}
              <Text size="xs" c="dimmed">
                L{node.line}
                {node.endLine > node.line ? `-${node.endLine}` : ""}
              </Text>
              {node.children.length > 0 ? (
                <Text size="xs" c="dimmed">
                  {node.children.length} child{node.children.length === 1 ? "" : "ren"}
                </Text>
              ) : null}
            </Group>
            <Text size="xs" fw={500} lineClamp={1}>
              {node.summary ?? node.kind}
            </Text>
          </Stack>
        </button>
        <Group gap={4} wrap="nowrap" style={{ alignSelf: "center", flexShrink: 0 }}>
          <ActionIcon size="sm" variant="subtle" color="gray" aria-label="Move step up" disabled={!siblingInfo.prev} onClick={(event) => { event.stopPropagation(); onMoveUp(node); }}>
            <IconArrowUp size={14} />
          </ActionIcon>
          <ActionIcon size="sm" variant="subtle" color="gray" aria-label="Move step down" disabled={!siblingInfo.next} onClick={(event) => { event.stopPropagation(); onMoveDown(node); }}>
            <IconArrowDown size={14} />
          </ActionIcon>
          <Menu withinPortal position="bottom-end" withArrow shadow="md" zIndex={1000}>
            <Menu.Target>
              <ActionIcon size="sm" variant="subtle" color="gray" aria-label="Insert step below">
                <IconPlus size={14} />
              </ActionIcon>
            </Menu.Target>
            <Menu.Dropdown>
              {STEP_TEMPLATE_OPTIONS.map((option) => (
                <Menu.Item key={`below-${option.kind}`} onClick={() => onInsertBelow(node, option.kind)}>
                  {insertLabel(option.kind, "below")}
                </Menu.Item>
              ))}
            </Menu.Dropdown>
          </Menu>
          {childTargets.length > 0 ? (
            <Menu withinPortal position="bottom-end" withArrow shadow="md" zIndex={1000}>
              <Menu.Target>
                <ActionIcon size="sm" variant="subtle" color="gray" aria-label="Insert child step">
                  <IconArrowDownRight size={14} />
                </ActionIcon>
              </Menu.Target>
              <Menu.Dropdown>
                {childTargets.map((target) => (
                  <Menu key={target.key} withinPortal={false} trigger="hover" position="right-start" shadow="md">
                    <Menu.Target>
                      <Menu.Item>{`Insert into ${target.label}`}</Menu.Item>
                    </Menu.Target>
                    <Menu.Dropdown>
                      {STEP_TEMPLATE_OPTIONS.map((option) => (
                        <Menu.Item
                          key={`child-${target.key}-${option.kind}`}
                          onClick={() => onInsertChild(node, option.kind, target.key)}
                        >
                          {insertLabel(option.kind, "child")}
                        </Menu.Item>
                      ))}
                    </Menu.Dropdown>
                  </Menu>
                ))}
              </Menu.Dropdown>
            </Menu>
          ) : null}
          <ActionIcon size="sm" variant="subtle" color="gray" aria-label="Duplicate step" onClick={(event) => { event.stopPropagation(); onDuplicate(node); }}>
            <IconCopy size={14} />
          </ActionIcon>
          <ActionIcon size="sm" variant="subtle" color="red" aria-label="Delete step" onClick={(event) => { event.stopPropagation(); onDelete(node); }}>
            <IconTrash size={14} />
          </ActionIcon>
        </Group>
      </div>
      {!collapsed &&
        node.children.map((child) => (
          <OutlineRow
            key={child.id}
            node={child}
            depth={depth + 1}
            selectedId={selectedId}
            onSelect={onSelect}
            collapsedById={collapsedById}
            onToggleCollapse={onToggleCollapse}
            onDuplicate={onDuplicate}
            onDelete={onDelete}
            onInsertBelow={onInsertBelow}
            onInsertChild={onInsertChild}
            siblingInfoById={siblingInfoById}
            onMoveUp={onMoveUp}
            onMoveDown={onMoveDown}
          />
        ))}
    </>
  );
}

type Props = {
  outline: SequencerStepOutlineNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  collapsedById: Record<string, boolean>;
  onToggleCollapse: (id: string) => void;
  onDuplicate: (node: SequencerStepOutlineNode) => void;
  onDelete: (node: SequencerStepOutlineNode) => void;
  onInsertBelow: (node: SequencerStepOutlineNode, kind: BasicSequencerStepTemplate) => void;
  onInsertChild: (
    node: SequencerStepOutlineNode,
    kind: BasicSequencerStepTemplate,
    containerKey: SequencerChildContainer
  ) => void;
  siblingInfoById: SiblingInfoMap;
  onMoveUp: (node: SequencerStepOutlineNode) => void;
  onMoveDown: (node: SequencerStepOutlineNode) => void;
  onInsertTopLevel: (kind: BasicSequencerStepTemplate) => void;
};

export function SequencerStepTree({
  outline,
  selectedId,
  onSelect,
  collapsedById,
  onToggleCollapse,
  onDuplicate,
  onDelete,
  onInsertBelow,
  onInsertChild,
  siblingInfoById,
  onMoveUp,
  onMoveDown,
  onInsertTopLevel,
}: Props) {
  return (
    <Card
      radius="sm"
      p="xs"
      style={{
        border: "1px solid var(--card-border)",
        minHeight: 0,
        height: "100%",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Group justify="space-between" align="center" mb={6}>
        <Text size="xs" c="dimmed">
          Steps
        </Text>
        <Menu withinPortal position="bottom-end" withArrow shadow="md" zIndex={1000}>
          <Menu.Target>
            <Button size="compact-xs" variant="light" leftSection={<IconPlus size={14} />}>
              Quick add
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            {STEP_TEMPLATE_OPTIONS.map((option) => (
              <Menu.Item key={option.kind} onClick={() => onInsertTopLevel(option.kind)}>
                {option.label}
              </Menu.Item>
            ))}
          </Menu.Dropdown>
        </Menu>
      </Group>
      {outline.length <= 0 ? (
        <Text size="xs" c="dimmed">
          No sequencer steps detected yet. Use quick add or load YAML to see a visual outline.
        </Text>
      ) : (
        <ScrollArea style={{ flex: 1, minHeight: 0 }}>
          <Stack gap={6}>
            {outline.map((node) => (
              <OutlineRow
                key={node.id}
                node={node}
                depth={0}
                selectedId={selectedId}
                onSelect={onSelect}
                collapsedById={collapsedById}
                onToggleCollapse={onToggleCollapse}
                onDuplicate={onDuplicate}
                onDelete={onDelete}
                onInsertBelow={onInsertBelow}
                onInsertChild={onInsertChild}
                siblingInfoById={siblingInfoById}
                onMoveUp={onMoveUp}
                onMoveDown={onMoveDown}
              />
            ))}
          </Stack>
        </ScrollArea>
      )}
    </Card>
  );
}
