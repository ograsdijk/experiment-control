import { ActionIcon, Badge, Card, Group, Menu, ScrollArea, Stack, Text } from "@mantine/core";
import {
  IconArrowDownRight,
  IconArrowDown,
  IconArrowUp,
  IconChevronDown,
  IconChevronRight,
  IconCopy,
  IconPlus,
  IconTrash,
} from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";
import {
  buildSequencerOutlineMetadata,
  buildSequencerStepOutline,
  flattenSequencerStepOutline,
} from "../features/sequencer/outline";
import {
  applyEditedVars,
  deleteStep,
  duplicateStep,
  getChildInsertionLine,
  insertStepBelow,
  insertStepInside,
  listChildInsertionTargets,
  moveStepDown,
  moveStepUp,
} from "../features/sequencer/editing";
import type { SequencerStepOutlineNode } from "../features/sequencer/types";
import type { CapabilityMember } from "../types";
import { AdaptiveStepInspector } from "./AdaptiveStepInspector";
import { CommonStepInspector } from "./CommonStepInspector";
import { EditableStepInspector } from "./EditableStepInspector";
import { LoopStepInspector } from "./LoopStepInspector";
import { SequencerVarsEditor } from "./SequencerVarsEditor";
import { YamlPreview } from "./YamlPreview";

type Props = {
  yamlText: string;
  onYamlTextChange: (value: string) => void;
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
  colorScheme: "light" | "dark";
};

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

function isEditableStep(node: SequencerStepOutlineNode): boolean {
  return Boolean(
    node.callDetail || node.sleepDetail || node.repeatDetail || node.forDetail
  );
}

type OutlineRowProps = {
  node: SequencerStepOutlineNode;
  depth: number;
  selectedId: string | null;
  onSelect: (id: string) => void;
  collapsedById: Record<string, boolean>;
  onToggleCollapse: (id: string) => void;
  onDuplicate: (node: SequencerStepOutlineNode) => void;
  onDelete: (node: SequencerStepOutlineNode) => void;
  onInsertBelow: (
    node: SequencerStepOutlineNode,
    kind: "call" | "sleep" | "repeat"
  ) => void;
  onInsertChild: (
    node: SequencerStepOutlineNode,
    kind: "call" | "sleep" | "repeat",
    containerKey: "do" | "then" | "else"
  ) => void;
  siblingInfoById: Record<
    string,
    { prev: SequencerStepOutlineNode | null; next: SequencerStepOutlineNode | null }
  >;
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
          style={{
            visibility: collapsible ? "visible" : "hidden",
            marginTop: 6,
            flexShrink: 0,
          }}
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
            border: selected
              ? "1px solid var(--mantine-color-blue-5)"
              : "1px solid var(--card-border)",
            background: selected
              ? "rgba(59, 130, 246, 0.08)"
              : "transparent",
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
              <Text size="xs" c="dimmed">
                L{node.line}
                {node.endLine > node.line ? `-${node.endLine}` : ""}
              </Text>
              {node.children.length > 0 ? (
                <Text size="xs" c="dimmed">
                  {node.children.length} child
                  {node.children.length === 1 ? "" : "ren"}
                </Text>
              ) : null}
            </Group>
            <Text size="xs" fw={500} lineClamp={1}>
              {node.summary ?? node.kind}
            </Text>
          </Stack>
        </button>
        <Group gap={4} wrap="nowrap" style={{ alignSelf: "center", flexShrink: 0 }}>
          <ActionIcon
            size="sm"
            variant="subtle"
            color="gray"
            aria-label="Move step up"
            disabled={!siblingInfo.prev}
            onClick={(event) => {
              event.stopPropagation();
              onMoveUp(node);
            }}
          >
            <IconArrowUp size={14} />
          </ActionIcon>
          <ActionIcon
            size="sm"
            variant="subtle"
            color="gray"
            aria-label="Move step down"
            disabled={!siblingInfo.next}
            onClick={(event) => {
              event.stopPropagation();
              onMoveDown(node);
            }}
          >
            <IconArrowDown size={14} />
          </ActionIcon>
          <Menu withinPortal position="bottom-end" withArrow shadow="md" zIndex={1000}>
            <Menu.Target>
              <ActionIcon
                size="sm"
                variant="subtle"
                color="gray"
                aria-label="Insert step below"
              >
                <IconPlus size={14} />
              </ActionIcon>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Item
                onClick={() => onInsertBelow(node, "call")}
              >
                Insert call below
              </Menu.Item>
              <Menu.Item
                onClick={() => onInsertBelow(node, "sleep")}
              >
                Insert sleep below
              </Menu.Item>
              <Menu.Item
                onClick={() => onInsertBelow(node, "repeat")}
              >
                Insert repeat below
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
          {childTargets.length > 0 ? (
            <Menu withinPortal position="bottom-end" withArrow shadow="md" zIndex={1000}>
              <Menu.Target>
                <ActionIcon
                  size="sm"
                  variant="subtle"
                  color="blue"
                  aria-label="Insert step inside"
                >
                  <IconArrowDownRight size={14} />
                </ActionIcon>
              </Menu.Target>
              <Menu.Dropdown>
                {childTargets.map((target) => (
                  <div key={target.key}>
                    <Menu.Item onClick={() => onInsertChild(node, "call", target.key)}>
                      Insert call in {target.label}
                    </Menu.Item>
                    <Menu.Item onClick={() => onInsertChild(node, "sleep", target.key)}>
                      Insert sleep in {target.label}
                    </Menu.Item>
                    <Menu.Item onClick={() => onInsertChild(node, "repeat", target.key)}>
                      Insert repeat in {target.label}
                    </Menu.Item>
                  </div>
                ))}
              </Menu.Dropdown>
            </Menu>
          ) : null}
          <ActionIcon
            size="sm"
            variant="subtle"
            color="gray"
            aria-label="Duplicate step"
            onClick={(event) => {
              event.stopPropagation();
              onDuplicate(node);
            }}
          >
            <IconCopy size={14} />
          </ActionIcon>
          <ActionIcon
            size="sm"
            variant="subtle"
            color="red"
            aria-label="Delete step"
            onClick={(event) => {
              event.stopPropagation();
              onDelete(node);
            }}
          >
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

type MetadataSectionProps = {
  title: string;
  entries: ReadonlyArray<{ name: string; value: string | null }>;
  emptyLabel: string;
  nameColor: string;
};

function MetadataSection({
  title,
  entries,
  emptyLabel,
  nameColor,
}: MetadataSectionProps) {
  return (
    <Card radius="sm" p="xs" style={{ border: "1px solid var(--card-border)" }}>
      <Stack gap={6}>
        <Group justify="space-between" align="center">
          <Text size="xs" fw={600}>
            {title}
          </Text>
          <Badge size="xs" variant="light" color={entries.length > 0 ? nameColor : "gray"}>
            {entries.length}
          </Badge>
        </Group>
        {entries.length <= 0 ? (
          <Text size="xs" c="dimmed">
            {emptyLabel}
          </Text>
        ) : (
          <ScrollArea h={entries.length > 4 ? 120 : undefined} type="auto">
            <Stack gap={6}>
              {entries.map((entry) => (
                <Card
                  key={entry.name}
                  radius="sm"
                  p="xs"
                  style={{
                    border: "1px solid var(--card-border)",
                    background: "rgba(148, 163, 184, 0.04)",
                  }}
                >
                  <Stack gap={2}>
                    <Group gap={6} wrap="wrap">
                      <Badge size="xs" variant="light" color={nameColor}>
                        {entry.name}
                      </Badge>
                      {entry.value ? (
                        <Text
                          size="xs"
                          style={{
                            fontFamily:
                              "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
                            wordBreak: "break-word",
                          }}
                        >
                          {entry.value}
                        </Text>
                      ) : (
                        <Text size="xs" c="dimmed">
                          no value
                        </Text>
                      )}
                    </Group>
                  </Stack>
                </Card>
              ))}
            </Stack>
          </ScrollArea>
        )}
      </Stack>
    </Card>
  );
}

function buildSiblingInfoMap(
  nodes: SequencerStepOutlineNode[]
): Record<string, { prev: SequencerStepOutlineNode | null; next: SequencerStepOutlineNode | null }> {
  const out: Record<
    string,
    { prev: SequencerStepOutlineNode | null; next: SequencerStepOutlineNode | null }
  > = {};

  const visit = (siblings: SequencerStepOutlineNode[]) => {
    siblings.forEach((node, index) => {
      out[node.id] = {
        prev: index > 0 ? siblings[index - 1] : null,
        next: index < siblings.length - 1 ? siblings[index + 1] : null,
      };
      if (node.children.length > 0) {
        visit(node.children);
      }
    });
  };

  visit(nodes);
  return out;
}

export function SequencerOutlinePane({
  yamlText,
  onYamlTextChange,
  capabilitiesByDevice,
  colorScheme,
}: Props) {
  const metadata = useMemo(
    () => buildSequencerOutlineMetadata(yamlText),
    [yamlText]
  );
  const outline = useMemo(() => buildSequencerStepOutline(yamlText), [yamlText]);
  const flatOutline = useMemo(
    () => flattenSequencerStepOutline(outline),
    [outline]
  );
  const siblingInfoById = useMemo(() => buildSiblingInfoMap(outline), [outline]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [collapsedById, setCollapsedById] = useState<Record<string, boolean>>({});
  const [outlineCollapsed, setOutlineCollapsed] = useState(false);
  const [metadataCollapsed, setMetadataCollapsed] = useState(false);
  const [pendingSelection, setPendingSelection] = useState<{
    line: number;
    kind: string | null;
    mode: "exact" | "closest";
  } | null>(null);

  useEffect(() => {
    if (flatOutline.length <= 0) {
      if (selectedId !== null) {
        setSelectedId(null);
      }
      if (pendingSelection !== null) {
        setPendingSelection(null);
      }
      return;
    }
    if (pendingSelection) {
      let nextNode: SequencerStepOutlineNode | null = null;
      if (pendingSelection.mode === "exact") {
        nextNode =
          flatOutline.find(
            (node) =>
              node.line === pendingSelection.line &&
              (!pendingSelection.kind || node.kind === pendingSelection.kind)
          ) ?? null;
      }
      if (!nextNode) {
        nextNode =
          flatOutline.find((node) => node.line >= pendingSelection.line) ??
          flatOutline[flatOutline.length - 1] ??
          null;
      }
      if (nextNode && nextNode.id !== selectedId) {
        setSelectedId(nextNode.id);
      }
      setPendingSelection(null);
      return;
    }
    if (!selectedId || !flatOutline.some((node) => node.id === selectedId)) {
      setSelectedId(flatOutline[0].id);
    }
  }, [flatOutline, selectedId, pendingSelection]);

  useEffect(() => {
    if (flatOutline.length <= 0) {
      if (Object.keys(collapsedById).length > 0) {
        setCollapsedById({});
      }
      return;
    }
    const validIds = new Set(flatOutline.map((node) => node.id));
    const nextEntries = Object.entries(collapsedById).filter(([id]) =>
      validIds.has(id)
    );
    if (nextEntries.length !== Object.keys(collapsedById).length) {
      setCollapsedById(Object.fromEntries(nextEntries));
    }
  }, [flatOutline, collapsedById]);

  const selectedStep =
    selectedId === null
      ? null
      : flatOutline.find((node) => node.id === selectedId) ?? null;

  const handleDuplicateStep = (node: SequencerStepOutlineNode) => {
    setPendingSelection({
      line: node.endLine + 1,
      kind: node.kind,
      mode: "exact",
    });
    onYamlTextChange(duplicateStep(yamlText, node));
  };

  const handleDeleteStep = (node: SequencerStepOutlineNode) => {
    setPendingSelection({
      line: node.line,
      kind: null,
      mode: "closest",
    });
    onYamlTextChange(deleteStep(yamlText, node));
  };

  const handleInsertBelow = (
    node: SequencerStepOutlineNode,
    kind: "call" | "sleep" | "repeat"
  ) => {
    setPendingSelection({
      line: node.endLine + 1,
      kind,
      mode: "exact",
    });
    onYamlTextChange(insertStepBelow(yamlText, node, kind));
  };

  const handleInsertChild = (
    node: SequencerStepOutlineNode,
    kind: "call" | "sleep" | "repeat",
    containerKey: "do" | "then" | "else"
  ) => {
    const insertionLine = getChildInsertionLine(node, containerKey);
    setPendingSelection({
      line: insertionLine ?? node.line + 1,
      kind,
      mode: insertionLine ? "exact" : "closest",
    });
    setCollapsedById((prev) => ({
      ...prev,
      [node.id]: false,
    }));
    onYamlTextChange(insertStepInside(yamlText, node, kind, containerKey));
  };

  const handleMoveUp = (node: SequencerStepOutlineNode) => {
    const previousSibling = siblingInfoById[node.id]?.prev;
    if (!previousSibling) {
      return;
    }
    setPendingSelection({
      line: previousSibling.line,
      kind: node.kind,
      mode: "exact",
    });
    onYamlTextChange(moveStepUp(yamlText, node, previousSibling));
  };

  const handleMoveDown = (node: SequencerStepOutlineNode) => {
    const nextSibling = siblingInfoById[node.id]?.next;
    if (!nextSibling) {
      return;
    }
    setPendingSelection({
      line: nextSibling.line,
      kind: node.kind,
      mode: "exact",
    });
    onYamlTextChange(moveStepDown(yamlText, node, nextSibling));
  };

  return (
    <Card
      radius="md"
      p="sm"
      style={{
        border: "1px solid var(--card-border)",
        display: "flex",
        flexDirection: "column",
        flex: outlineCollapsed ? "0 0 auto" : "1 1 auto",
        width: "100%",
        minHeight: 0,
      }}
    >
      <Stack
        gap="sm"
        style={{
          flex: outlineCollapsed ? "0 0 auto" : "1 1 auto",
          minHeight: 0,
        }}
      >
        <Group justify="space-between" align="center">
          <Stack gap={2}>
            <Text size="sm" fw={600}>
              Sequence outline
            </Text>
            <Text size="xs" c="dimmed">
              Visual view of the current YAML with basic editing for variables,
              call, sleep, repeat, and for
            </Text>
          </Stack>
          <ActionIcon
            size="sm"
            variant="subtle"
            color="gray"
            aria-label={outlineCollapsed ? "Expand sequence outline" : "Collapse sequence outline"}
            onClick={() => setOutlineCollapsed((prev) => !prev)}
          >
            {outlineCollapsed ? (
              <IconChevronRight size={16} />
            ) : (
              <IconChevronDown size={16} />
            )}
          </ActionIcon>
        </Group>
        {!outlineCollapsed && (
          <>
            <Card
              radius="sm"
              p="xs"
              style={{ border: "1px solid var(--card-border)" }}
            >
              <Stack gap={6}>
                <Group justify="space-between" align="center">
                  <Group gap="xs" wrap="wrap" align="center">
                    <Badge size="xs" variant="light" color="gray">
                      version: {metadata.version ?? "n/a"}
                    </Badge>
                    <Text size="xs" c="dimmed">
                      Sequence metadata
                    </Text>
                  </Group>
                  <ActionIcon
                    size="sm"
                    variant="subtle"
                    color="gray"
                    aria-label={
                      metadataCollapsed
                        ? "Expand sequence metadata"
                        : "Collapse sequence metadata"
                    }
                    onClick={() => setMetadataCollapsed((prev) => !prev)}
                  >
                    {metadataCollapsed ? (
                      <IconChevronRight size={16} />
                    ) : (
                      <IconChevronDown size={16} />
                    )}
                  </ActionIcon>
                </Group>
                {!metadataCollapsed && (
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                      gap: 8,
                    }}
                  >
                    <SequencerVarsEditor
                      entries={metadata.vars}
                      onChange={(entries) =>
                        onYamlTextChange(applyEditedVars(yamlText, entries))
                      }
                    />
                    <MetadataSection
                      title="Context columns"
                      entries={metadata.contextColumns}
                      emptyLabel="No context columns"
                      nameColor="indigo"
                    />
                  </div>
                )}
              </Stack>
            </Card>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(220px, 0.9fr) minmax(0, 1.1fr)",
                gap: 12,
                flex: 1,
                minHeight: 0,
              }}
            >
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
                {outline.length <= 0 ? (
                  <Text size="xs" c="dimmed">
                    No sequencer steps detected yet. Add or load YAML to see a visual
                    outline.
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
                          onSelect={setSelectedId}
                          collapsedById={collapsedById}
                          onToggleCollapse={(id) =>
                            setCollapsedById((prev) => ({
                              ...prev,
                              [id]: !Boolean(prev[id]),
                            }))
                          }
                          onDuplicate={handleDuplicateStep}
                          onDelete={handleDeleteStep}
                          onInsertBelow={handleInsertBelow}
                          onInsertChild={handleInsertChild}
                          siblingInfoById={siblingInfoById}
                          onMoveUp={handleMoveUp}
                          onMoveDown={handleMoveDown}
                        />
                      ))}
                    </Stack>
                  </ScrollArea>
                )}
              </Card>

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
                {!selectedStep ? (
                  <Text size="xs" c="dimmed">
                    Select a step to inspect it.
                  </Text>
                ) : (
                  <ScrollArea style={{ flex: 1, minHeight: 0 }}>
                    <Stack gap="sm">
                      <Group gap="xs" wrap="wrap">
                        <Badge
                          size="sm"
                          variant="light"
                          color={kindColor(selectedStep.kind)}
                        >
                          {selectedStep.kind}
                        </Badge>
                        <Text size="xs" c="dimmed">
                          Lines {selectedStep.line}
                          {selectedStep.endLine > selectedStep.line
                            ? `-${selectedStep.endLine}`
                            : ""}
                        </Text>
                        {selectedStep.children.length > 0 ? (
                          <Badge size="xs" variant="outline" color="gray">
                            {selectedStep.children.length} nested step
                            {selectedStep.children.length === 1 ? "" : "s"}
                          </Badge>
                        ) : null}
                      </Group>
                      <Text size="sm" fw={600}>
                        {selectedStep.summary ?? selectedStep.kind}
                      </Text>
                      {isEditableStep(selectedStep) ? (
                        <EditableStepInspector
                          node={selectedStep}
                          yamlText={yamlText}
                          onYamlTextChange={onYamlTextChange}
                          capabilitiesByDevice={capabilitiesByDevice}
                        />
                      ) : selectedStep.adaptiveDetail ? (
                        <AdaptiveStepInspector detail={selectedStep.adaptiveDetail} />
                      ) : selectedStep.callDetail ? (
                        <CommonStepInspector kind="call" detail={selectedStep.callDetail} />
                      ) : selectedStep.sleepDetail ? (
                        <CommonStepInspector kind="sleep" detail={selectedStep.sleepDetail} />
                      ) : selectedStep.setDetail ? (
                        <CommonStepInspector kind="set" detail={selectedStep.setDetail} />
                      ) : selectedStep.assignDetail ? (
                        <CommonStepInspector kind="assign" detail={selectedStep.assignDetail} />
                      ) : selectedStep.waitUntilDetail ? (
                        <CommonStepInspector
                          kind="wait_until"
                          detail={selectedStep.waitUntilDetail}
                        />
                      ) : selectedStep.setContextDetail ? (
                        <CommonStepInspector
                          kind="set_context"
                          detail={selectedStep.setContextDetail}
                        />
                      ) : selectedStep.ifDetail ? (
                        <CommonStepInspector kind="if" detail={selectedStep.ifDetail} />
                      ) : selectedStep.whileDetail ? (
                        <CommonStepInspector
                          kind="while"
                          detail={selectedStep.whileDetail}
                        />
                      ) : selectedStep.atomicDetail ? (
                        <CommonStepInspector
                          kind="atomic"
                          detail={selectedStep.atomicDetail}
                        />
                      ) : selectedStep.pauseDetail ? (
                        <CommonStepInspector kind="pause" detail={selectedStep.pauseDetail} />
                      ) : selectedStep.parallelDetail ? (
                        <CommonStepInspector
                          kind="parallel"
                          detail={selectedStep.parallelDetail}
                        />
                      ) : selectedStep.forDetail ? (
                        <LoopStepInspector kind="for" detail={selectedStep.forDetail} />
                      ) : selectedStep.repeatDetail ? (
                        <LoopStepInspector
                          kind="repeat"
                          detail={selectedStep.repeatDetail}
                        />
                      ) : null}
                      <Stack gap={4}>
                        <Text size="xs" c="dimmed">
                          YAML block
                        </Text>
                        <YamlPreview
                          text={selectedStep.snippet}
                          colorScheme={colorScheme}
                          scrollable={false}
                        />
                      </Stack>
                    </Stack>
                  </ScrollArea>
                )}
              </Card>
            </div>
          </>
        )}
      </Stack>
    </Card>
  );
}
