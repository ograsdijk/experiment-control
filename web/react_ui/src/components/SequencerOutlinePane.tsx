import { ActionIcon, Badge, Card, Group, ScrollArea, Stack, Text } from "@mantine/core";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";
import {
  buildSequencerOutlineMetadata,
  buildSequencerStepOutline,
  flattenSequencerStepOutline,
} from "../features/sequencer/outline";
import { applyEditedVars } from "../features/sequencer/editing";
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
  return Boolean(node.callDetail || node.sleepDetail || node.repeatDetail);
}

type OutlineRowProps = {
  node: SequencerStepOutlineNode;
  depth: number;
  selectedId: string | null;
  onSelect: (id: string) => void;
  collapsedById: Record<string, boolean>;
  onToggleCollapse: (id: string) => void;
};

function OutlineRow({
  node,
  depth,
  selectedId,
  onSelect,
  collapsedById,
  onToggleCollapse,
}: OutlineRowProps) {
  const selected = node.id === selectedId;
  const collapsible = node.children.length > 0;
  const collapsed = collapsible ? Boolean(collapsedById[node.id]) : false;
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
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [collapsedById, setCollapsedById] = useState<Record<string, boolean>>({});
  const [outlineCollapsed, setOutlineCollapsed] = useState(false);
  const [metadataCollapsed, setMetadataCollapsed] = useState(false);

  useEffect(() => {
    if (flatOutline.length <= 0) {
      if (selectedId !== null) {
        setSelectedId(null);
      }
      return;
    }
    if (!selectedId || !flatOutline.some((node) => node.id === selectedId)) {
      setSelectedId(flatOutline[0].id);
    }
  }, [flatOutline, selectedId]);

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

  return (
    <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
      <Stack gap="sm">
        <Group justify="space-between" align="center">
          <Stack gap={2}>
            <Text size="sm" fw={600}>
              Sequence outline
            </Text>
            <Text size="xs" c="dimmed">
              Visual view of the current YAML with basic editing for variables,
              call, sleep, and repeat
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
              }}
            >
              <Card
                radius="sm"
                p="xs"
                style={{ border: "1px solid var(--card-border)", minHeight: 260 }}
              >
                {outline.length <= 0 ? (
                  <Text size="xs" c="dimmed">
                    No sequencer steps detected yet. Add or load YAML to see a visual
                    outline.
                  </Text>
                ) : (
                  <ScrollArea h={260}>
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
                        />
                      ))}
                    </Stack>
                  </ScrollArea>
                )}
              </Card>

              <Card
                radius="sm"
                p="xs"
                style={{ border: "1px solid var(--card-border)", minHeight: 260 }}
              >
                {!selectedStep ? (
                  <Text size="xs" c="dimmed">
                    Select a step to inspect it.
                  </Text>
                ) : (
                  <ScrollArea h={260}>
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
