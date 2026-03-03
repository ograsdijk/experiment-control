import { Badge, Card, Group, ScrollArea, Stack, Text } from "@mantine/core";
import { AdaptiveStepInspector } from "../../../components/AdaptiveStepInspector";
import { CommonStepInspector } from "../../../components/CommonStepInspector";
import { EditableStepInspector } from "../../../components/EditableStepInspector";
import { LoopStepInspector } from "../../../components/LoopStepInspector";
import { YamlPreview } from "../../../components/YamlPreview";
import type { CapabilityMember } from "../../../types";
import type { SequencerStepOutlineNode } from "../types";

type Props = {
  selectedStep: SequencerStepOutlineNode | null;
  yamlText: string;
  onYamlTextChange: (value: string) => void;
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
  colorScheme: "light" | "dark";
  onSelectStep: (id: string) => void;
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
    node.callDetail ||
      node.sleepDetail ||
      node.waitUntilDetail ||
      node.repeatDetail ||
      node.forDetail ||
      node.ifDetail ||
      node.whileDetail ||
      node.adaptiveDetail
  );
}

export function SequencerSelectionPanel({
  selectedStep,
  yamlText,
  onYamlTextChange,
  capabilitiesByDevice,
  colorScheme,
  onSelectStep,
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
      {!selectedStep ? (
        <Text size="xs" c="dimmed">
          Select a step to inspect it.
        </Text>
      ) : (
        <ScrollArea style={{ flex: 1, minHeight: 0 }}>
          <Stack gap="sm">
            <Group gap="xs" wrap="wrap">
              <Badge size="sm" variant="light" color={kindColor(selectedStep.kind)}>
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
                onSelectStep={onSelectStep}
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
              <CommonStepInspector kind="while" detail={selectedStep.whileDetail} />
            ) : selectedStep.atomicDetail ? (
              <CommonStepInspector kind="atomic" detail={selectedStep.atomicDetail} />
            ) : selectedStep.pauseDetail ? (
              <CommonStepInspector kind="pause" detail={selectedStep.pauseDetail} />
            ) : selectedStep.parallelDetail ? (
              <CommonStepInspector kind="parallel" detail={selectedStep.parallelDetail} />
            ) : selectedStep.forDetail ? (
              <LoopStepInspector kind="for" detail={selectedStep.forDetail} />
            ) : selectedStep.repeatDetail ? (
              <LoopStepInspector kind="repeat" detail={selectedStep.repeatDetail} />
            ) : null}
            {selectedStep.children.length > 0 ? (
              <Card
                radius="sm"
                p="xs"
                style={{
                  border: "1px solid var(--card-border)",
                  background: "rgba(148, 163, 184, 0.04)",
                }}
              >
                <Stack gap={6}>
                  <Group justify="space-between" align="center">
                    <Text size="xs" fw={600}>
                      Nested steps
                    </Text>
                    <Text size="xs" c="dimmed">
                      {selectedStep.children.length} step
                      {selectedStep.children.length === 1 ? "" : "s"}
                    </Text>
                  </Group>
                  <Stack gap={6}>
                    {selectedStep.children.map((child) => (
                      <button
                        key={child.id}
                        type="button"
                        onClick={() => onSelectStep(child.id)}
                        style={{
                          width: "100%",
                          textAlign: "left",
                          padding: "8px 10px",
                          borderRadius: 8,
                          border: "1px solid var(--card-border)",
                          background: "rgba(148, 163, 184, 0.03)",
                          cursor: "pointer",
                        }}
                      >
                        <Stack gap={2}>
                          <Group gap={6} wrap="wrap">
                            {child.branchLabel ? (
                              <Badge size="xs" variant="light" color="gray">
                                {child.branchLabel}
                              </Badge>
                            ) : null}
                            <Text size="xs" c="dimmed">
                              {child.kind}
                            </Text>
                          </Group>
                          <Text size="xs" fw={500} lineClamp={1}>
                            {child.summary ?? child.kind}
                          </Text>
                        </Stack>
                      </button>
                    ))}
                  </Stack>
                  <Text size="xs" c="dimmed">
                    Select a nested step to edit it in context.
                  </Text>
                </Stack>
              </Card>
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
  );
}
