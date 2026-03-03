import {
  ActionIcon,
  Button,
  Card,
  Group,
  Menu,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { applyEditedCallStep } from "../editing";
import {
  getCapabilityParamDefaultValue,
  getCapabilityParamPlaceholder,
  nextParamName,
  renderValue,
} from "../editor_helpers";
import type {
  SequencerOutlineMetadataEntry,
  SequencerStepOutlineNode,
} from "../types";
import type { CapabilityMember, CapabilityParam } from "../../../types";

type Props = {
  node: SequencerStepOutlineNode;
  yamlText: string;
  onYamlTextChange: (value: string) => void;
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
};

const cardStyle = {
  border: "1px solid var(--card-border)",
  background: "rgba(148, 163, 184, 0.04)",
} as const;

export function CallStepEditor({
  node,
  yamlText,
  onYamlTextChange,
  capabilitiesByDevice,
}: Props) {
  if (!node.callDetail) {
    return null;
  }

  const params = node.callDetail.params;
  const isSimpleParams = params.every((entry) => !entry.name.includes("."));
  const selectedDevice = node.callDetail.device ?? "";
  const actionOptions = (capabilitiesByDevice[selectedDevice] ?? []).map(
    (member) => member.name
  );
  const actionSelectOptions = Array.from(
    new Set([...(node.callDetail.action ? [node.callDetail.action] : []), ...actionOptions])
  ).map((action) => ({ value: action, label: action }));
  const selectedActionMember =
    (capabilitiesByDevice[selectedDevice] ?? []).find(
      (member) => member.name === (node.callDetail?.action ?? "")
    ) ?? null;
  const paramNameOptions = (selectedActionMember?.params ?? [])
    .map((param) => param.name)
    .filter((name): name is string => typeof name === "string" && name.trim().length > 0);
  const paramSpecsByName = new Map(
    (selectedActionMember?.params ?? [])
      .filter(
        (param): param is CapabilityParam =>
          typeof param?.name === "string" && param.name.trim().length > 0
      )
      .map((param) => [param.name, param] as const)
  );
  const paramNameSelectOptions = Array.from(
    new Set([...paramNameOptions, ...params.map((param) => param.name).filter(Boolean)])
  ).map((name) => ({ value: name, label: name }));
  const unusedParamNameOptions = paramNameOptions.filter(
    (name) => !params.some((param) => param.name === name)
  );

  if (!isSimpleParams) {
    return (
      <Card radius="sm" p="xs" style={cardStyle}>
        <Stack gap={6}>
          <Text size="xs" c="dimmed">
            This call uses nested parameter keys, so Phase 2 keeps it read-only for
            now.
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

  const buildParamEntry = (name: string): SequencerOutlineMetadataEntry => ({
    name,
    value: getCapabilityParamDefaultValue(paramSpecsByName.get(name)),
  });

  return (
    <Card radius="sm" p="xs" style={cardStyle}>
      <Stack gap={8}>
        <Stack gap={2}>
          <Text size="xs" c="dimmed">
            Device
          </Text>
          <Text
            size="sm"
            fw={500}
            style={{
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
              wordBreak: "break-word",
            }}
          >
            {selectedDevice || "No device"}
          </Text>
        </Stack>

        {actionSelectOptions.length > 0 ? (
          <Select
            size="xs"
            label="Action"
            data={actionSelectOptions}
            value={node.callDetail.action ?? ""}
            allowDeselect={false}
            searchable
            comboboxProps={{ withinPortal: false }}
            onChange={(value) => {
              if (value === null) {
                return;
              }
              updateCall(value, params);
            }}
          />
        ) : (
          <TextInput
            size="xs"
            label="Action"
            value={node.callDetail.action ?? ""}
            onChange={(event) => updateCall(event.currentTarget.value, params)}
          />
        )}
        <Group justify="space-between" align="center">
          <Text size="xs" fw={600}>
            Params
          </Text>
          <Menu shadow="md" withArrow position="bottom-end" zIndex={1000}>
            <Menu.Target>
              <Button
                size="compact-xs"
                variant="light"
                leftSection={<IconPlus size={14} />}
              >
                Add
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              {unusedParamNameOptions.map((name) => (
                <Menu.Item
                  key={name}
                  onClick={() =>
                    updateCall(node.callDetail?.action ?? "", [...params, buildParamEntry(name)])
                  }
                >
                  {name}
                </Menu.Item>
              ))}
              <Menu.Item
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
                Custom
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
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
                  {paramNameSelectOptions.length > 0 ? (
                    <Select
                      size="xs"
                      aria-label="Param name"
                      data={paramNameSelectOptions}
                      value={param.name}
                      allowDeselect={false}
                      searchable
                      comboboxProps={{ withinPortal: false }}
                      onChange={(value) => {
                        if (value === null) {
                          return;
                        }
                        const next = params.map((entry, entryIndex) =>
                          entryIndex === index
                            ? {
                                ...entry,
                                name: value,
                                value:
                                  entry.name === value
                                    ? entry.value
                                    : getCapabilityParamDefaultValue(paramSpecsByName.get(value)),
                              }
                            : entry
                        );
                        updateCall(node.callDetail?.action ?? "", next);
                      }}
                    />
                  ) : (
                    <TextInput
                      size="xs"
                      aria-label="Param name"
                      placeholder="param"
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
                  )}
                </div>
                <div className="sequencer-var-segment sequencer-var-value">
                  <TextInput
                    size="xs"
                    aria-label="Param value"
                    placeholder={getCapabilityParamPlaceholder(paramSpecsByName.get(param.name))}
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
      </Stack>
    </Card>
  );
}
