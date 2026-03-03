import { ActionIcon, Card, Group, Stack, Text } from "@mantine/core";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";
import {
  buildSequencerOutlineMetadata,
  buildSequencerStepOutline,
  flattenSequencerStepOutline,
} from "../features/sequencer/outline";
import {
  deleteStep,
  duplicateStep,
  getChildInsertionLine,
  insertStepBelow,
  insertStepInside,
  moveStepDown,
  moveStepUp,
} from "../features/sequencer/editing";
import type { SequencerStepOutlineNode } from "../features/sequencer/types";
import { SequencerMetadataPanel } from "../features/sequencer/components/SequencerMetadataPanel";
import { SequencerSelectionPanel } from "../features/sequencer/components/SequencerSelectionPanel";
import { SequencerStepTree } from "../features/sequencer/components/SequencerStepTree";
import type { CapabilityMember } from "../types";

type Props = {
  yamlText: string;
  onYamlTextChange: (value: string) => void;
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
  colorScheme: "light" | "dark";
};

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
            <SequencerMetadataPanel
              metadata={metadata}
              metadataCollapsed={metadataCollapsed}
              onToggleCollapsed={() => setMetadataCollapsed((prev) => !prev)}
              yamlText={yamlText}
              onYamlTextChange={onYamlTextChange}
            />
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(220px, 0.9fr) minmax(0, 1.1fr)",
                gap: 12,
                flex: 1,
                minHeight: 0,
              }}
            >
              <SequencerStepTree
                outline={outline}
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
              <SequencerSelectionPanel
                selectedStep={selectedStep}
                yamlText={yamlText}
                onYamlTextChange={onYamlTextChange}
                capabilitiesByDevice={capabilitiesByDevice}
                colorScheme={colorScheme}
                onSelectStep={setSelectedId}
              />
            </div>
          </>
        )}
      </Stack>
    </Card>
  );
}
