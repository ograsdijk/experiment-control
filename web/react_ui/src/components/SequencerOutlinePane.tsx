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
  insertStepAtTopLevel,
  insertStepBelow,
  insertStepInside,
  moveStepDown,
  moveStepUp,
  type BasicSequencerStepTemplate,
} from "../features/sequencer/editing";
import type {
  SequencerOutlineMetadata,
  SequencerStepOutlineNode,
} from "../features/sequencer/types";
import { SequencerMetadataPanel } from "../features/sequencer/components/SequencerMetadataPanel";
import { SequencerSelectionPanel } from "../features/sequencer/components/SequencerSelectionPanel";
import { SequencerStepTree } from "../features/sequencer/components/SequencerStepTree";
import type { StreamAnalysisWorkspaceConfig } from "../features/stream/types";
import type { CapabilityMember } from "../types";
import type { StreamCatalogEntry } from "../types";
import type { TelemetrySignal } from "../types";

type Props = {
  yamlText: string;
  onYamlTextChange: (value: string) => void;
  streamCatalog: StreamCatalogEntry[];
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
  streamWorkspaces: Record<string, StreamAnalysisWorkspaceConfig>;
  latestSignalsByDevice: Record<string, Record<string, TelemetrySignal>>;
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
  streamCatalog,
  capabilitiesByDevice,
  streamWorkspaces,
  latestSignalsByDevice,
  colorScheme,
}: Props) {
  const parsedOutline = useMemo(() => {
    try {
      return {
        metadata: buildSequencerOutlineMetadata(yamlText),
        outline: buildSequencerStepOutline(yamlText),
        error: null as string | null,
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const fallbackMetadata: SequencerOutlineMetadata = {
        version: null,
        vars: [],
        contextColumns: [],
      };
      return {
        metadata: fallbackMetadata,
        outline: [] as SequencerStepOutlineNode[],
        error: message,
      };
    }
  }, [yamlText]);
  const metadata = parsedOutline.metadata;
  const outline = parsedOutline.outline;
  const outlineParseError = parsedOutline.error;
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
    kind: BasicSequencerStepTemplate
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
    kind: BasicSequencerStepTemplate,
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

  const handleInsertTopLevel = (kind: BasicSequencerStepTemplate) => {
    const fallbackLine = outline.length > 0 ? outline[outline.length - 1]?.endLine ?? 1 : 1;
    setPendingSelection({
      line: fallbackLine,
      kind,
      mode: "closest",
    });
    onYamlTextChange(insertStepAtTopLevel(yamlText, kind));
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
              Visual view of the current YAML with step-level editing, insertion,
              and metadata controls
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
            {outlineParseError ? (
              <Card radius="sm" p="xs" style={{ border: "1px solid var(--card-border)" }}>
                <Stack gap={4}>
                  <Text size="xs" c="red" fw={600}>
                    Outline parser error
                  </Text>
                  <Text size="xs" c="dimmed">
                    Visual outline is temporarily disabled for this YAML text.
                    You can continue editing in the full YAML editor.
                  </Text>
                  <Text
                    size="xs"
                    c="dimmed"
                    style={{
                      fontFamily:
                        'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
                    }}
                  >
                    {outlineParseError}
                  </Text>
                </Stack>
              </Card>
            ) : null}
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
                onInsertTopLevel={handleInsertTopLevel}
              />
              <SequencerSelectionPanel
                selectedStep={selectedStep}
                yamlText={yamlText}
                onYamlTextChange={onYamlTextChange}
                streamCatalog={streamCatalog}
                capabilitiesByDevice={capabilitiesByDevice}
                streamWorkspaces={streamWorkspaces}
                latestSignalsByDevice={latestSignalsByDevice}
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
