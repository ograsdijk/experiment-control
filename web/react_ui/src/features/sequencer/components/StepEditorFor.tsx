import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Group,
  Menu,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
} from "@mantine/core";
import { IconChevronDown, IconPlus, IconTrash } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { applyEditedForStep } from "../editing";
import {
  FOR_GENERATOR_OPTIONS,
  FOR_SOURCE_OPTIONS,
  SCAN2D_FORM_OPTIONS,
  SCAN2D_ORDER_OPTIONS,
  SCAN2D_PATTERN_OPTIONS,
  SCAN2D_RESOLUTION_OPTIONS,
} from "../editor_constants";
import {
  availableForBindFields,
  buildScan2dConfig,
  defaultForBindEntries,
  defaultForGeneratorConfig,
  defaultForGeneratorModifiers,
  detectScan2dForm,
  detectScan2dResolutionMode,
  duplicateNameSet,
  isBlank,
  isPositiveIntegerLiteral,
  modifierValue,
  nextBindSourceField,
  nextEntryName,
  removeEntry,
  renderValue,
  scalarGeneratorFieldNames,
  setEntryValue,
  setModifierValue,
  valueByKey,
} from "../editor_helpers";
import type { SequencerOutlineMetadataEntry, SequencerStepOutlineNode } from "../types";
import { useStepDraftSync } from "../useStepDraftSync";

type Props = {
  node: SequencerStepOutlineNode;
  yamlText: string;
  onYamlTextChange: (value: string) => void;
};

const cardStyle = {
  border: "1px solid var(--card-border)",
  background: "rgba(148, 163, 184, 0.04)",
} as const;

export function ForStepEditor({ node, yamlText, onYamlTextChange }: Props) {
  const [bindDraft, setBindDraft] = useState<SequencerOutlineMetadataEntry[]>([]);
  const [sourceModeDraft, setSourceModeDraft] = useState<"generator" | "direct">(
    "generator"
  );
  const [iterableKindDraft, setIterableKindDraft] = useState("");
  const [directValueDraft, setDirectValueDraft] = useState("");
  const [modifierDraft, setModifierDraft] = useState<SequencerOutlineMetadataEntry[]>([]);
  const [iterableConfigDraft, setIterableConfigDraft] = useState<
    SequencerOutlineMetadataEntry[]
  >([]);
  const { usingDraft, needsSync, markCurrent } = useStepDraftSync(node.id, node.snippet);

  useEffect(() => {
    if (!node.forDetail) {
      return;
    }
    if (!needsSync) {
      return;
    }
    markCurrent();
    setBindDraft(node.forDetail.bind);
    setSourceModeDraft(node.forDetail.sourceMode);
    setIterableKindDraft(node.forDetail.generatorKind ?? "linspace");
    setDirectValueDraft(node.forDetail.directValue ?? "");
    setModifierDraft(node.forDetail.generatorModifiers);
    setIterableConfigDraft(node.forDetail.iterableConfig);
  }, [markCurrent, needsSync, node]);

  if (!node.forDetail) {
    return null;
  }

  const bind = usingDraft ? bindDraft : node.forDetail.bind;
  const sourceMode = usingDraft ? sourceModeDraft : node.forDetail.sourceMode;
  const iterableConfig = usingDraft ? iterableConfigDraft : node.forDetail.iterableConfig;
  const iterableKind = usingDraft
    ? iterableKindDraft
    : node.forDetail.generatorKind ?? "linspace";
  const directValue = usingDraft ? directValueDraft : node.forDetail.directValue ?? "";
  const generatorModifiers = usingDraft
    ? modifierDraft
    : node.forDetail.generatorModifiers;

  const updateFor = (
    nextBind: SequencerOutlineMetadataEntry[],
    nextSourceMode: "generator" | "direct",
    nextIterableKind: string,
    nextDirectValue: string,
    nextGeneratorModifiers: SequencerOutlineMetadataEntry[],
    nextIterableConfig: SequencerOutlineMetadataEntry[]
  ) => {
    markCurrent();
    setBindDraft(nextBind);
    setSourceModeDraft(nextSourceMode);
    setIterableKindDraft(nextIterableKind);
    setDirectValueDraft(nextDirectValue);
    setModifierDraft(nextGeneratorModifiers);
    setIterableConfigDraft(nextIterableConfig);
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
  const showScalarModifiers = sourceMode === "generator" && iterableKind !== "scan2d";
  const scan2dForm = detectScan2dForm(iterableConfig);
  const scan2dResolutionMode = detectScan2dResolutionMode(iterableConfig);
  const scan2dPattern = valueByKey(iterableConfig, "pattern") || "serpentine";
  const scan2dOrder = valueByKey(iterableConfig, "order") || "row_major";
  const duplicateBindNames = duplicateNameSet(bind);
  const blankBindTargets = bind.filter((entry) => isBlank(entry.value)).length;
  const bindIssueCount = duplicateBindNames.size + blankBindTargets;
  const directValueError = sourceMode === "direct" && isBlank(directValue);
  const scalarFieldError = (fieldName: string, value: string): string | undefined => {
    if (isBlank(value)) {
      return `${fieldName} is required.`;
    }
    if (
      (fieldName === "num" || fieldName.endsWith(".num") || fieldName.startsWith("steps.")) &&
      /^[\d.\-]+$/.test(value.trim()) &&
      !isPositiveIntegerLiteral(value)
    ) {
      return `${fieldName} must be a positive integer.`;
    }
    return undefined;
  };

  return (
    <Card radius="sm" p="xs" style={cardStyle}>
      <Stack gap={8}>
        <Group justify="space-between" align="center">
          <Group gap={6} align="center">
            <Text size="xs" fw={600}>
              Bind
            </Text>
            {bindIssueCount > 0 ? (
              <Badge size="xs" color="red" variant="light">
                {bindIssueCount} issue{bindIssueCount === 1 ? "" : "s"}
              </Badge>
            ) : null}
          </Group>
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
                        [...bind, { name: option.value, value: '""' }],
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
              <Stack key={`bind-${index}`} gap={4}>
                <div className="sequencer-var-chip">
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
                      comboboxProps={{ withinPortal: false }}
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
                {duplicateBindNames.has(entry.name.trim()) ? (
                  <Text size="xs" c="red">
                    Bind source fields must be unique.
                  </Text>
                ) : null}
                {isBlank(entry.value) ? (
                  <Text size="xs" c="red">
                    Bind target is required.
                  </Text>
                ) : null}
              </Stack>
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
          comboboxProps={{ withinPortal: false }}
          onChange={(value) => {
            const nextMode = value === "direct" ? "direct" : "generator";
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
              error={directValueError ? "Iterable expression is required." : undefined}
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
              comboboxProps={{ withinPortal: false }}
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
            {showScalarModifiers ? (
              <Stack gap={6}>
                <Text size="xs" fw={600}>
                  Modifiers
                </Text>
                <Group grow align="flex-end">
                  <Switch
                    size="sm"
                    label="Shuffle"
                    checked={Boolean(modifierValue(generatorModifiers, "shuffle"))}
                    onChange={(event) =>
                      updateFor(
                        bind,
                        sourceMode,
                        iterableKind,
                        directValue,
                        setModifierValue(
                          generatorModifiers,
                          "shuffle",
                          event.currentTarget.checked ? "true" : null
                        ),
                        iterableConfig
                      )
                    }
                  />
                  <Switch
                    size="sm"
                    label="Serpentine"
                    checked={Boolean(modifierValue(generatorModifiers, "serpentine"))}
                    onChange={(event) =>
                      updateFor(
                        bind,
                        sourceMode,
                        iterableKind,
                        directValue,
                        setModifierValue(
                          generatorModifiers,
                          "serpentine",
                          event.currentTarget.checked ? "true" : null
                        ),
                        iterableConfig
                      )
                    }
                  />
                </Group>
                <Group grow align="flex-end">
                  <TextInput
                    size="xs"
                    label="Seed"
                    value={modifierValue(generatorModifiers, "seed")}
                    onChange={(event) =>
                      updateFor(
                        bind,
                        sourceMode,
                        iterableKind,
                        directValue,
                        setModifierValue(
                          generatorModifiers,
                          "seed",
                          event.currentTarget.value
                        ),
                        iterableConfig
                      )
                    }
                  />
                  <TextInput
                    size="xs"
                    label="Offset"
                    value={modifierValue(generatorModifiers, "offset")}
                    onChange={(event) =>
                      updateFor(
                        bind,
                        sourceMode,
                        iterableKind,
                        directValue,
                        setModifierValue(
                          generatorModifiers,
                          "offset",
                          event.currentTarget.value
                        ),
                        iterableConfig
                      )
                    }
                  />
                </Group>
                <Text size="xs" c="dimmed">
                  In 1D, serpentine only changes alternating passes of a nested inner
                  loop.
                </Text>
              </Stack>
            ) : null}

            {scalarFieldNames ? (
              <Stack gap={6}>
                {scalarFieldNames.map((fieldName) => (
                  <TextInput
                    key={fieldName}
                    size="xs"
                    label={fieldName}
                    value={valueByKey(iterableConfig, fieldName)}
                    error={scalarFieldError(fieldName, valueByKey(iterableConfig, fieldName))}
                    onChange={(event) =>
                      updateFor(
                        bind,
                        sourceMode,
                        iterableKind,
                        directValue,
                        generatorModifiers,
                        setEntryValue(iterableConfig, fieldName, event.currentTarget.value)
                      )
                    }
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
                      updateFor(
                        bind,
                        sourceMode,
                        iterableKind,
                        directValue,
                        generatorModifiers,
                        [...iterableConfig, { name: String(iterableConfig.length), value: '""' }]
                      )
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
            ) : iterableKind === "scan2d" ? (
              <>
                <Select
                  size="xs"
                  label="Scan form"
                  data={SCAN2D_FORM_OPTIONS.map((option) => ({
                    value: option.value,
                    label: option.label,
                  }))}
                  value={scan2dForm}
                  allowDeselect={false}
                  searchable={false}
                  comboboxProps={{ withinPortal: false }}
                  onChange={(value) => {
                    const nextForm = value === "explicit" ? "explicit" : "shorthand";
                    updateFor(
                      bind,
                      sourceMode,
                      iterableKind,
                      directValue,
                      generatorModifiers,
                      buildScan2dConfig({
                        previous: iterableConfig,
                        form: nextForm,
                        resolutionMode: scan2dResolutionMode,
                      })
                    );
                  }}
                />
                {scan2dForm === "shorthand" ? (
                  <>
                    <Group grow align="flex-end">
                      <TextInput
                        size="xs"
                        label="Center x"
                          value={valueByKey(iterableConfig, "center.x")}
                        error={scalarFieldError("center.x", valueByKey(iterableConfig, "center.x"))}
                        onChange={(event) =>
                          updateFor(
                            bind,
                            sourceMode,
                            iterableKind,
                            directValue,
                            generatorModifiers,
                            setEntryValue(iterableConfig, "center.x", event.currentTarget.value)
                          )
                        }
                      />
                      <TextInput
                        size="xs"
                        label="Center y"
                          value={valueByKey(iterableConfig, "center.y")}
                        error={scalarFieldError("center.y", valueByKey(iterableConfig, "center.y"))}
                        onChange={(event) =>
                          updateFor(
                            bind,
                            sourceMode,
                            iterableKind,
                            directValue,
                            generatorModifiers,
                            setEntryValue(iterableConfig, "center.y", event.currentTarget.value)
                          )
                        }
                      />
                    </Group>
                    <Group grow align="flex-end">
                      <TextInput
                        size="xs"
                        label="Width"
                        value={valueByKey(iterableConfig, "width")}
                        error={scalarFieldError("width", valueByKey(iterableConfig, "width"))}
                        onChange={(event) =>
                          updateFor(
                            bind,
                            sourceMode,
                            iterableKind,
                            directValue,
                            generatorModifiers,
                            setEntryValue(iterableConfig, "width", event.currentTarget.value)
                          )
                        }
                      />
                      <TextInput
                        size="xs"
                        label="Height"
                        value={valueByKey(iterableConfig, "height")}
                        error={scalarFieldError("height", valueByKey(iterableConfig, "height"))}
                        onChange={(event) =>
                          updateFor(
                            bind,
                            sourceMode,
                            iterableKind,
                            directValue,
                            generatorModifiers,
                            setEntryValue(iterableConfig, "height", event.currentTarget.value)
                          )
                        }
                      />
                    </Group>
                    <Select
                      size="xs"
                      label="Resolution"
                      data={SCAN2D_RESOLUTION_OPTIONS.map((option) => ({
                        value: option.value,
                        label: option.label,
                      }))}
                      value={scan2dResolutionMode}
                      allowDeselect={false}
                      searchable={false}
                      comboboxProps={{ withinPortal: false }}
                      onChange={(value) => {
                        const nextMode = value === "pitch" ? "pitch" : "steps";
                        updateFor(
                          bind,
                          sourceMode,
                          iterableKind,
                          directValue,
                          generatorModifiers,
                          buildScan2dConfig({
                            previous: iterableConfig,
                            form: "shorthand",
                            resolutionMode: nextMode,
                          })
                        );
                      }}
                    />
                    <Group grow align="flex-end">
                      <TextInput
                        size="xs"
                        label={scan2dResolutionMode === "pitch" ? "Pitch x" : "Steps x"}
                        value={valueByKey(
                          iterableConfig,
                          scan2dResolutionMode === "pitch" ? "pitch.x" : "steps.x"
                        )}
                        error={scalarFieldError(
                          scan2dResolutionMode === "pitch" ? "pitch.x" : "steps.x",
                          valueByKey(
                            iterableConfig,
                            scan2dResolutionMode === "pitch" ? "pitch.x" : "steps.x"
                          )
                        )}
                        onChange={(event) =>
                          updateFor(
                            bind,
                            sourceMode,
                            iterableKind,
                            directValue,
                            generatorModifiers,
                            setEntryValue(
                              iterableConfig,
                              scan2dResolutionMode === "pitch" ? "pitch.x" : "steps.x",
                              event.currentTarget.value
                            )
                          )
                        }
                      />
                      <TextInput
                        size="xs"
                        label={scan2dResolutionMode === "pitch" ? "Pitch y" : "Steps y"}
                        value={valueByKey(
                          iterableConfig,
                          scan2dResolutionMode === "pitch" ? "pitch.y" : "steps.y"
                        )}
                        error={scalarFieldError(
                          scan2dResolutionMode === "pitch" ? "pitch.y" : "steps.y",
                          valueByKey(
                            iterableConfig,
                            scan2dResolutionMode === "pitch" ? "pitch.y" : "steps.y"
                          )
                        )}
                        onChange={(event) =>
                          updateFor(
                            bind,
                            sourceMode,
                            iterableKind,
                            directValue,
                            generatorModifiers,
                            setEntryValue(
                              iterableConfig,
                              scan2dResolutionMode === "pitch" ? "pitch.y" : "steps.y",
                              event.currentTarget.value
                            )
                          )
                        }
                      />
                    </Group>
                  </>
                ) : (
                  <>
                    <Group grow align="flex-end">
                      <TextInput size="xs" label="x start" value={valueByKey(iterableConfig, "x.linspace.start")} error={scalarFieldError("x.linspace.start", valueByKey(iterableConfig, "x.linspace.start"))} onChange={(event) => updateFor(bind, sourceMode, iterableKind, directValue, generatorModifiers, setEntryValue(iterableConfig, "x.linspace.start", event.currentTarget.value))} />
                      <TextInput size="xs" label="x stop" value={valueByKey(iterableConfig, "x.linspace.stop")} error={scalarFieldError("x.linspace.stop", valueByKey(iterableConfig, "x.linspace.stop"))} onChange={(event) => updateFor(bind, sourceMode, iterableKind, directValue, generatorModifiers, setEntryValue(iterableConfig, "x.linspace.stop", event.currentTarget.value))} />
                      <TextInput size="xs" label="x num" value={valueByKey(iterableConfig, "x.linspace.num")} error={scalarFieldError("x.linspace.num", valueByKey(iterableConfig, "x.linspace.num"))} onChange={(event) => updateFor(bind, sourceMode, iterableKind, directValue, generatorModifiers, setEntryValue(iterableConfig, "x.linspace.num", event.currentTarget.value))} />
                    </Group>
                    <Group grow align="flex-end">
                      <TextInput size="xs" label="y start" value={valueByKey(iterableConfig, "y.linspace.start")} error={scalarFieldError("y.linspace.start", valueByKey(iterableConfig, "y.linspace.start"))} onChange={(event) => updateFor(bind, sourceMode, iterableKind, directValue, generatorModifiers, setEntryValue(iterableConfig, "y.linspace.start", event.currentTarget.value))} />
                      <TextInput size="xs" label="y stop" value={valueByKey(iterableConfig, "y.linspace.stop")} error={scalarFieldError("y.linspace.stop", valueByKey(iterableConfig, "y.linspace.stop"))} onChange={(event) => updateFor(bind, sourceMode, iterableKind, directValue, generatorModifiers, setEntryValue(iterableConfig, "y.linspace.stop", event.currentTarget.value))} />
                      <TextInput size="xs" label="y num" value={valueByKey(iterableConfig, "y.linspace.num")} error={scalarFieldError("y.linspace.num", valueByKey(iterableConfig, "y.linspace.num"))} onChange={(event) => updateFor(bind, sourceMode, iterableKind, directValue, generatorModifiers, setEntryValue(iterableConfig, "y.linspace.num", event.currentTarget.value))} />
                    </Group>
                  </>
                )}
                <Group grow align="flex-end">
                  <Select size="xs" label="Pattern" data={SCAN2D_PATTERN_OPTIONS.map((option) => ({ value: option.value, label: option.label }))} value={scan2dPattern} allowDeselect={false} searchable={false} comboboxProps={{ withinPortal: false }} onChange={(value) => { const nextPattern = value ?? "serpentine"; let nextConfig = setEntryValue(iterableConfig, "pattern", nextPattern); if (nextPattern !== "random") { nextConfig = removeEntry(nextConfig, "seed"); } updateFor(bind, sourceMode, iterableKind, directValue, generatorModifiers, nextConfig); }} />
                  <Select size="xs" label="Order" data={SCAN2D_ORDER_OPTIONS.map((option) => ({ value: option.value, label: option.label }))} value={scan2dOrder} allowDeselect={false} searchable={false} comboboxProps={{ withinPortal: false }} onChange={(value) => updateFor(bind, sourceMode, iterableKind, directValue, generatorModifiers, setEntryValue(iterableConfig, "order", value ?? "row_major"))} />
                </Group>
                {scan2dPattern === "random" ? (
                  <TextInput size="xs" label="Seed" value={valueByKey(iterableConfig, "seed")} onChange={(event) => updateFor(bind, sourceMode, iterableKind, directValue, generatorModifiers, event.currentTarget.value.trim() ? setEntryValue(iterableConfig, "seed", event.currentTarget.value) : removeEntry(iterableConfig, "seed"))} />
                ) : null}
                <Text size="xs" c="dimmed">
                  scan2d uses its own pattern and order; scalar modifiers do not apply.
                </Text>
              </>
            ) : (
              <>
                <Group justify="space-between" align="center">
                  <Text size="xs" fw={600}>
                    Iterable config
                  </Text>
                  <Button size="compact-xs" variant="light" leftSection={<IconPlus size={14} />} onClick={() => updateFor(bind, sourceMode, iterableKind, directValue, generatorModifiers, [...iterableConfig, { name: nextEntryName("field", iterableConfig), value: '""' }])}>
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
                          <TextInput size="xs" aria-label="Iterable config key" placeholder="key" variant="unstyled" value={entry.name} onChange={(event) => { const next = iterableConfig.map((item, itemIndex) => itemIndex === index ? { ...item, name: event.currentTarget.value } : item); updateFor(bind, sourceMode, iterableKind, directValue, generatorModifiers, next); }} />
                        </div>
                        <div className="sequencer-var-segment sequencer-var-value">
                          <TextInput size="xs" aria-label="Iterable config value" placeholder="value" variant="unstyled" value={renderValue(entry.value)} onChange={(event) => { const next = iterableConfig.map((item, itemIndex) => itemIndex === index ? { ...item, value: event.currentTarget.value } : item); updateFor(bind, sourceMode, iterableKind, directValue, generatorModifiers, next); }} />
                        </div>
                        <div className="sequencer-var-segment sequencer-var-remove">
                          <ActionIcon size="sm" variant="subtle" color="red" aria-label="Remove iterable config" onClick={() => { const next = iterableConfig.filter((_, itemIndex) => itemIndex !== index); updateFor(bind, sourceMode, iterableKind, directValue, generatorModifiers, next); }}>
                            <IconTrash size={14} />
                          </ActionIcon>
                        </div>
                      </div>
                    ))}
                  </Stack>
                )}
              </>
            )}
          </>
        )}

        <Text size="xs" c="dimmed">
          Edit the loop body from the step tree using insert/move/delete actions on child steps.
        </Text>
      </Stack>
    </Card>
  );
}
