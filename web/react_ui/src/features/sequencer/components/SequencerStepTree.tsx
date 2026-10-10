import { ActionIcon, Badge, Button, Card, Group, Menu, ScrollArea, Stack, Text, Tooltip } from "@mantine/core";
import {
  IconArrowDown,
  IconArrowDownRight,
  IconArrowUp,
  IconChevronDown,
  IconChevronRight,
  IconCopy,
  IconEye,
  IconEyeOff,
  IconPlus,
  IconTrash,
} from "@tabler/icons-react";
import { useEffect, useRef } from "react";
import {
  listChildInsertionTargets,
  type BasicSequencerStepTemplate,
  type SequencerChildContainer,
} from "../editing";
import { countStepIssues } from "../editor_helpers";
import {
  SEVERITY_ORDER,
  groupBySeverity,
  type StepDiagnostics,
} from "../diagnostic_locations";
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
  onToggleEnabled: (node: SequencerStepOutlineNode) => void;
  onInsertBelow: (node: SequencerStepOutlineNode, kind: BasicSequencerStepTemplate) => void;
  onInsertChild: (
    node: SequencerStepOutlineNode,
    kind: BasicSequencerStepTemplate,
    containerKey: SequencerChildContainer
  ) => void;
  siblingInfoById: SiblingInfoMap;
  onMoveUp: (node: SequencerStepOutlineNode) => void;
  onMoveDown: (node: SequencerStepOutlineNode) => void;
  stepDiagnostics?: StepDiagnostics;
  activeLeafId?: string | null;
  activeAncestorIds?: ReadonlySet<string>;
};

const SEVERITY_COLOR = { error: "red", warning: "yellow", info: "gray" } as const;

const SEVERITY_LABEL = { error: "error", warning: "warning", info: "note" } as const;

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

/**
 * One badge per severity: counting everything under the worst severity made
 * 1 error + 1 warning read "2 errors".
 */
function StepDiagnosticBadge({
  nodeId,
  collapsed,
  stepDiagnostics,
}: {
  nodeId: string;
  collapsed: boolean;
  stepDiagnostics?: StepDiagnostics;
}) {
  const own = stepDiagnostics?.byStepId.get(nodeId) ?? [];
  if (own.length > 0) {
    return (
      <>
        {groupBySeverity(own).map(({ severity, items }) => (
          <Tooltip
            key={severity}
            multiline
            w={360}
            withinPortal
            zIndex={1000}
            label={items.map((d) => d.message).join("\n\n")}
            style={{ whiteSpace: "pre-wrap" }}
          >
            <Badge
              size="xs"
              variant={severity === "error" ? "filled" : "light"}
              color={SEVERITY_COLOR[severity]}
            >
              {plural(items.length, SEVERITY_LABEL[severity])}
            </Badge>
          </Tooltip>
        ))}
      </>
    );
  }
  // A collapsed step hides its children's markers: summarize them here.
  const inside = stepDiagnostics?.insideById.get(nodeId);
  if (inside && collapsed) {
    return (
      <>
        {SEVERITY_ORDER.filter((severity) => inside[severity] > 0).map((severity) => (
          <Badge key={severity} size="xs" variant="outline" color={SEVERITY_COLOR[severity]}>
            {plural(inside[severity], SEVERITY_LABEL[severity])} inside
          </Badge>
        ))}
      </>
    );
  }
  return null;
}

function OutlineRow({
  node,
  depth,
  selectedId,
  onSelect,
  collapsedById,
  onToggleCollapse,
  onDuplicate,
  onDelete,
  onToggleEnabled,
  onInsertBelow,
  onInsertChild,
  siblingInfoById,
  onMoveUp,
  onMoveDown,
  stepDiagnostics,
  activeLeafId = null,
  activeAncestorIds,
}: OutlineRowProps) {
  const selected = node.id === selectedId;
  const active = node.id === activeLeafId;
  // The running step is below this one (shown even while it is collapsed).
  const activeInside = !active && Boolean(activeAncestorIds?.has(node.id));
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
          data-active-step={active ? "true" : undefined}
          onClick={() => onSelect(node.id)}
          style={{
            display: "block",
            width: "100%",
            padding: "8px 10px",
            borderRadius: 8,
            border: selected ? "1px solid var(--mantine-color-blue-5)" : "1px solid var(--card-border)",
            // The running step: a green bar and fill, distinct from the blue selection.
            borderLeft: active
              ? "4px solid var(--mantine-color-green-6)"
              : activeInside
                ? "4px solid rgba(34, 197, 94, 0.45)"
                : undefined,
            background: active
              ? "rgba(34, 197, 94, 0.16)"
              : activeInside
                ? "rgba(34, 197, 94, 0.06)"
                : selected
                  ? "rgba(59, 130, 246, 0.08)"
                  : "transparent",
            textAlign: "left",
            cursor: "pointer",
            opacity: node.disabled ? 0.55 : 1,
          }}
        >
          <Stack gap={4}>
            <Group gap={6} wrap="wrap">
              <Badge size="xs" variant="light" color={kindColor(node.kind)}>
                {node.kind}
              </Badge>
              {active ? (
                <Badge size="xs" variant="filled" color="green">
                  running
                </Badge>
              ) : activeInside && collapsed ? (
                <Badge size="xs" variant="outline" color="green">
                  running inside
                </Badge>
              ) : null}
              {node.disabled ? (
                <Badge size="xs" variant="outline" color="gray">
                  disabled
                </Badge>
              ) : null}
              {node.branchLabel ? (
                <Badge size="xs" variant="outline" color="gray">
                  {node.branchLabel === "finally"
                    ? "finally cleanup"
                    : node.branchLabel}
                </Badge>
              ) : null}
              <StepDiagnosticBadge
                nodeId={node.id}
                collapsed={collapsed}
                stepDiagnostics={stepDiagnostics}
              />
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
          <ActionIcon
            size="sm"
            variant="subtle"
            color="gray"
            aria-label={node.disabled ? "Enable step" : "Disable step"}
            onClick={(event) => { event.stopPropagation(); onToggleEnabled(node); }}
          >
            {node.disabled ? <IconEyeOff size={14} /> : <IconEye size={14} />}
          </ActionIcon>
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
            onToggleEnabled={onToggleEnabled}
            onInsertBelow={onInsertBelow}
            onInsertChild={onInsertChild}
            siblingInfoById={siblingInfoById}
            onMoveUp={onMoveUp}
            onMoveDown={onMoveDown}
            stepDiagnostics={stepDiagnostics}
            activeLeafId={activeLeafId}
            activeAncestorIds={activeAncestorIds}
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
  onToggleEnabled: (node: SequencerStepOutlineNode) => void;
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
  stepDiagnostics?: StepDiagnostics;
  activeLeafId?: string | null;
  activeAncestorIds?: ReadonlySet<string>;
  /** Scroll the running step into view when it changes. */
  follow?: boolean;
  /** The user scrolled the list themselves (wheel, touch, scrollbar, paging keys). */
  onUserScroll?: () => void;
};

const SCROLL_KEYS = new Set(["PageUp", "PageDown", "Home", "End", "ArrowUp", "ArrowDown"]);

export function SequencerStepTree({
  outline,
  selectedId,
  onSelect,
  collapsedById,
  onToggleCollapse,
  onDuplicate,
  onDelete,
  onToggleEnabled,
  onInsertBelow,
  onInsertChild,
  siblingInfoById,
  onMoveUp,
  onMoveDown,
  onInsertTopLevel,
  stepDiagnostics,
  activeLeafId = null,
  activeAncestorIds,
  follow = false,
  onUserScroll,
}: Props) {
  const viewportRef = useRef<HTMLDivElement | null>(null);

  // After a frame, so a parent expanded for the same change is rendered.
  useEffect(() => {
    if (!follow || !activeLeafId) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      viewportRef.current
        ?.querySelector('[data-active-step="true"]')
        ?.scrollIntoView({ block: "nearest" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [follow, activeLeafId]);

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
        <ScrollArea
          style={{ flex: 1, minHeight: 0 }}
          type="auto"
          offsetScrollbars
          viewportRef={viewportRef}
          // scrollIntoView only fires scroll events; these are the user's.
          onWheel={onUserScroll}
          onTouchMove={onUserScroll}
          onPointerDown={(event) => {
            // The scrollbars are outside the viewport.
            if (!viewportRef.current?.contains(event.target as Node)) {
              onUserScroll?.();
            }
          }}
          onKeyDown={(event) => {
            if (SCROLL_KEYS.has(event.key)) {
              onUserScroll?.();
            }
          }}
        >
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
                onToggleEnabled={onToggleEnabled}
                onInsertBelow={onInsertBelow}
                onInsertChild={onInsertChild}
                siblingInfoById={siblingInfoById}
                onMoveUp={onMoveUp}
                onMoveDown={onMoveDown}
                stepDiagnostics={stepDiagnostics}
                activeLeafId={activeLeafId}
                activeAncestorIds={activeAncestorIds}
              />
            ))}
          </Stack>
        </ScrollArea>
      )}
    </Card>
  );
}
