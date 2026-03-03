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
import { IconChevronDown, IconPlus, IconTrash } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { applyEditedAdaptiveStep } from "../editing";
import {
  ADAPTIVE_CONTROLLER_OPTIONS,
  ADAPTIVE_METRIC_SOURCE_OPTIONS,
} from "../editor_constants";
import {
  adaptiveBindFieldOptions,
  cloneAdaptiveMetrics,
  cloneAdaptiveSpace,
  cloneEntries,
  getCapabilityParamDefaultValue,
  getCapabilityParamPlaceholder,
  metricCallParamEntries,
  metricConfigFieldSpecs,
  metricExtraConfigEntries,
  nextEntryName,
  nextParamName,
  renderValue,
  valueByKey,
  withMetricCallParamEntries,
} from "../editor_helpers";
import type {
  SequencerAdaptiveFieldGroup,
  SequencerAdaptiveMetricDetail,
  SequencerOutlineMetadataEntry,
  SequencerStepOutlineNode,
} from "../types";
import { useStepDraftSync } from "../useStepDraftSync";
import type { CapabilityMember, CapabilityParam } from "../../../types";
import { KeyValueChipList } from "./KeyValueChipList";
import { KeyValueChipRow } from "./KeyValueChipRow";

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

function BooleanField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <Select
      size="xs"
      label={label}
      data={[
        { value: "", label: "unset" },
        { value: "true", label: "true" },
        { value: "false", label: "false" },
      ]}
      value={value}
      allowDeselect={false}
      searchable={false}
      comboboxProps={{ withinPortal: false }}
      onChange={(next) => onChange(next ?? "")}
    />
  );
}

export function AdaptiveStepEditor({
  node,
  yamlText,
  onYamlTextChange,
  capabilitiesByDevice,
}: Props) {
  const [adaptiveIdDraft, setAdaptiveIdDraft] = useState("");
  const [adaptiveControllerKindDraft, setAdaptiveControllerKindDraft] = useState("");
  const [adaptiveMinLossDraft, setAdaptiveMinLossDraft] = useState("");
  const [adaptiveObserveRepeatsDraft, setAdaptiveObserveRepeatsDraft] = useState("");
  const [adaptiveScoreDraft, setAdaptiveScoreDraft] = useState("");
  const [adaptiveMaxTrialsDraft, setAdaptiveMaxTrialsDraft] = useState("");
  const [adaptiveSpaceDraft, setAdaptiveSpaceDraft] = useState<SequencerAdaptiveFieldGroup[]>([]);
  const [adaptiveBindDraft, setAdaptiveBindDraft] = useState<SequencerOutlineMetadataEntry[]>(
    []
  );
  const [adaptiveMetricsDraft, setAdaptiveMetricsDraft] = useState<
    SequencerAdaptiveMetricDetail[]
  >([]);
  const [adaptiveAggregateDraft, setAdaptiveAggregateDraft] = useState<
    SequencerOutlineMetadataEntry[]
  >([]);
  const { usingDraft, needsSync, markCurrent } = useStepDraftSync(node.id, node.snippet);

  useEffect(() => {
    if (!node.adaptiveDetail) {
      return;
    }
    if (!needsSync) {
      return;
    }
    markCurrent();
    setAdaptiveIdDraft(node.adaptiveDetail.id ?? "");
    setAdaptiveControllerKindDraft(
      node.adaptiveDetail.controllerKind ?? "adaptive.adaptive_grid_1d"
    );
    setAdaptiveMinLossDraft(valueByKey(node.adaptiveDetail.controllerConfig, "min_loss"));
    setAdaptiveObserveRepeatsDraft(node.adaptiveDetail.observeRepeats ?? "");
    setAdaptiveScoreDraft(node.adaptiveDetail.score ?? "");
    setAdaptiveMaxTrialsDraft(valueByKey(node.adaptiveDetail.stopping, "max_trials"));
    setAdaptiveSpaceDraft(cloneAdaptiveSpace(node.adaptiveDetail.space));
    setAdaptiveBindDraft(cloneEntries(node.adaptiveDetail.bind));
    setAdaptiveMetricsDraft(cloneAdaptiveMetrics(node.adaptiveDetail.metrics));
    setAdaptiveAggregateDraft(cloneEntries(node.adaptiveDetail.aggregate));
  }, [markCurrent, needsSync, node]);

  if (!node.adaptiveDetail) {
    return null;
  }

  try {
    const adaptiveId = usingDraft ? adaptiveIdDraft : node.adaptiveDetail.id ?? "";
    const controllerKind = usingDraft
      ? adaptiveControllerKindDraft
      : node.adaptiveDetail.controllerKind ?? "adaptive.adaptive_grid_1d";
    const minLoss = usingDraft
      ? adaptiveMinLossDraft
      : valueByKey(node.adaptiveDetail.controllerConfig, "min_loss");
    const observeRepeats = usingDraft
      ? adaptiveObserveRepeatsDraft
      : node.adaptiveDetail.observeRepeats ?? "";
    const score = usingDraft ? adaptiveScoreDraft : node.adaptiveDetail.score ?? "";
    const maxTrials = usingDraft
      ? adaptiveMaxTrialsDraft
      : valueByKey(node.adaptiveDetail.stopping, "max_trials");
    const adaptiveSpace = usingDraft ? adaptiveSpaceDraft : cloneAdaptiveSpace(node.adaptiveDetail.space);
    const adaptiveBind = usingDraft ? adaptiveBindDraft : cloneEntries(node.adaptiveDetail.bind);
    const adaptiveMetrics = usingDraft
      ? adaptiveMetricsDraft
      : cloneAdaptiveMetrics(node.adaptiveDetail.metrics);
    const adaptiveAggregate = usingDraft
      ? adaptiveAggregateDraft
      : cloneEntries(node.adaptiveDetail.aggregate);

    const updateAdaptive = (
      nextAdaptiveId: string,
      nextControllerKind: string,
      nextMinLoss: string,
      nextSpace: SequencerAdaptiveFieldGroup[],
      nextBind: SequencerOutlineMetadataEntry[],
      nextMetrics: SequencerAdaptiveMetricDetail[],
      nextAggregate: SequencerOutlineMetadataEntry[] = adaptiveAggregate,
      nextObserveRepeats: string,
      nextScore: string,
      nextMaxTrials: string
    ) => {
      markCurrent();
      setAdaptiveIdDraft(nextAdaptiveId);
      setAdaptiveControllerKindDraft(nextControllerKind);
      setAdaptiveMinLossDraft(nextMinLoss);
      setAdaptiveSpaceDraft(nextSpace);
      setAdaptiveBindDraft(nextBind);
      setAdaptiveMetricsDraft(nextMetrics);
      setAdaptiveAggregateDraft(nextAggregate);
      setAdaptiveObserveRepeatsDraft(nextObserveRepeats);
      setAdaptiveScoreDraft(nextScore);
      setAdaptiveMaxTrialsDraft(nextMaxTrials);
      onYamlTextChange(
        applyEditedAdaptiveStep(
          yamlText,
          node,
          nextAdaptiveId,
          nextControllerKind,
          nextMinLoss,
          nextSpace,
          nextBind,
          nextMetrics,
          nextAggregate,
          nextObserveRepeats,
          nextScore,
          nextMaxTrials
        )
      );
    };

    return (
      <Stack gap="sm">
        <Card radius="sm" p="xs" style={cardStyle}>
          <Stack gap={8}>
            <TextInput size="xs" label="Study id" value={adaptiveId} onChange={(event) => updateAdaptive(event.currentTarget.value, controllerKind, minLoss, adaptiveSpace, adaptiveBind, adaptiveMetrics, adaptiveAggregate, observeRepeats, score, maxTrials)} />
            <Select
              size="xs"
              label="Controller kind"
              data={Array.from(
                new Set([
                  ...ADAPTIVE_CONTROLLER_OPTIONS.map((option) => option.value),
                  ...(controllerKind ? [controllerKind] : []),
                ])
              ).map((value) => ({ value, label: value }))}
              value={controllerKind}
              allowDeselect={false}
              searchable={false}
              comboboxProps={{ withinPortal: false }}
              onChange={(value) => {
                if (value === null) return;
                updateAdaptive(adaptiveId, value, minLoss, adaptiveSpace, adaptiveBind, adaptiveMetrics, adaptiveAggregate, observeRepeats, score, maxTrials);
              }}
            />
            <Group grow align="flex-end">
              <TextInput size="xs" label="Min loss" value={minLoss} onChange={(event) => updateAdaptive(adaptiveId, controllerKind, event.currentTarget.value, adaptiveSpace, adaptiveBind, adaptiveMetrics, adaptiveAggregate, observeRepeats, score, maxTrials)} />
              <TextInput size="xs" label="Max trials" value={maxTrials} onChange={(event) => updateAdaptive(adaptiveId, controllerKind, minLoss, adaptiveSpace, adaptiveBind, adaptiveMetrics, adaptiveAggregate, observeRepeats, score, event.currentTarget.value)} />
            </Group>
            <Group grow align="flex-end">
              <TextInput size="xs" label="Observe repeats" value={observeRepeats} onChange={(event) => updateAdaptive(adaptiveId, controllerKind, minLoss, adaptiveSpace, adaptiveBind, adaptiveMetrics, adaptiveAggregate, event.currentTarget.value, score, maxTrials)} />
              <TextInput size="xs" label="Score" value={score} onChange={(event) => updateAdaptive(adaptiveId, controllerKind, minLoss, adaptiveSpace, adaptiveBind, adaptiveMetrics, adaptiveAggregate, observeRepeats, event.currentTarget.value, maxTrials)} />
            </Group>
          </Stack>
        </Card>
        <Card radius="sm" p="xs" style={cardStyle}>
          <KeyValueChipList
            entries={adaptiveAggregate}
            onChange={(nextAggregate) =>
              updateAdaptive(
                adaptiveId,
                controllerKind,
                minLoss,
                adaptiveSpace,
                adaptiveBind,
                adaptiveMetrics,
                nextAggregate,
                observeRepeats,
                score,
                maxTrials
              )
            }
            title="Aggregate"
            addLabel="Add aggregate"
            emptyLabel="No aggregate entries."
            nameLabel="Aggregate metric"
            valueLabel="Aggregate functions"
            removeLabel="Remove aggregate"
            nextNamePrefix="metric"
            defaultNewValue="[mean]"
          />
        </Card>

        <Card radius="sm" p="xs" style={cardStyle}>
          <Stack gap={8}>
            <Group justify="space-between" align="center">
              <Text size="xs" fw={600}>
                Space
              </Text>
              <Button
                size="compact-xs"
                variant="light"
                leftSection={<IconPlus size={14} />}
                onClick={() =>
                  updateAdaptive(
                    adaptiveId,
                    controllerKind,
                    minLoss,
                    [
                      ...adaptiveSpace,
                      {
                        name: nextEntryName(
                          "param",
                          adaptiveSpace.map((group) => ({ name: group.name, value: null }))
                        ),
                        entries: [
                          { name: "type", value: "float" },
                          { name: "min", value: "0" },
                          { name: "max", value: "1" },
                        ],
                      },
                    ],
                    adaptiveBind,
                    adaptiveMetrics,
                    adaptiveAggregate,
                    observeRepeats,
                    score,
                    maxTrials
                  )
                }
              >
                Add param
              </Button>
            </Group>
            {adaptiveSpace.length <= 0 ? (
              <Text size="xs" c="dimmed">
                No parameters defined.
              </Text>
            ) : (
              <Stack gap={8}>
                {adaptiveSpace.map((group, groupIndex) => (
                  <Card key={`${group.name}:${groupIndex}`} radius="sm" p="xs" style={cardStyle}>
                    <Stack gap={6}>
                      <div className="sequencer-var-chip">
                        <div className="sequencer-var-segment sequencer-var-name">
                          <TextInput
                            size="xs"
                            aria-label="Parameter name"
                            placeholder="parameter"
                            variant="unstyled"
                            value={group.name}
                            onChange={(event) => {
                              const nextSpace = adaptiveSpace.map((entry, entryIndex) =>
                                entryIndex === groupIndex
                                  ? { ...entry, name: event.currentTarget.value }
                                  : entry
                              );
                              updateAdaptive(adaptiveId, controllerKind, minLoss, nextSpace, adaptiveBind, adaptiveMetrics, adaptiveAggregate, observeRepeats, score, maxTrials);
                            }}
                          />
                        </div>
                        <div className="sequencer-var-segment sequencer-var-value">
                          <Text size="xs" c="dimmed">
                            {group.entries.length} field{group.entries.length === 1 ? "" : "s"}
                          </Text>
                        </div>
                        <div className="sequencer-var-segment sequencer-var-remove">
                          <ActionIcon size="sm" variant="subtle" color="red" aria-label="Remove parameter" onClick={() => {
                            const nextSpace = adaptiveSpace.filter((_, entryIndex) => entryIndex !== groupIndex);
                            updateAdaptive(adaptiveId, controllerKind, minLoss, nextSpace, adaptiveBind, adaptiveMetrics, adaptiveAggregate, observeRepeats, score, maxTrials);
                          }}>
                            <IconTrash size={14} />
                          </ActionIcon>
                        </div>
                      </div>
                      <KeyValueChipList
                        entries={group.entries}
                        onChange={(nextEntries) => {
                          const nextSpace = adaptiveSpace.map((entry, entryIndex) =>
                            entryIndex === groupIndex ? { ...entry, entries: nextEntries } : entry
                          );
                          updateAdaptive(adaptiveId, controllerKind, minLoss, nextSpace, adaptiveBind, adaptiveMetrics, adaptiveAggregate, observeRepeats, score, maxTrials);
                        }}
                        title="Config"
                        addLabel="Add field"
                        emptyLabel="No config entries."
                        nameLabel="Field name"
                        valueLabel="Field value"
                        removeLabel="Remove field"
                        nextNamePrefix="field"
                      />
                    </Stack>
                  </Card>
                ))}
              </Stack>
            )}
          </Stack>
        </Card>

        <Card radius="sm" p="xs" style={cardStyle}>
          <Stack gap={8}>
            <Group justify="space-between" align="center">
              <Text size="xs" fw={600}>
                Metrics
              </Text>
              <Button
                size="compact-xs"
                variant="light"
                leftSection={<IconPlus size={14} />}
                onClick={() =>
                  updateAdaptive(
                    adaptiveId,
                    controllerKind,
                    minLoss,
                    adaptiveSpace,
                    adaptiveBind,
                    [
                      ...adaptiveMetrics,
                      {
                        name: nextEntryName(
                          "metric",
                          adaptiveMetrics.map((metric) => ({
                            name: metric.name,
                            value: null,
                          }))
                        ),
                        sourceKind: "analysis_output",
                        config: [],
                      },
                    ],
                    adaptiveAggregate,
                    observeRepeats,
                    score,
                    maxTrials
                  )
                }
              >
                Add metric
              </Button>
            </Group>
            {adaptiveMetrics.length <= 0 ? (
              <Text size="xs" c="dimmed">
                No metrics defined.
              </Text>
            ) : (
              <Stack gap={8}>
                {adaptiveMetrics.map((metric, metricIndex) => {
                  const fieldSpecs = metricConfigFieldSpecs(metric.sourceKind);
                  const extraMetricEntries = metricExtraConfigEntries(metric);
                  const callParamEntries = metricCallParamEntries(metric);
                  const isCallMetric = metric.sourceKind === "call";
                  const metricDevice = valueByKey(metric.config, "device");
                  const actionOptions = (capabilitiesByDevice[metricDevice] ?? []).map(
                    (member) => member.name
                  );
                  const selectedAction = valueByKey(metric.config, "action");
                  const actionSelectOptions = Array.from(
                    new Set([
                      ...(selectedAction ? [selectedAction] : []),
                      ...actionOptions,
                    ])
                  ).map((value) => ({ value, label: value }));
                  const selectedActionMember =
                    (capabilitiesByDevice[metricDevice] ?? []).find(
                      (member) => member.name === selectedAction
                    ) ?? null;
                  const paramNameOptions = (selectedActionMember?.params ?? [])
                    .map((param) => param.name)
                    .filter(
                      (name): name is string =>
                        typeof name === "string" && name.trim().length > 0
                    );
                  const paramSpecsByName = new Map(
                    (selectedActionMember?.params ?? [])
                      .filter(
                        (param): param is CapabilityParam =>
                          typeof param?.name === "string" &&
                          param.name.trim().length > 0
                      )
                      .map((param) => [param.name, param] as const)
                  );

                  const updateMetric = (nextMetric: SequencerAdaptiveMetricDetail) => {
                    const nextMetrics = adaptiveMetrics.map(
                      (metricEntry, adaptiveMetricIndex) =>
                        adaptiveMetricIndex === metricIndex ? nextMetric : metricEntry
                    );
                    updateAdaptive(
                      adaptiveId,
                      controllerKind,
                      minLoss,
                      adaptiveSpace,
                      adaptiveBind,
                      nextMetrics,
                      adaptiveAggregate,
                      observeRepeats,
                      score,
                      maxTrials
                    );
                  };

                  return (
                    <Card
                      key={`${metric.name}:${metricIndex}`}
                      radius="sm"
                      p="xs"
                      style={cardStyle}
                    >
                      <Stack gap={6}>
                        <Group grow align="flex-end">
                          <TextInput
                            size="xs"
                            label="Name"
                            value={metric.name}
                            onChange={(event) =>
                              updateMetric({
                                ...metric,
                                name: event.currentTarget.value,
                              })
                            }
                          />
                          <Select
                            size="xs"
                            label="Source"
                            data={ADAPTIVE_METRIC_SOURCE_OPTIONS.map((option) => ({
                              value: option.value,
                              label: option.label,
                            }))}
                            value={metric.sourceKind ?? "analysis_output"}
                            allowDeselect={false}
                            searchable={false}
                            comboboxProps={{ withinPortal: false }}
                            onChange={(value) =>
                              updateMetric({
                                ...metric,
                                sourceKind: value ?? "analysis_output",
                                config: [],
                              })
                            }
                          />
                          <ActionIcon
                            size="sm"
                            variant="subtle"
                            color="red"
                            aria-label="Remove metric"
                            onClick={() => {
                              const nextMetrics = adaptiveMetrics.filter(
                                (_, adaptiveMetricIndex) =>
                                  adaptiveMetricIndex !== metricIndex
                              );
                              updateAdaptive(
                                adaptiveId,
                                controllerKind,
                                minLoss,
                                adaptiveSpace,
                                adaptiveBind,
                                nextMetrics,
                                adaptiveAggregate,
                                observeRepeats,
                                score,
                                maxTrials
                              );
                            }}
                          >
                            <IconTrash size={14} />
                          </ActionIcon>
                        </Group>
                        {isCallMetric ? (
                          <Group grow align="flex-end">
                            <TextInput
                              size="xs"
                              label="Device"
                              value={metricDevice}
                              onChange={(event) =>
                                updateMetric({
                                  ...metric,
                                  config: setConfigEntry(
                                    metric.config,
                                    "device",
                                    event.currentTarget.value
                                  ),
                                })
                              }
                            />
                            {actionSelectOptions.length > 0 ? (
                              <Select
                                size="xs"
                                label="Action"
                                data={actionSelectOptions}
                                value={selectedAction}
                                allowDeselect={false}
                                searchable
                                comboboxProps={{ withinPortal: false }}
                                onChange={(value) =>
                                  updateMetric({
                                    ...metric,
                                    config: setConfigEntry(
                                      metric.config,
                                      "action",
                                      value ?? ""
                                    ),
                                  })
                                }
                              />
                            ) : (
                              <TextInput
                                size="xs"
                                label="Action"
                                value={selectedAction}
                                onChange={(event) =>
                                  updateMetric({
                                    ...metric,
                                    config: setConfigEntry(
                                      metric.config,
                                      "action",
                                      event.currentTarget.value
                                    ),
                                  })
                                }
                              />
                            )}
                          </Group>
                        ) : null}
                        {fieldSpecs.map((spec) =>
                          spec.kind === "boolean" ? (
                            <BooleanField
                              key={spec.key}
                              label={spec.label}
                              value={valueByKey(metric.config, spec.key)}
                              onChange={(value) =>
                                updateMetric({
                                  ...metric,
                                  config: setConfigEntry(metric.config, spec.key, value),
                                })
                              }
                            />
                          ) : (
                            <TextInput
                              key={spec.key}
                              size="xs"
                              label={spec.label}
                              value={valueByKey(metric.config, spec.key)}
                              onChange={(event) =>
                                updateMetric({
                                  ...metric,
                                  config: setConfigEntry(
                                    metric.config,
                                    spec.key,
                                    event.currentTarget.value
                                  ),
                                })
                              }
                            />
                          )
                        )}
                        {isCallMetric ? (
                          <KeyValueChipList
                            entries={callParamEntries}
                            onChange={(nextParams) =>
                              updateMetric({
                                ...metric,
                                config: withMetricCallParamEntries(metric, nextParams),
                              })
                            }
                            title="Call params"
                            addLabel="Add param"
                            emptyLabel="No params."
                            nameLabel="Param name"
                            valueLabel="Param value"
                            removeLabel="Remove param"
                            nextNamePrefix="param"
                            onAdd={
                              paramNameOptions.length > 0
                                ? () => {
                                    const existingNames = new Set(
                                      callParamEntries.map((entry) => entry.name)
                                    );
                                    const nextKnown = paramNameOptions.find(
                                      (name) => !existingNames.has(name)
                                    );
                                    const nextName =
                                      nextKnown ?? nextParamName(callParamEntries);
                                    updateMetric({
                                      ...metric,
                                      config: withMetricCallParamEntries(metric, [
                                        ...callParamEntries,
                                        {
                                          name: nextName,
                                          value: getCapabilityParamDefaultValue(
                                            paramSpecsByName.get(nextName)
                                          ),
                                        },
                                      ]),
                                    });
                                  }
                                : undefined
                            }
                            valuePlaceholderResolver={(entry) =>
                              getCapabilityParamPlaceholder(
                                paramSpecsByName.get(entry.name)
                              )
                            }
                          />
                        ) : null}
                        <KeyValueChipList
                          entries={extraMetricEntries}
                          onChange={(nextExtraEntries) => {
                            const preservedNonExtra = metric.config.filter(
                              (configEntry) =>
                                !extraMetricEntries.some(
                                  (extra) => extra.name === configEntry.name
                                )
                            );
                            updateMetric({
                              ...metric,
                              config: [...preservedNonExtra, ...nextExtraEntries],
                            });
                          }}
                          title="Additional config"
                          addLabel="Add field"
                          emptyLabel="No additional config entries."
                          nameLabel="Metric config field"
                          valueLabel="Metric config value"
                          removeLabel="Remove metric field"
                          nextNamePrefix="field"
                        />
                      </Stack>
                    </Card>
                  );
                })}
              </Stack>
            )}
          </Stack>
        </Card>
        <Card radius="sm" p="xs" style={cardStyle}>
          <Stack gap={8}>
            <Group justify="space-between" align="center">
              <Text size="xs" fw={600}>
                Bind
              </Text>
              <Menu shadow="md" withArrow position="bottom-end" zIndex={1000}>
                <Menu.Target>
                  <Button
                    size="compact-xs"
                    variant="light"
                    leftSection={<IconPlus size={14} />}
                    rightSection={<IconChevronDown size={12} />}
                    disabled={
                      adaptiveBindFieldOptions(adaptiveSpace, adaptiveBind).filter(
                        (option) => !adaptiveBind.some((entry) => entry.name === option.value)
                      ).length <= 0
                    }
                  >
                    Add
                  </Button>
                </Menu.Target>
                <Menu.Dropdown>
                  {adaptiveBindFieldOptions(adaptiveSpace, adaptiveBind)
                    .filter((option) => !adaptiveBind.some((entry) => entry.name === option.value))
                    .map((option) => (
                      <Menu.Item
                        key={option.value}
                        onClick={() =>
                          updateAdaptive(
                            adaptiveId,
                            controllerKind,
                            minLoss,
                            adaptiveSpace,
                            [...adaptiveBind, { name: option.value, value: '""' }],
                            adaptiveMetrics,
                            adaptiveAggregate,
                            observeRepeats,
                            score,
                            maxTrials
                          )
                        }
                      >
                        {option.label}
                      </Menu.Item>
                    ))}
                </Menu.Dropdown>
              </Menu>
            </Group>
            {adaptiveBind.length <= 0 ? (
              <Text size="xs" c="dimmed">
                No bound variables.
              </Text>
            ) : (
              <Stack gap={6}>
                {adaptiveBind.map((entry, entryIndex) => {
                  const bindFieldOptions = adaptiveBindFieldOptions(adaptiveSpace, adaptiveBind);
                  return (
                    <KeyValueChipRow
                      key={`bind-${entryIndex}`}
                      nameControl={
                        <Select
                          size="xs"
                          aria-label="Bind source"
                          data={bindFieldOptions}
                          value={entry.name}
                          allowDeselect={false}
                          searchable={false}
                          comboboxProps={{ withinPortal: false }}
                          onChange={(value) => {
                            if (value === null) return;
                            const nextBind = adaptiveBind.map((bindEntry, bindIndex) =>
                              bindIndex === entryIndex ? { ...bindEntry, name: value } : bindEntry
                            );
                            updateAdaptive(adaptiveId, controllerKind, minLoss, adaptiveSpace, nextBind, adaptiveMetrics, adaptiveAggregate, observeRepeats, score, maxTrials);
                          }}
                        />
                      }
                      valueControl={
                        <TextInput
                          size="xs"
                          aria-label="Bind target"
                          placeholder="variable"
                          variant="unstyled"
                          value={renderValue(entry.value)}
                          onChange={(event) => {
                            const nextBind = adaptiveBind.map((bindEntry, bindIndex) =>
                              bindIndex === entryIndex
                                ? { ...bindEntry, value: event.currentTarget.value }
                                : bindEntry
                            );
                            updateAdaptive(adaptiveId, controllerKind, minLoss, adaptiveSpace, nextBind, adaptiveMetrics, adaptiveAggregate, observeRepeats, score, maxTrials);
                          }}
                        />
                      }
                      removeLabel="Remove bind"
                      onRemove={() => {
                        const nextBind = adaptiveBind.filter((_, bindIndex) => bindIndex !== entryIndex);
                        updateAdaptive(adaptiveId, controllerKind, minLoss, adaptiveSpace, nextBind, adaptiveMetrics, adaptiveAggregate, observeRepeats, score, maxTrials);
                      }}
                    />
                  );
                })}
              </Stack>
            )}
          </Stack>
        </Card>

        <Card radius="sm" p="xs" style={cardStyle}>
          <Stack gap={6}>
            <Text size="xs" fw={600}>
              Remaining sections (read-only)
            </Text>
            <Text size="xs" c="dimmed">
              The nested body and advanced adaptive config remain unchanged in this phase.
            </Text>
          </Stack>
        </Card>
      </Stack>
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return (
      <Card radius="sm" p="xs" style={cardStyle}>
        <Stack gap={6}>
          <Text size="xs" fw={600} c="red">
            Adaptive editor failed to render
          </Text>
          <Text size="xs" c="dimmed">
            {message}
          </Text>
          <Text size="xs" c="dimmed">
            The YAML block is still preserved. Switch to the full YAML editor if
            needed.
          </Text>
        </Stack>
      </Card>
    );
  }
}

function setConfigEntry(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>,
  key: string,
  value: string
): SequencerOutlineMetadataEntry[] {
  const next = entries.filter((entry) => entry.name !== key);
  if (!value.trim()) {
    return next;
  }
  return [...next, { name: key, value }];
}
