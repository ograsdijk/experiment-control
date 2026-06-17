import {
  ActionIcon,
  Badge,
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
  duplicateNameSet,
  getCapabilityParamDefaultValue,
  getCapabilityParamPlaceholder,
  isBlank,
  isNonNegativeNumberLiteral,
  isPositiveIntegerLiteral,
  metricCallParamEntries,
  metricConfigFieldSpecs,
  metricExtraConfigEntries,
  nextEntryName,
  nextParamName,
  renderValue,
  valueByKey,
  withMetricCallParamEntries,
} from "../editor_helpers";
import { callableActionNames, deviceNames } from "../device_field_options";
import { useDevicesContext } from "../../devices/DevicesContext";
import { FieldAutocomplete } from "./FieldAutocomplete";
import type {
  SequencerAdaptiveFieldGroup,
  SequencerAdaptiveMetricDetail,
  SequencerOutlineMetadataEntry,
  SequencerStepOutlineNode,
} from "../types";
import { useStepDraftSync } from "../useStepDraftSync";
import type { StreamAnalysisWorkspaceConfig } from "../../stream/types";
import type {
  CapabilityMember,
  CapabilityParam,
  TelemetrySignal,
} from "../../../types";
import { KeyValueChipList } from "./KeyValueChipList";
import { KeyValueChipRow } from "./KeyValueChipRow";

type Props = {
  node: SequencerStepOutlineNode;
  yamlText: string;
  onYamlTextChange: (value: string) => void;
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
  streamWorkspaces: Record<string, StreamAnalysisWorkspaceConfig>;
  latestSignalsByDevice: Record<string, Record<string, TelemetrySignal>>;
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
  streamWorkspaces,
  latestSignalsByDevice,
}: Props) {
  const { devices } = useDevicesContext();
  const [adaptiveIdDraft, setAdaptiveIdDraft] = useState("");
  const [adaptiveControllerKindDraft, setAdaptiveControllerKindDraft] = useState("");
  const [adaptiveMinLossDraft, setAdaptiveMinLossDraft] = useState("");
  const [adaptiveObserveRepeatsDraft, setAdaptiveObserveRepeatsDraft] = useState("");
  const [adaptiveScoreDraft, setAdaptiveScoreDraft] = useState("");
  const [adaptiveMaxTrialsDraft, setAdaptiveMaxTrialsDraft] = useState("");
  const [adaptiveControllerConfigExtraDraft, setAdaptiveControllerConfigExtraDraft] =
    useState<SequencerOutlineMetadataEntry[]>([]);
  const [adaptiveStoppingExtraDraft, setAdaptiveStoppingExtraDraft] = useState<
    SequencerOutlineMetadataEntry[]
  >([]);
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
    setAdaptiveControllerConfigExtraDraft(
      cloneEntries(
        node.adaptiveDetail.controllerConfig.filter((entry) => entry.name !== "min_loss")
      )
    );
    setAdaptiveStoppingExtraDraft(
      cloneEntries(node.adaptiveDetail.stopping.filter((entry) => entry.name !== "max_trials"))
    );
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
    const adaptiveControllerConfigExtra = usingDraft
      ? adaptiveControllerConfigExtraDraft
      : cloneEntries(
          node.adaptiveDetail.controllerConfig.filter((entry) => entry.name !== "min_loss")
        );
    const adaptiveStoppingExtra = usingDraft
      ? adaptiveStoppingExtraDraft
      : cloneEntries(node.adaptiveDetail.stopping.filter((entry) => entry.name !== "max_trials"));
    const adaptiveSpace = usingDraft ? adaptiveSpaceDraft : cloneAdaptiveSpace(node.adaptiveDetail.space);
    const adaptiveBind = usingDraft ? adaptiveBindDraft : cloneEntries(node.adaptiveDetail.bind);
    const adaptiveMetrics = usingDraft
      ? adaptiveMetricsDraft
      : cloneAdaptiveMetrics(node.adaptiveDetail.metrics);
    const adaptiveAggregate = usingDraft
      ? adaptiveAggregateDraft
      : cloneEntries(node.adaptiveDetail.aggregate);
    const isNumericLiteralText = (value: string) =>
      /^-?\d+(?:\.\d+)?$/.test(value.trim());
    const adaptiveIdError = isBlank(adaptiveId);
    const scoreError = isBlank(score);
    const minLossError =
      !isBlank(minLoss) &&
      isNumericLiteralText(minLoss) &&
      !isNonNegativeNumberLiteral(minLoss);
    const maxTrialsError =
      !isBlank(maxTrials) &&
      isNumericLiteralText(maxTrials) &&
      !isPositiveIntegerLiteral(maxTrials);
    const observeRepeatsError =
      !isBlank(observeRepeats) &&
      isNumericLiteralText(observeRepeats) &&
      !isPositiveIntegerLiteral(observeRepeats);
    const duplicateControllerConfigExtraNames = duplicateNameSet(
      adaptiveControllerConfigExtra
    );
    const blankControllerConfigExtraNames = adaptiveControllerConfigExtra.filter((entry) =>
      isBlank(entry.name)
    ).length;
    const controllerConfigExtraIssueCount =
      duplicateControllerConfigExtraNames.size + blankControllerConfigExtraNames;
    const duplicateStoppingExtraNames = duplicateNameSet(adaptiveStoppingExtra);
    const blankStoppingExtraNames = adaptiveStoppingExtra.filter((entry) =>
      isBlank(entry.name)
    ).length;
    const stoppingExtraIssueCount =
      duplicateStoppingExtraNames.size + blankStoppingExtraNames;
    const duplicateSpaceNames = duplicateNameSet(
      adaptiveSpace.map((group) => ({ name: group.name, value: null }))
    );
    const blankSpaceNames = adaptiveSpace.filter((group) => isBlank(group.name)).length;
    const blankSpaceFieldNames = adaptiveSpace.reduce(
      (count, group) => count + group.entries.filter((entry) => isBlank(entry.name)).length,
      0
    );
    const duplicateSpaceFieldNames = adaptiveSpace.reduce(
      (count, group) => count + duplicateNameSet(group.entries).size,
      0
    );
    const spaceIssueCount =
      duplicateSpaceNames.size +
      blankSpaceNames +
      blankSpaceFieldNames +
      duplicateSpaceFieldNames;
    const duplicateBindSources = duplicateNameSet(adaptiveBind);
    const blankBindSources = adaptiveBind.filter((entry) => isBlank(entry.name)).length;
    const blankBindTargets = adaptiveBind.filter((entry) => isBlank(entry.value)).length;
    const bindIssueCount =
      duplicateBindSources.size + blankBindSources + blankBindTargets;
    const duplicateMetricNames = duplicateNameSet(
      adaptiveMetrics.map((metric) => ({ name: metric.name, value: null }))
    );
    const metricIssues = adaptiveMetrics.map((metric) => {
      const issues: string[] = [];
      if (isBlank(metric.name)) {
        issues.push("Metric name is required.");
      } else if (duplicateMetricNames.has(metric.name.trim())) {
        issues.push("Metric names must be unique.");
      }
      if (metric.sourceKind === "analysis_output") {
        if (isBlank(valueByKey(metric.config, "workspace_id"))) {
          issues.push("Workspace id is required.");
        }
        if (isBlank(valueByKey(metric.config, "output_id"))) {
          issues.push("Output id is required.");
        }
      } else if (metric.sourceKind === "telemetry") {
        if (isBlank(valueByKey(metric.config, "device"))) {
          issues.push("Device is required.");
        }
        if (isBlank(valueByKey(metric.config, "signal"))) {
          issues.push("Signal is required.");
        }
      } else if (metric.sourceKind === "call") {
        if (isBlank(valueByKey(metric.config, "device"))) {
          issues.push("Device is required.");
        }
        if (isBlank(valueByKey(metric.config, "action"))) {
          issues.push("Action is required.");
        }
        const callParams = metricCallParamEntries(metric);
        if (callParams.some((entry) => isBlank(entry.name))) {
          issues.push("Call param names are required.");
        }
        if (duplicateNameSet(callParams).size > 0) {
          issues.push("Call param names must be unique.");
        }
      }
      return issues;
    });
    const metricsIssueCount =
      (adaptiveMetrics.length <= 0 ? 1 : 0) +
      metricIssues.reduce((count, issues) => count + issues.length, 0);
    const duplicateAggregateNames = duplicateNameSet(adaptiveAggregate);
    const blankAggregateNames = adaptiveAggregate.filter((entry) =>
      isBlank(entry.name)
    ).length;
    const aggregateIssueCount =
      duplicateAggregateNames.size + blankAggregateNames;

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
      nextMaxTrials: string,
      nextControllerConfigExtra: SequencerOutlineMetadataEntry[] =
        adaptiveControllerConfigExtra,
      nextStoppingExtra: SequencerOutlineMetadataEntry[] = adaptiveStoppingExtra
    ) => {
      const normalizedControllerConfigExtra = nextControllerConfigExtra.filter(
        (entry) => entry.name.trim() !== "min_loss"
      );
      const normalizedStoppingExtra = nextStoppingExtra.filter(
        (entry) => entry.name.trim() !== "max_trials"
      );
      markCurrent();
      setAdaptiveIdDraft(nextAdaptiveId);
      setAdaptiveControllerKindDraft(nextControllerKind);
      setAdaptiveMinLossDraft(nextMinLoss);
      setAdaptiveControllerConfigExtraDraft(normalizedControllerConfigExtra);
      setAdaptiveStoppingExtraDraft(normalizedStoppingExtra);
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
          normalizedControllerConfigExtra,
          nextSpace,
          nextBind,
          nextMetrics,
          nextAggregate,
          nextObserveRepeats,
          nextScore,
          nextMaxTrials,
          normalizedStoppingExtra
        )
      );
    };

    return (
      <Stack gap="sm">
        <Card radius="sm" p="xs" style={cardStyle}>
          <Stack gap={8}>
            <TextInput
              size="xs"
              label="Study id"
              value={adaptiveId}
              error={adaptiveIdError ? "Study id is required." : undefined}
              onChange={(event) =>
                updateAdaptive(
                  event.currentTarget.value,
                  controllerKind,
                  minLoss,
                  adaptiveSpace,
                  adaptiveBind,
                  adaptiveMetrics,
                  adaptiveAggregate,
                  observeRepeats,
                  score,
                  maxTrials
                )
              }
            />
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
              <TextInput
                size="xs"
                label="Min loss"
                value={minLoss}
                error={minLossError ? "Min loss must be non-negative." : undefined}
                onChange={(event) =>
                  updateAdaptive(
                    adaptiveId,
                    controllerKind,
                    event.currentTarget.value,
                    adaptiveSpace,
                    adaptiveBind,
                    adaptiveMetrics,
                    adaptiveAggregate,
                    observeRepeats,
                    score,
                    maxTrials
                  )
                }
              />
              <TextInput
                size="xs"
                label="Max trials"
                value={maxTrials}
                error={
                  maxTrialsError ? "Max trials must be a positive integer." : undefined
                }
                onChange={(event) =>
                  updateAdaptive(
                    adaptiveId,
                    controllerKind,
                    minLoss,
                    adaptiveSpace,
                    adaptiveBind,
                    adaptiveMetrics,
                    adaptiveAggregate,
                    observeRepeats,
                    score,
                    event.currentTarget.value
                  )
                }
              />
            </Group>
            <Group grow align="flex-end">
              <TextInput
                size="xs"
                label="Observe repeats"
                value={observeRepeats}
                error={
                  observeRepeatsError
                    ? "Observe repeats must be a positive integer."
                    : undefined
                }
                onChange={(event) =>
                  updateAdaptive(
                    adaptiveId,
                    controllerKind,
                    minLoss,
                    adaptiveSpace,
                    adaptiveBind,
                    adaptiveMetrics,
                    adaptiveAggregate,
                    event.currentTarget.value,
                    score,
                    maxTrials
                  )
                }
              />
              <TextInput
                size="xs"
                label="Score"
                value={score}
                error={scoreError ? "Score is required." : undefined}
                onChange={(event) =>
                  updateAdaptive(
                    adaptiveId,
                    controllerKind,
                    minLoss,
                    adaptiveSpace,
                    adaptiveBind,
                    adaptiveMetrics,
                    adaptiveAggregate,
                    observeRepeats,
                    event.currentTarget.value,
                    maxTrials
                  )
                }
              />
            </Group>
          </Stack>
        </Card>
        <Card radius="sm" p="xs" style={cardStyle}>
          <Stack gap={8}>
            <Group justify="space-between" align="center">
              <Text size="xs" fw={600}>
                Advanced config
              </Text>
              {controllerConfigExtraIssueCount + stoppingExtraIssueCount > 0 ? (
                <Badge size="xs" color="red" variant="light">
                  {controllerConfigExtraIssueCount + stoppingExtraIssueCount} issue
                  {controllerConfigExtraIssueCount + stoppingExtraIssueCount === 1
                    ? ""
                    : "s"}
                </Badge>
              ) : null}
            </Group>
            <KeyValueChipList
              entries={adaptiveControllerConfigExtra}
              onChange={(nextControllerConfigExtra) =>
                updateAdaptive(
                  adaptiveId,
                  controllerKind,
                  minLoss,
                  adaptiveSpace,
                  adaptiveBind,
                  adaptiveMetrics,
                  adaptiveAggregate,
                  observeRepeats,
                  score,
                  maxTrials,
                  nextControllerConfigExtra,
                  adaptiveStoppingExtra
                )
              }
              title="Controller config (extra)"
              addLabel="Add controller field"
              emptyLabel="No extra controller fields."
              nameLabel="Controller field"
              valueLabel="Controller value"
              removeLabel="Remove controller field"
              nextNamePrefix="field"
            />
            {duplicateControllerConfigExtraNames.size > 0 ? (
              <Text size="xs" c="red">
                Controller config field names must be unique.
              </Text>
            ) : null}
            {blankControllerConfigExtraNames > 0 ? (
              <Text size="xs" c="red">
                Controller config field names are required.
              </Text>
            ) : null}
            <KeyValueChipList
              entries={adaptiveStoppingExtra}
              onChange={(nextStoppingExtra) =>
                updateAdaptive(
                  adaptiveId,
                  controllerKind,
                  minLoss,
                  adaptiveSpace,
                  adaptiveBind,
                  adaptiveMetrics,
                  adaptiveAggregate,
                  observeRepeats,
                  score,
                  maxTrials,
                  adaptiveControllerConfigExtra,
                  nextStoppingExtra
                )
              }
              title="Stopping (extra)"
              addLabel="Add stopping field"
              emptyLabel="No extra stopping fields."
              nameLabel="Stopping field"
              valueLabel="Stopping value"
              removeLabel="Remove stopping field"
              nextNamePrefix="field"
            />
            {duplicateStoppingExtraNames.size > 0 ? (
              <Text size="xs" c="red">
                Stopping field names must be unique.
              </Text>
            ) : null}
            {blankStoppingExtraNames > 0 ? (
              <Text size="xs" c="red">
                Stopping field names are required.
              </Text>
            ) : null}
          </Stack>
        </Card>
        <Card radius="sm" p="xs" style={cardStyle}>
          <Stack gap={6}>
            <Group justify="space-between" align="center">
              <Text size="xs" fw={600}>
                Aggregate
              </Text>
              {aggregateIssueCount > 0 ? (
                <Badge size="xs" color="red" variant="light">
                  {aggregateIssueCount} issue{aggregateIssueCount === 1 ? "" : "s"}
                </Badge>
              ) : null}
            </Group>
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
            {duplicateAggregateNames.size > 0 ? (
              <Text size="xs" c="red">
                Aggregate metric names must be unique.
              </Text>
            ) : null}
            {blankAggregateNames > 0 ? (
              <Text size="xs" c="red">
                Aggregate metric names are required.
              </Text>
            ) : null}
          </Stack>
        </Card>

        <Card radius="sm" p="xs" style={cardStyle}>
          <Stack gap={8}>
            <Group justify="space-between" align="center">
              <Group gap={6} align="center">
                <Text size="xs" fw={600}>
                  Space
                </Text>
                {spaceIssueCount > 0 ? (
                  <Badge size="xs" color="red" variant="light">
                    {spaceIssueCount} issue{spaceIssueCount === 1 ? "" : "s"}
                  </Badge>
                ) : null}
              </Group>
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
                  <Card key={`space-${groupIndex}`} radius="sm" p="xs" style={cardStyle}>
                    <Stack gap={6}>
                      <div className="sequencer-var-chip">
                        <div className="sequencer-var-segment sequencer-var-name">
                          <TextInput
                            size="xs"
                            aria-label="Parameter name"
                            placeholder="parameter"
                            variant="unstyled"
                            value={group.name}
                            error={
                              isBlank(group.name)
                                ? "Parameter name is required."
                                : duplicateSpaceNames.has(group.name.trim())
                                  ? "Parameter names must be unique."
                                  : undefined
                            }
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
                      {duplicateNameSet(group.entries).size > 0 ? (
                        <Text size="xs" c="red">
                          Config field names must be unique.
                        </Text>
                      ) : null}
                      {group.entries.some((entry) => isBlank(entry.name)) ? (
                        <Text size="xs" c="red">
                          Config field names are required.
                        </Text>
                      ) : null}
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
              <Group gap={6} align="center">
                <Text size="xs" fw={600}>
                  Metrics
                </Text>
                {metricsIssueCount > 0 ? (
                  <Badge size="xs" color="red" variant="light">
                    {metricsIssueCount} issue{metricsIssueCount === 1 ? "" : "s"}
                  </Badge>
                ) : null}
              </Group>
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
                  const issues = metricIssues[metricIndex] ?? [];
                  const fieldSpecs = metricConfigFieldSpecs(metric.sourceKind);
                  const extraMetricEntries = metricExtraConfigEntries(metric);
                  const callParamEntries = metricCallParamEntries(metric);
                  const isCallMetric = metric.sourceKind === "call";
                  const isAnalysisMetric = metric.sourceKind === "analysis_output";
                  const isTelemetryMetric = metric.sourceKind === "telemetry";
                  const metricDevice = valueByKey(metric.config, "device");
                  const selectedWorkspaceId = valueByKey(metric.config, "workspace_id");
                  const selectedOutputId = valueByKey(metric.config, "output_id");
                  const selectedTelemetrySignal = valueByKey(metric.config, "signal");
                  const workspaceSelectOptions = Array.from(
                    new Map(
                      [
                        ...(selectedWorkspaceId
                          ? [
                              {
                                value: selectedWorkspaceId,
                                label:
                                  streamWorkspaces[selectedWorkspaceId]?.name ||
                                  selectedWorkspaceId,
                              },
                            ]
                          : []),
                        ...Object.values(streamWorkspaces).map((workspace) => ({
                          value: workspace.workspaceId,
                          label: workspace.name || workspace.workspaceId,
                        })),
                      ].map((option) => [option.value, option] as const)
                    ).values()
                  );
                  const outputSelectOptions = Array.from(
                    new Map(
                      [
                        ...(selectedOutputId
                          ? [{ value: selectedOutputId, label: selectedOutputId }]
                          : []),
                        ...((selectedWorkspaceId
                          ? streamWorkspaces[selectedWorkspaceId]?.publishOutputs ?? []
                          : []
                        ).map((output) => ({
                          value: output.outputId,
                          label: output.outputId,
                        }))),
                      ].map((option) => [option.value, option] as const)
                    ).values()
                  );
                  const knownWorkspaceIds = new Set(
                    Object.values(streamWorkspaces).map((workspace) => workspace.workspaceId)
                  );
                  const knownOutputIds = new Set(
                    (selectedWorkspaceId
                      ? streamWorkspaces[selectedWorkspaceId]?.publishOutputs ?? []
                      : []
                    ).map((output) => output.outputId)
                  );
                  const selectedWorkspaceMissing =
                    selectedWorkspaceId.trim().length > 0 &&
                    !knownWorkspaceIds.has(selectedWorkspaceId);
                  const selectedOutputMissing =
                    selectedOutputId.trim().length > 0 &&
                    knownOutputIds.size > 0 &&
                    !knownOutputIds.has(selectedOutputId);
                  const deviceOptions = deviceNames(devices);
                  const telemetrySignalNames = Object.keys(
                    latestSignalsByDevice[metricDevice] ?? {}
                  ).sort((a, b) => a.localeCompare(b));
                  const callActionNames = callableActionNames(
                    capabilitiesByDevice[metricDevice]
                  );
                  const knownTelemetryDeviceIds = new Set([
                    ...Object.keys(capabilitiesByDevice),
                    ...Object.keys(latestSignalsByDevice),
                  ]);
                  const knownTelemetrySignalIds = new Set(
                    Object.keys(latestSignalsByDevice[metricDevice] ?? {})
                  );
                  const selectedTelemetryDeviceMissing =
                    metricDevice.trim().length > 0 &&
                    !knownTelemetryDeviceIds.has(metricDevice);
                  const selectedTelemetrySignalMissing =
                    selectedTelemetrySignal.trim().length > 0 &&
                    knownTelemetrySignalIds.size > 0 &&
                    !knownTelemetrySignalIds.has(selectedTelemetrySignal);
                  const knownCallDeviceIds = new Set(Object.keys(capabilitiesByDevice));
                  const selectedCallDeviceMissing =
                    metricDevice.trim().length > 0 &&
                    !knownCallDeviceIds.has(metricDevice);
                  const actionOptions = (capabilitiesByDevice[metricDevice] ?? []).map(
                    (member) => member.name
                  );
                  const selectedAction = valueByKey(metric.config, "action");
                  const selectedActionMissing =
                    selectedAction.trim().length > 0 &&
                    actionOptions.length > 0 &&
                    !actionOptions.includes(selectedAction);
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
                      key={`metric-${metricIndex}`}
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
                            error={
                              isBlank(metric.name)
                                ? "Metric name is required."
                                : duplicateMetricNames.has(metric.name.trim())
                                  ? "Metric names must be unique."
                                  : undefined
                            }
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
                        {isAnalysisMetric ? (
                          <>
                          <Group grow align="flex-end">
                            {workspaceSelectOptions.length > 0 ? (
                              <Select
                                size="xs"
                                label="Workspace id"
                                data={workspaceSelectOptions}
                                value={selectedWorkspaceId}
                                error={
                                  isBlank(selectedWorkspaceId)
                                    ? "Workspace id is required."
                                    : undefined
                                }
                                allowDeselect={false}
                                searchable
                                comboboxProps={{ withinPortal: false }}
                                onChange={(value) =>
                                  updateMetric({
                                    ...metric,
                                    config: setConfigEntry(
                                      setConfigEntry(metric.config, "output_id", ""),
                                      "workspace_id",
                                      value ?? ""
                                    ),
                                  })
                                }
                              />
                            ) : (
                              <TextInput
                                size="xs"
                                label="Workspace id"
                                value={selectedWorkspaceId}
                                error={
                                  isBlank(selectedWorkspaceId)
                                    ? "Workspace id is required."
                                    : undefined
                                }
                                onChange={(event) =>
                                  updateMetric({
                                    ...metric,
                                    config: setConfigEntry(
                                      metric.config,
                                      "workspace_id",
                                      event.currentTarget.value
                                    ),
                                  })
                                }
                              />
                            )}
                            {outputSelectOptions.length > 0 ? (
                              <Select
                                size="xs"
                                label="Output id"
                                data={outputSelectOptions}
                                value={selectedOutputId}
                                error={
                                  isBlank(selectedOutputId)
                                    ? "Output id is required."
                                    : undefined
                                }
                                allowDeselect={false}
                                searchable
                                comboboxProps={{ withinPortal: false }}
                                onChange={(value) =>
                                  updateMetric({
                                    ...metric,
                                    config: setConfigEntry(
                                      metric.config,
                                      "output_id",
                                      value ?? ""
                                    ),
                                  })
                                }
                              />
                            ) : (
                              <TextInput
                                size="xs"
                                label="Output id"
                                value={selectedOutputId}
                                error={
                                  isBlank(selectedOutputId)
                                    ? "Output id is required."
                                    : undefined
                                }
                                onChange={(event) =>
                                  updateMetric({
                                    ...metric,
                                    config: setConfigEntry(
                                      metric.config,
                                      "output_id",
                                      event.currentTarget.value
                                    ),
                                  })
                                }
                              />
                            )}
                          </Group>
                          {selectedWorkspaceMissing ? (
                            <Text size="xs" c="orange">
                              Selected workspace is not in the current workspace list.
                            </Text>
                          ) : null}
                          {selectedOutputMissing ? (
                            <Text size="xs" c="orange">
                              Selected output is not in the chosen workspace's published outputs.
                            </Text>
                          ) : null}
                          </>
                        ) : null}
                        {isTelemetryMetric ? (
                          <>
                          <Group grow align="flex-end">
                            <FieldAutocomplete
                              label="Device"
                              value={metricDevice}
                              options={deviceOptions}
                              placeholder="device"
                              error={isBlank(metricDevice) ? "Device is required." : undefined}
                              onChange={(value) =>
                                updateMetric({
                                  ...metric,
                                  config: setConfigEntry(
                                    setConfigEntry(metric.config, "signal", ""),
                                    "device",
                                    value
                                  ),
                                })
                              }
                            />
                            <FieldAutocomplete
                              label="Signal"
                              value={selectedTelemetrySignal}
                              options={telemetrySignalNames}
                              placeholder="signal"
                              error={
                                isBlank(selectedTelemetrySignal)
                                  ? "Signal is required."
                                  : undefined
                              }
                              onChange={(value) =>
                                updateMetric({
                                  ...metric,
                                  config: setConfigEntry(metric.config, "signal", value),
                                })
                              }
                            />
                          </Group>
                          {selectedTelemetryDeviceMissing ? (
                            <Text size="xs" c="orange">
                              Selected device is not in the current telemetry/device list.
                            </Text>
                          ) : null}
                          {selectedTelemetrySignalMissing ? (
                            <Text size="xs" c="orange">
                              Selected signal is not in the current signal list for this device.
                            </Text>
                          ) : null}
                          </>
                        ) : null}
                        {isCallMetric ? (
                          <>
                          <Group grow align="flex-end">
                            <FieldAutocomplete
                              label="Device"
                              value={metricDevice}
                              options={deviceOptions}
                              placeholder="device"
                              error={isBlank(metricDevice) ? "Device is required." : undefined}
                              onChange={(value) =>
                                updateMetric({
                                  ...metric,
                                  config: [
                                    ...metric.config.filter(
                                      (entry) =>
                                        entry.name !== "device" &&
                                        entry.name !== "action" &&
                                        !entry.name.startsWith("params.")
                                    ),
                                    { name: "device", value },
                                  ],
                                })
                              }
                            />
                            <FieldAutocomplete
                              label="Action"
                              value={selectedAction}
                              options={callActionNames}
                              placeholder="action"
                              error={
                                isBlank(selectedAction) ? "Action is required." : undefined
                              }
                              onChange={(value) =>
                                updateMetric({
                                  ...metric,
                                  config: setConfigEntry(metric.config, "action", value),
                                })
                              }
                            />
                          </Group>
                          {selectedCallDeviceMissing ? (
                            <Text size="xs" c="orange">
                              Selected device is not in the current callable device list.
                            </Text>
                          ) : null}
                          {selectedActionMissing ? (
                            <Text size="xs" c="orange">
                              Selected action is not in the current action list for this device.
                            </Text>
                          ) : null}
                          </>
                        ) : null}
                        {fieldSpecs
                          .filter(
                            (spec) =>
                              !(
                                (isAnalysisMetric &&
                                  (spec.key === "workspace_id" ||
                                    spec.key === "output_id")) ||
                                (isTelemetryMetric &&
                                  (spec.key === "device" || spec.key === "signal"))
                              )
                          )
                          .map((spec) =>
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
                              error={
                                (metric.sourceKind === "analysis_output" &&
                                  (spec.key === "workspace_id" ||
                                    spec.key === "output_id") &&
                                  isBlank(valueByKey(metric.config, spec.key))) ||
                                (metric.sourceKind === "telemetry" &&
                                  (spec.key === "device" || spec.key === "signal") &&
                                  isBlank(valueByKey(metric.config, spec.key)))
                                  ? `${spec.label} is required.`
                                  : undefined
                              }
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
                            valuePlaceholderResolver={(entry: SequencerOutlineMetadataEntry) =>
                              getCapabilityParamPlaceholder(
                                paramSpecsByName.get(entry.name)
                              )
                            }
                          />
                        ) : null}
                        {issues.length > 0 ? (
                          <Stack gap={2}>
                            {issues.map((issue, issueIndex) => (
                              <Text key={`metric-issue-${issueIndex}`} size="xs" c="red">
                                {issue}
                              </Text>
                            ))}
                          </Stack>
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
                          error={
                            isBlank(entry.name)
                              ? "Bind source is required."
                              : duplicateBindSources.has(entry.name.trim())
                                ? "Bind source fields must be unique."
                                : undefined
                          }
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
                          error={
                            isBlank(entry.value) ? "Bind target is required." : undefined
                          }
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
            {duplicateBindSources.size > 0 ? (
              <Text size="xs" c="red">
                Bind source fields must be unique.
              </Text>
            ) : null}
          </Stack>
        </Card>

        <Text size="xs" c="dimmed">
          Edit the adaptive body from the step tree using insert/move/delete actions on child steps.
        </Text>
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
