import { ActionIcon, Button, Card, Group, Menu, Select, Stack, Text, TextInput } from "@mantine/core";
import { IconChevronDown, IconPlus, IconTrash } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import {
  applyEditedCallStep,
  applyEditedForStep,
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

function nextParamName(entries: ReadonlyArray<SequencerOutlineMetadataEntry>): string {
  const existing = new Set(entries.map((entry) => entry.name));
  let index = existing.size + 1;
  while (existing.has(`param_${index}`)) {
    index += 1;
  }
  return `param_${index}`;
}

function nextEntryName(
  prefix: string,
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): string {
  const existing = new Set(entries.map((entry) => entry.name));
  let index = existing.size + 1;
  while (existing.has(`${prefix}_${index}`)) {
    index += 1;
  }
  return `${prefix}_${index}`;
}

const FOR_SOURCE_OPTIONS = [
  { value: "generator", label: "Generator" },
  { value: "direct", label: "Direct (advanced)" },
] as const;

const FOR_GENERATOR_OPTIONS = [
  { value: "range", label: "range" },
  { value: "linspace", label: "linspace" },
  { value: "triangle", label: "triangle" },
  { value: "logspace", label: "logspace" },
  { value: "geomspace", label: "geomspace" },
  { value: "values", label: "values" },
  { value: "scan2d", label: "scan2d" },
] as const;

const SCALAR_FOR_FIELDS = ["value", "index", "u", "count"] as const;
const SCAN2D_FOR_FIELDS = ["x", "y", "row", "col", "index", "u", "v", "count"] as const;

function defaultForBindEntries(
  sourceMode: "generator" | "direct",
  generatorKind: string | null
): SequencerOutlineMetadataEntry[] {
  if (sourceMode === "direct") {
    return [
      { name: "value", value: '""' },
      { name: "index", value: '""' },
    ];
  }
  if (generatorKind === "scan2d") {
    return [
      { name: "x", value: "scan_x" },
      { name: "y", value: "scan_y" },
      { name: "row", value: "scan_row" },
      { name: "col", value: "scan_col" },
      { name: "index", value: "scan_idx" },
    ];
  }
  return [
    { name: "value", value: "loop_value" },
    { name: "index", value: "loop_index" },
  ];
}

function defaultForGeneratorConfig(generatorKind: string): SequencerOutlineMetadataEntry[] {
  switch (generatorKind) {
    case "range":
      return [
        { name: "start", value: "0" },
        { name: "stop", value: "10" },
        { name: "step", value: "1" },
      ];
    case "linspace":
      return [
        { name: "start", value: "0" },
        { name: "stop", value: "10" },
        { name: "num", value: "11" },
      ];
    case "triangle":
      return [
        { name: "start", value: "0" },
        { name: "stop", value: "10" },
        { name: "num", value: "11" },
      ];
    case "logspace":
      return [
        { name: "start", value: "0" },
        { name: "stop", value: "1" },
        { name: "num", value: "10" },
        { name: "base", value: "10" },
      ];
    case "geomspace":
      return [
        { name: "start", value: "1" },
        { name: "stop", value: "10" },
        { name: "num", value: "10" },
      ];
    case "values":
      return [{ name: "0", value: "0" }];
    case "scan2d":
      return [
        { name: "center.x", value: "0.0" },
        { name: "center.y", value: "0.0" },
        { name: "width", value: "1.0" },
        { name: "height", value: "1.0" },
        { name: "steps.x", value: "11" },
        { name: "steps.y", value: "11" },
        { name: "pattern", value: "serpentine" },
        { name: "order", value: "row_major" },
      ];
    default:
      return [];
  }
}

function defaultForGeneratorModifiers(
  generatorKind: string
): SequencerOutlineMetadataEntry[] {
  if (generatorKind === "scan2d") {
    return [];
  }
  return [];
}

function availableForBindFields(
  sourceMode: "generator" | "direct",
  generatorKind: string | null
): string[] {
  if (sourceMode === "direct") {
    return [...SCALAR_FOR_FIELDS];
  }
  if (generatorKind === "scan2d") {
    return [...SCAN2D_FOR_FIELDS];
  }
  return [...SCALAR_FOR_FIELDS];
}

function nextBindSourceField(
  sourceMode: "generator" | "direct",
  generatorKind: string | null,
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): string {
  const allowed = availableForBindFields(sourceMode, generatorKind);
  const used = new Set(entries.map((entry) => entry.name));
  const nextAllowed = allowed.find((field) => !used.has(field));
  if (nextAllowed) {
    return nextAllowed;
  }
  return nextEntryName("field", entries);
}

function scalarGeneratorFieldNames(generatorKind: string): string[] | null {
  switch (generatorKind) {
    case "range":
      return ["start", "stop", "step"];
    case "linspace":
    case "triangle":
      return ["start", "stop", "num"];
    case "logspace":
      return ["start", "stop", "num", "base"];
    case "geomspace":
      return ["start", "stop", "num"];
    default:
      return null;
  }
}

function valueByKey(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>,
  key: string
): string {
  return entries.find((entry) => entry.name === key)?.value ?? "";
}

function setEntryValue(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>,
  key: string,
  value: string
): SequencerOutlineMetadataEntry[] {
  let replaced = false;
  const next = entries.map((entry) => {
    if (entry.name !== key) {
      return entry;
    }
    replaced = true;
    return { ...entry, value };
  });
  if (replaced) {
    return next;
  }
  return [...entries, { name: key, value }];
}

export function EditableStepInspector({
  node,
  yamlText,
  onYamlTextChange,
  capabilitiesByDevice,
}: Props) {
  const [forDraftNodeId, setForDraftNodeId] = useState<string | null>(null);
  const [forBindDraft, setForBindDraft] = useState<SequencerOutlineMetadataEntry[]>([]);
  const [forSourceModeDraft, setForSourceModeDraft] = useState<"generator" | "direct">(
    "generator"
  );
  const [forIterableKindDraft, setForIterableKindDraft] = useState("");
  const [forDirectValueDraft, setForDirectValueDraft] = useState("");
  const [forModifierDraft, setForModifierDraft] = useState<SequencerOutlineMetadataEntry[]>(
    []
  );
  const [forIterableConfigDraft, setForIterableConfigDraft] = useState<
    SequencerOutlineMetadataEntry[]
  >([]);

  useEffect(() => {
    if (!node.forDetail) {
      return;
    }
    if (forDraftNodeId === node.id) {
      return;
    }
    setForDraftNodeId(node.id);
    setForBindDraft(node.forDetail.bind);
    setForSourceModeDraft(node.forDetail.sourceMode);
    setForIterableKindDraft(node.forDetail.generatorKind ?? "linspace");
    setForDirectValueDraft(node.forDetail.directValue ?? "");
    setForModifierDraft(node.forDetail.generatorModifiers);
    setForIterableConfigDraft(node.forDetail.iterableConfig);
  }, [node, forDraftNodeId]);

  if (node.callDetail) {
    const params = node.callDetail.params;
    const isSimpleParams = params.every((entry) => !entry.name.includes("."));
    const selectedDevice = node.callDetail.device ?? "";
    const actionOptions = (capabilitiesByDevice[selectedDevice] ?? []).map((member) => member.name);
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
    const paramNameSelectOptions = Array.from(
      new Set([...paramNameOptions, ...params.map((param) => param.name).filter(Boolean)])
    ).map((name) => ({ value: name, label: name }));

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

          {actionSelectOptions.length > 0 ? (
            <Select
              size="xs"
              label="Action"
              data={actionSelectOptions}
              value={node.callDetail.action ?? ""}
              allowDeselect={false}
              searchable
              comboboxProps={{ withinPortal: true }}
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
                    {paramNameSelectOptions.length > 0 ? (
                      <Select
                        size="xs"
                        aria-label="Param name"
                        data={paramNameSelectOptions}
                        value={param.name}
                        allowDeselect={false}
                        searchable
                        comboboxProps={{ withinPortal: true }}
                        onChange={(value) => {
                          if (value === null) {
                            return;
                          }
                          const next = params.map((entry, entryIndex) =>
                            entryIndex === index ? { ...entry, name: value } : entry
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
        </Stack>
      </Card>
    );
  }

  if (node.forDetail) {
    const usingDraft = forDraftNodeId === node.id;
    const bind = usingDraft ? forBindDraft : node.forDetail.bind;
    const sourceMode = usingDraft ? forSourceModeDraft : node.forDetail.sourceMode;
    const iterableConfig = usingDraft
      ? forIterableConfigDraft
      : node.forDetail.iterableConfig;
    const iterableKind = usingDraft
      ? forIterableKindDraft
      : node.forDetail.generatorKind ?? "linspace";
    const directValue = usingDraft ? forDirectValueDraft : node.forDetail.directValue ?? "";
    const generatorModifiers = usingDraft
      ? forModifierDraft
      : node.forDetail.generatorModifiers;
    const updateFor = (
      nextBind: SequencerOutlineMetadataEntry[],
      nextSourceMode: "generator" | "direct",
      nextIterableKind: string,
      nextDirectValue: string,
      nextGeneratorModifiers: SequencerOutlineMetadataEntry[],
      nextIterableConfig: SequencerOutlineMetadataEntry[]
    ) => {
      setForDraftNodeId(node.id);
      setForBindDraft(nextBind);
      setForSourceModeDraft(nextSourceMode);
      setForIterableKindDraft(nextIterableKind);
      setForDirectValueDraft(nextDirectValue);
      setForModifierDraft(nextGeneratorModifiers);
      setForIterableConfigDraft(nextIterableConfig);
      onYamlTextChange(
        applyEditedForStep(
          yamlText,
          node,
          nextBind,
          nextSourceMode,
          nextIterableKind,
          nextDirectValue,
          nextGeneratorModifiers,
          nextIterableConfig
        )
      );
    };
    const bindFieldOptions = availableForBindFields(sourceMode, iterableKind).map((field) => ({
      value: field,
      label: field,
    }));
    const unusedBindFieldOptions = bindFieldOptions.filter(
      (option) => !bind.some((entry) => entry.name === option.value)
    );
    const scalarFieldNames =
      sourceMode === "generator" ? scalarGeneratorFieldNames(iterableKind) : null;

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
          <Group justify="space-between" align="center">
            <Text size="xs" fw={600}>
              Bind
            </Text>
            {sourceMode === "generator" ? (
              <Menu shadow="md" withArrow position="bottom-end" zIndex={1000}>
                <Menu.Target>
                  <Button
                    size="compact-xs"
                    variant="light"
                    leftSection={<IconPlus size={14} />}
                    rightSection={<IconChevronDown size={12} />}
                    disabled={unusedBindFieldOptions.length <= 0}
                  >
                    Add
                  </Button>
                </Menu.Target>
                <Menu.Dropdown>
                  {unusedBindFieldOptions.map((option) => (
                    <Menu.Item
                      key={option.value}
                      onClick={() =>
                        updateFor(
                          [
                            ...bind,
                            {
                              name: option.value,
                              value: '""',
                            },
                          ],
                          sourceMode,
                          iterableKind,
                          directValue,
                          generatorModifiers,
                          iterableConfig
                        )
                      }
                    >
                      {option.label}
                    </Menu.Item>
                  ))}
                </Menu.Dropdown>
              </Menu>
            ) : (
              <Button
                size="compact-xs"
                variant="light"
                leftSection={<IconPlus size={14} />}
                onClick={() =>
                  updateFor(
                    [
                      ...bind,
                      {
                        name: nextBindSourceField(sourceMode, iterableKind, bind),
                        value: '""',
                      },
                    ],
                    sourceMode,
                    iterableKind,
                    directValue,
                    generatorModifiers,
                    iterableConfig
                  )
                }
              >
                Add
              </Button>
            )}
          </Group>
          {bind.length <= 0 ? (
            <Text size="xs" c="dimmed">
              No bind entries.
            </Text>
          ) : (
            <Stack gap={6}>
              {bind.map((entry, index) => (
                <div key={`${entry.name}:${index}`} className="sequencer-var-chip">
                  <div className="sequencer-var-segment sequencer-var-name">
                    {sourceMode === "generator" ? (
                      <Select
                        size="xs"
                        aria-label="Bind source"
                        data={bindFieldOptions.map((option) => ({
                          ...option,
                          disabled: bind.some(
                            (item, itemIndex) =>
                              itemIndex !== index && item.name === option.value
                          ),
                        }))}
                        value={entry.name}
                        onChange={(value) => {
                          if (value === null) {
                            return;
                          }
                          const next = bind.map((item, itemIndex) =>
                            itemIndex === index ? { ...item, name: value } : item
                          );
                          updateFor(
                            next,
                            sourceMode,
                            iterableKind,
                            directValue,
                            generatorModifiers,
                            iterableConfig
                          );
                        }}
                        allowDeselect={false}
                        searchable={false}
                        comboboxProps={{ withinPortal: true }}
                      />
                    ) : (
                      <TextInput
                        size="xs"
                        aria-label="Bind source"
                        placeholder="source"
                        variant="unstyled"
                        value={entry.name}
                        onChange={(event) => {
                          const next = bind.map((item, itemIndex) =>
                            itemIndex === index
                              ? { ...item, name: event.currentTarget.value }
                              : item
                          );
                          updateFor(
                            next,
                            sourceMode,
                            iterableKind,
                            directValue,
                            generatorModifiers,
                            iterableConfig
                          );
                        }}
                      />
                    )}
                  </div>
                  <div className="sequencer-var-segment sequencer-var-value">
                    <TextInput
                      size="xs"
                      aria-label="Bind target"
                      placeholder="target"
                      variant="unstyled"
                      value={renderValue(entry.value)}
                      onChange={(event) => {
                        const next = bind.map((item, itemIndex) =>
                          itemIndex === index
                            ? { ...item, value: event.currentTarget.value }
                            : item
                        );
                        updateFor(
                          next,
                          sourceMode,
                          iterableKind,
                          directValue,
                          generatorModifiers,
                          iterableConfig
                        );
                      }}
                    />
                  </div>
                  <div className="sequencer-var-segment sequencer-var-remove">
                    <ActionIcon
                      size="sm"
                      variant="subtle"
                      color="red"
                      aria-label="Remove bind"
                      onClick={() => {
                        const next = bind.filter((_, itemIndex) => itemIndex !== index);
                        updateFor(
                          next,
                          sourceMode,
                          iterableKind,
                          directValue,
                          generatorModifiers,
                          iterableConfig
                        );
                      }}
                    >
                      <IconTrash size={14} />
                    </ActionIcon>
                  </div>
                </div>
              ))}
            </Stack>
          )}

          <Select
            size="xs"
            label="Source"
            data={FOR_SOURCE_OPTIONS.map((option) => ({
              value: option.value,
              label: option.label,
            }))}
            value={sourceMode}
            allowDeselect={false}
            searchable={false}
            onChange={(value) => {
              const nextMode =
                value === "direct" ? "direct" : "generator";
              if (nextMode === "direct") {
                updateFor(
                  defaultForBindEntries("direct", null),
                  "direct",
                  "linspace",
                  directValue || "${points}",
                  [],
                  []
                );
                return;
              }
              const nextKind = iterableKind || "linspace";
              updateFor(
                defaultForBindEntries("generator", nextKind),
                "generator",
                nextKind,
                "",
                defaultForGeneratorModifiers(nextKind),
                defaultForGeneratorConfig(nextKind)
              );
            }}
          />
          {sourceMode === "direct" ? (
            <>
              <TextInput
                size="xs"
                label="Iterable expression"
                value={directValue}
                onChange={(event) =>
                  updateFor(
                    bind,
                    sourceMode,
                    iterableKind,
                    event.currentTarget.value,
                    [],
                    []
                  )
                }
              />
              <Text size="xs" c="dimmed">
                Use this for an expression or prebuilt iterable/record list.
              </Text>
            </>
          ) : (
            <>
              <Select
                size="xs"
                label="Generator kind"
                data={FOR_GENERATOR_OPTIONS.map((option) => ({
                  value: option.value,
                  label: option.label,
                }))}
                value={iterableKind}
                allowDeselect={false}
                searchable={false}
                onChange={(value) => {
                  const nextKind = value ?? "linspace";
                  updateFor(
                    defaultForBindEntries("generator", nextKind),
                    "generator",
                    nextKind,
                    "",
                    defaultForGeneratorModifiers(nextKind),
                    defaultForGeneratorConfig(nextKind)
                  );
                }}
              />

              {scalarFieldNames ? (
                <Stack gap={6}>
                  {scalarFieldNames.map((fieldName) => (
                    <TextInput
                      key={fieldName}
                      size="xs"
                      label={fieldName}
                      value={valueByKey(iterableConfig, fieldName)}
                      onChange={(event) => {
                        updateFor(
                          bind,
                          sourceMode,
                          iterableKind,
                          directValue,
                          generatorModifiers,
                          setEntryValue(
                            iterableConfig,
                            fieldName,
                            event.currentTarget.value
                          )
                        );
                      }}
                    />
                  ))}
                </Stack>
              ) : iterableKind === "values" ? (
                <>
                  <Group justify="space-between" align="center">
                    <Text size="xs" fw={600}>
                      Values
                    </Text>
                    <Button
                      size="compact-xs"
                      variant="light"
                      leftSection={<IconPlus size={14} />}
                      onClick={() =>
                        updateFor(bind, sourceMode, iterableKind, directValue, generatorModifiers, [
                          ...iterableConfig,
                          {
                            name: String(iterableConfig.length),
                            value: '""',
                          },
                        ])
                      }
                    >
                      Add
                    </Button>
                  </Group>
                  {iterableConfig.length <= 0 ? (
                    <Text size="xs" c="dimmed">
                      No values.
                    </Text>
                  ) : (
                    <Stack gap={6}>
                      {iterableConfig.map((entry, index) => (
                        <div key={`${entry.name}:${index}`} className="sequencer-var-chip">
                          <div className="sequencer-var-segment sequencer-var-value">
                            <TextInput
                              size="xs"
                              aria-label="Iterable value"
                              placeholder="value"
                              variant="unstyled"
                              value={renderValue(entry.value)}
                              onChange={(event) => {
                                const next = iterableConfig.map((item, itemIndex) =>
                                  itemIndex === index
                                    ? { ...item, value: event.currentTarget.value }
                                    : item
                                );
                                updateFor(
                                  bind,
                                  sourceMode,
                                  iterableKind,
                                  directValue,
                                  generatorModifiers,
                                  next.map((item, itemIndex) => ({
                                    ...item,
                                    name: String(itemIndex),
                                  }))
                                );
                              }}
                            />
                          </div>
                          <div className="sequencer-var-segment sequencer-var-remove">
                            <ActionIcon
                              size="sm"
                              variant="subtle"
                              color="red"
                              aria-label="Remove iterable value"
                              onClick={() => {
                                const next = iterableConfig
                                  .filter((_, itemIndex) => itemIndex !== index)
                                  .map((item, itemIndex) => ({
                                    ...item,
                                    name: String(itemIndex),
                                  }));
                                updateFor(
                                  bind,
                                  sourceMode,
                                  iterableKind,
                                  directValue,
                                  generatorModifiers,
                                  next
                                );
                              }}
                            >
                              <IconTrash size={14} />
                            </ActionIcon>
                          </div>
                        </div>
                      ))}
                    </Stack>
                  )}
                </>
              ) : (
                <>
                  <Group justify="space-between" align="center">
                    <Text size="xs" fw={600}>
                      Iterable config
                    </Text>
                    <Button
                      size="compact-xs"
                      variant="light"
                      leftSection={<IconPlus size={14} />}
                      onClick={() =>
                        updateFor(
                          bind,
                          sourceMode,
                          iterableKind,
                          directValue,
                          generatorModifiers,
                          [
                            ...iterableConfig,
                            {
                              name: nextEntryName("field", iterableConfig),
                              value: '""',
                            },
                          ]
                        )
                      }
                    >
                      Add
                    </Button>
                  </Group>
                  {iterableConfig.length <= 0 ? (
                    <Text size="xs" c="dimmed">
                      No iterable config entries.
                    </Text>
                  ) : (
                    <Stack gap={6}>
                      {iterableConfig.map((entry, index) => (
                        <div key={`${entry.name}:${index}`} className="sequencer-var-chip">
                          <div className="sequencer-var-segment sequencer-var-name">
                            <TextInput
                              size="xs"
                              aria-label="Iterable config key"
                              placeholder="key"
                              variant="unstyled"
                              value={entry.name}
                              onChange={(event) => {
                                const next = iterableConfig.map((item, itemIndex) =>
                                  itemIndex === index
                                    ? { ...item, name: event.currentTarget.value }
                                    : item
                                );
                                updateFor(
                                  bind,
                                  sourceMode,
                                  iterableKind,
                                  directValue,
                                  generatorModifiers,
                                  next
                                );
                              }}
                            />
                          </div>
                          <div className="sequencer-var-segment sequencer-var-value">
                            <TextInput
                              size="xs"
                              aria-label="Iterable config value"
                              placeholder="value"
                              variant="unstyled"
                              value={renderValue(entry.value)}
                              onChange={(event) => {
                                const next = iterableConfig.map((item, itemIndex) =>
                                  itemIndex === index
                                    ? { ...item, value: event.currentTarget.value }
                                    : item
                                );
                                updateFor(
                                  bind,
                                  sourceMode,
                                  iterableKind,
                                  directValue,
                                  generatorModifiers,
                                  next
                                );
                              }}
                            />
                          </div>
                          <div className="sequencer-var-segment sequencer-var-remove">
                            <ActionIcon
                              size="sm"
                              variant="subtle"
                              color="red"
                              aria-label="Remove iterable config"
                              onClick={() => {
                                const next = iterableConfig.filter(
                                  (_, itemIndex) => itemIndex !== index
                                );
                                updateFor(
                                  bind,
                                  sourceMode,
                                  iterableKind,
                                  directValue,
                                  generatorModifiers,
                                  next
                                );
                              }}
                            >
                              <IconTrash size={14} />
                            </ActionIcon>
                          </div>
                        </div>
                      ))}
                    </Stack>
                  )}
                  {iterableKind === "scan2d" ? (
                    <Text size="xs" c="dimmed">
                      scan2d uses the advanced key/value editor in this phase.
                    </Text>
                  ) : null}
                </>
              )}
            </>
          )}

          <Text size="xs" c="dimmed">
            The nested body remains unchanged in this phase.
          </Text>
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
