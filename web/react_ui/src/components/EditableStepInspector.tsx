import { ActionIcon, Button, Card, Group, Stack, Text, TextInput } from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import {
  applyEditedCallStep,
  applyEditedRepeatStep,
  applyEditedSleepStep,
} from "../features/sequencer/editing";
import type { SequencerOutlineMetadataEntry, SequencerStepOutlineNode } from "../features/sequencer/types";
import type { CapabilityMember } from "../types";

type Props = {
  node: SequencerStepOutlineNode;
  yamlText: string;
  onYamlTextChange: (value: string) => void;
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
};

function renderValue(value: string | null): string {
  return value ?? "";
}

function datalistId(nodeId: string, suffix: string): string {
  return `seq-${nodeId}-${suffix}`;
}

function nextParamName(entries: ReadonlyArray<SequencerOutlineMetadataEntry>): string {
  const existing = new Set(entries.map((entry) => entry.name));
  let index = existing.size + 1;
  while (existing.has(`param_${index}`)) {
    index += 1;
  }
  return `param_${index}`;
}

export function EditableStepInspector({
  node,
  yamlText,
  onYamlTextChange,
  capabilitiesByDevice,
}: Props) {
  if (node.callDetail) {
    const params = node.callDetail.params;
    const isSimpleParams = params.every((entry) => !entry.name.includes("."));
    const selectedDevice = node.callDetail.device ?? "";
    const actionOptions = (capabilitiesByDevice[selectedDevice] ?? []).map((member) => member.name);
    const selectedActionMember =
      (capabilitiesByDevice[selectedDevice] ?? []).find(
        (member) => member.name === (node.callDetail?.action ?? "")
      ) ?? null;
    const paramNameOptions = (selectedActionMember?.params ?? [])
      .map((param) => param.name)
      .filter((name): name is string => typeof name === "string" && name.trim().length > 0);

    if (!isSimpleParams) {
      return (
        <Card
          radius="sm"
          p="xs"
          style={{
            border: "1px solid var(--card-border)",
            background: "rgba(148, 163, 184, 0.04)",
          }}
        >
          <Stack gap={6}>
            <Text size="xs" c="dimmed">
              This call uses nested parameter keys, so Phase 2 keeps it read-only for now.
            </Text>
          </Stack>
        </Card>
      );
    }

    const updateCall = (
      nextAction: string,
      nextParams: SequencerOutlineMetadataEntry[]
    ) => {
      onYamlTextChange(
        applyEditedCallStep(yamlText, node, selectedDevice, nextAction, nextParams)
      );
    };

    return (
      <Card
        radius="sm"
        p="xs"
        style={{
          border: "1px solid var(--card-border)",
          background: "rgba(148, 163, 184, 0.04)",
        }}
        >
        <Stack gap={8}>
          <Stack gap={2}>
            <Text size="xs" c="dimmed">
              Device
            </Text>
            <Text
              size="sm"
              fw={500}
              style={{
                fontFamily:
                  "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
                wordBreak: "break-word",
              }}
            >
              {selectedDevice || "No device"}
            </Text>
          </Stack>

          <TextInput
            size="xs"
            label="Action"
            list={datalistId(node.id, "actions")}
            value={node.callDetail.action ?? ""}
            onChange={(event) =>
              updateCall(event.currentTarget.value, params)
            }
          />
          <datalist id={datalistId(node.id, "actions")}>
            {actionOptions.map((action) => (
              <option key={action} value={action} />
            ))}
          </datalist>

          <Group justify="space-between" align="center">
            <Text size="xs" fw={600}>
              Params
            </Text>
            <Button
              size="compact-xs"
              variant="light"
              leftSection={<IconPlus size={14} />}
              onClick={() =>
                updateCall(node.callDetail?.action ?? "", [
                  ...params,
                  {
                    name: nextParamName(params),
                    value: '""',
                  },
                ])
              }
            >
              Add
            </Button>
          </Group>
          {params.length <= 0 ? (
            <Text size="xs" c="dimmed">
              No params.
            </Text>
          ) : (
            <Stack gap={6}>
              {params.map((param, index) => (
                <div key={`${param.name}:${index}`} className="sequencer-var-chip">
                  <div className="sequencer-var-segment sequencer-var-name">
                    <TextInput
                      size="xs"
                      aria-label="Param name"
                      placeholder="param"
                      list={datalistId(node.id, "params")}
                      variant="unstyled"
                      value={param.name}
                      onChange={(event) => {
                        const next = params.map((entry, entryIndex) =>
                          entryIndex === index
                            ? { ...entry, name: event.currentTarget.value }
                            : entry
                        );
                        updateCall(node.callDetail?.action ?? "", next);
                      }}
                    />
                  </div>
                  <div className="sequencer-var-segment sequencer-var-value">
                    <TextInput
                      size="xs"
                      aria-label="Param value"
                      placeholder="value"
                      variant="unstyled"
                      value={renderValue(param.value)}
                      onChange={(event) => {
                        const next = params.map((entry, entryIndex) =>
                          entryIndex === index
                            ? { ...entry, value: event.currentTarget.value }
                            : entry
                        );
                        updateCall(node.callDetail?.action ?? "", next);
                      }}
                    />
                  </div>
                  <div className="sequencer-var-segment sequencer-var-remove">
                    <ActionIcon
                      size="sm"
                      variant="subtle"
                      color="red"
                      aria-label="Remove param"
                      onClick={() => {
                        const next = params.filter((_, entryIndex) => entryIndex !== index);
                        updateCall(node.callDetail?.action ?? "", next);
                      }}
                    >
                      <IconTrash size={14} />
                    </ActionIcon>
                  </div>
                </div>
              ))}
            </Stack>
          )}
          <datalist id={datalistId(node.id, "params")}>
            {paramNameOptions.map((paramName) => (
              <option key={paramName} value={paramName} />
            ))}
          </datalist>
        </Stack>
      </Card>
    );
  }

  if (node.sleepDetail) {
    return (
      <Card
        radius="sm"
        p="xs"
        style={{
          border: "1px solid var(--card-border)",
          background: "rgba(148, 163, 184, 0.04)",
        }}
      >
        <Stack gap={8}>
          <TextInput
            size="xs"
            label="Duration"
            value={renderValue(node.sleepDetail.duration)}
            onChange={(event) =>
              onYamlTextChange(
                applyEditedSleepStep(yamlText, node, event.currentTarget.value)
              )
            }
          />
        </Stack>
      </Card>
    );
  }

  if (node.repeatDetail) {
    return (
      <Card
        radius="sm"
        p="xs"
        style={{
          border: "1px solid var(--card-border)",
          background: "rgba(148, 163, 184, 0.04)",
        }}
      >
        <Stack gap={8}>
          <TextInput
            size="xs"
            label="Times"
            value={renderValue(node.repeatDetail.times)}
            onChange={(event) =>
              onYamlTextChange(
                applyEditedRepeatStep(yamlText, node, event.currentTarget.value)
              )
            }
          />
          <Text size="xs" c="dimmed">
            The nested body remains unchanged in Phase 2.
          </Text>
        </Stack>
      </Card>
    );
  }

  return null;
}
