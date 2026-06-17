import { ActionIcon, Badge, Card, Group, ScrollArea, Stack, Text } from "@mantine/core";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
import { applyEditedContextColumns, applyEditedVars } from "../editing";
import {
  countContextColumnIssues,
  countMetadataNameIssues,
} from "../editor_helpers";
import type { SequencerOutlineMetadata } from "../types";
import { SequencerVarsEditor } from "../../../components/SequencerVarsEditor";

type Props = {
  metadata: SequencerOutlineMetadata;
  metadataCollapsed: boolean;
  onToggleCollapsed: () => void;
  yamlText: string;
  onYamlTextChange: (value: string) => void;
};

export function SequencerMetadataPanel({
  metadata,
  metadataCollapsed,
  onToggleCollapsed,
  yamlText,
  onYamlTextChange,
}: Props) {
  const varsIssueCount = countMetadataNameIssues(metadata.vars);
  const contextIssueCount = countContextColumnIssues(metadata.contextColumns);

  return (
    <Card
      radius="sm"
      p="xs"
      style={{
        border: "1px solid var(--card-border)",
        // When expanded, become a flex region that shares the outline pane's
        // height with the step tree (and is bounded by it), so the vars list
        // scrolls within a real, fully-reachable area instead of overflowing
        // and getting clipped by the modal.
        ...(metadataCollapsed
          ? { flexShrink: 0 }
          : {
              // Size to content for short lists, but cap and scroll for long
              // ones. The inner ScrollArea (flex:1, minHeight:0) shrinks with
              // the card when space is tight, so the list is always fully
              // scrollable and never clipped.
              flex: "0 1 auto",
              minHeight: 0,
              maxHeight: "clamp(8rem, 38vh, 30rem)",
              display: "flex",
              flexDirection: "column",
            }),
      }}
    >
      <Stack gap={6} style={metadataCollapsed ? undefined : { flex: 1, minHeight: 0 }}>
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
              metadataCollapsed ? "Expand sequence metadata" : "Collapse sequence metadata"
            }
            onClick={onToggleCollapsed}
          >
            {metadataCollapsed ? (
              <IconChevronRight size={16} />
            ) : (
              <IconChevronDown size={16} />
            )}
          </ActionIcon>
        </Group>
        {!metadataCollapsed && (
          <ScrollArea
            style={{ flex: 1, minHeight: 0 }}
            type="auto"
            offsetScrollbars
          >
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                gap: 8,
              }}
            >
            <SequencerVarsEditor
              entries={metadata.vars}
              issueCount={varsIssueCount}
              issueText={
                varsIssueCount > 0 ? "Variable names must be unique and non-empty." : null
              }
              onChange={(entries) => onYamlTextChange(applyEditedVars(yamlText, entries))}
            />
            <SequencerVarsEditor
              title="Context columns"
              addLabel="Add"
              emptyLabel="No context columns."
              addEmptyHint="Add one to create a top-level context_columns block."
              nameLabel="Context column name"
              valueLabel="Context column type"
              removeLabel="Remove context column"
              nextNamePrefix="context"
              valueOptions={[
                { value: "float64", label: "float64" },
                { value: "int64", label: "int64" },
                { value: "bool", label: "bool" },
              ]}
              entries={metadata.contextColumns}
              issueCount={contextIssueCount}
              issueText={
                contextIssueCount > 0
                  ? "Context column names must be unique/non-empty and types must be float64, int64, or bool."
                  : null
              }
              onChange={(entries) =>
                onYamlTextChange(applyEditedContextColumns(yamlText, entries))
              }
            />
            </div>
          </ScrollArea>
        )}
      </Stack>
    </Card>
  );
}
