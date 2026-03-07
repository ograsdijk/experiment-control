import {
  Badge,
  Button,
  Card,
  Group,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { IconPlus } from "@tabler/icons-react";
import {
  applyEditedAssignStep,
  applyEditedIfStep,
  applyEditedSetStep,
  applyEditedSetContextStep,
  applyEditedWaitUntilStep,
  applyEditedWhileStep,
} from "../editing";
import { duplicateNameSet, renderValue, valueByKey } from "../editor_helpers";
import type {
  SequencerOutlineMetadataEntry,
  SequencerSetContextStreamDetail,
  SequencerStepOutlineNode,
} from "../types";
import { ConditionBuilder } from "./ConditionBuilder";
import { KeyValueChipRow } from "./KeyValueChipRow";
import { KeyValueChipList } from "./KeyValueChipList";
import type {
  CapabilityMember,
  StreamCatalogEntry,
  TelemetrySignal,
} from "../../../types";

type CommonProps = {
  node: SequencerStepOutlineNode;
  yamlText: string;
  onYamlTextChange: (value: string) => void;
};

type WaitUntilProps = CommonProps & {
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
  latestSignalsByDevice: Record<string, Record<string, TelemetrySignal>>;
};

type SetContextProps = CommonProps & {
  streamCatalog: StreamCatalogEntry[];
};

type WaitUntilSampleKind = "telemetry" | "call" | "custom";

const cardStyle = {
  border: "1px solid var(--card-border)",
  background: "rgba(148, 163, 184, 0.04)",
} as const;

export function WaitUntilStepEditor({
  node,
  yamlText,
  onYamlTextChange,
  capabilitiesByDevice,
  latestSignalsByDevice,
}: WaitUntilProps) {
  if (!node.waitUntilDetail) {
    return null;
  }

  const sample = node.waitUntilDetail.sample;
  const condition = node.waitUntilDetail.condition;
  const inferSampleKind = (entries: ReadonlyArray<SequencerOutlineMetadataEntry>): WaitUntilSampleKind => {
    const hasTelemetry = entries.some((entry) => entry.name.startsWith("telemetry."));
    const hasCall = entries.some((entry) => entry.name.startsWith("call."));
    if (hasTelemetry && !hasCall) return "telemetry";
    if (hasCall && !hasTelemetry) return "call";
    return "custom";
  };
  const sampleKind = inferSampleKind(sample);
  const sampleGet = (entries: ReadonlyArray<SequencerOutlineMetadataEntry>, key: string): string =>
    valueByKey(entries, key);
  const sampleSet = (
    entries: ReadonlyArray<SequencerOutlineMetadataEntry>,
    key: string,
    value: string
  ): SequencerOutlineMetadataEntry[] => {
    const index = entries.findIndex((entry) => entry.name === key);
    if (index >= 0) {
      return entries.map((entry, entryIndex) =>
        entryIndex === index ? { ...entry, value } : entry
      );
    }
    return [...entries, { name: key, value }];
  };
  const withSampleKind = (
    kind: WaitUntilSampleKind,
    entries: ReadonlyArray<SequencerOutlineMetadataEntry>
  ): SequencerOutlineMetadataEntry[] => {
    if (kind === "custom") {
      return [...entries];
    }
    if (kind === "telemetry") {
      const device = sampleGet(entries, "telemetry.device");
      const signal = sampleGet(entries, "telemetry.signal");
      const maxAge = sampleGet(entries, "telemetry.max_age_s");
      const extra = entries.filter(
        (entry) =>
          entry.name.startsWith("telemetry.") &&
          entry.name !== "telemetry.device" &&
          entry.name !== "telemetry.signal" &&
          entry.name !== "telemetry.max_age_s"
      );
      return [
        { name: "telemetry.device", value: device },
        { name: "telemetry.signal", value: signal },
        ...(maxAge.trim().length > 0
          ? [{ name: "telemetry.max_age_s", value: maxAge }]
          : []),
        ...extra,
      ];
    }
    const device = sampleGet(entries, "call.device");
    const action = sampleGet(entries, "call.action");
    const extra = entries.filter(
      (entry) =>
        entry.name.startsWith("call.") &&
        entry.name !== "call.device" &&
        entry.name !== "call.action"
    );
    return [
      { name: "call.device", value: device },
      { name: "call.action", value: action },
      ...extra,
    ];
  };
  const metricDevice = sampleKind === "telemetry" ? sampleGet(sample, "telemetry.device") : sampleGet(sample, "call.device");
  const telemetrySignal = sampleGet(sample, "telemetry.signal");
  const telemetryDeviceOptions = Array.from(
    new Set([...Object.keys(capabilitiesByDevice), ...Object.keys(latestSignalsByDevice), ...(metricDevice ? [metricDevice] : [])])
  ).map((value) => ({ value, label: value }));
  const telemetrySignalOptions = Array.from(
    new Set([
      ...Object.keys(latestSignalsByDevice[metricDevice] ?? {}),
      ...(telemetrySignal ? [telemetrySignal] : []),
    ])
  ).map((value) => ({ value, label: value }));
  const callDevice = sampleGet(sample, "call.device");
  const callDeviceOptions = Array.from(
    new Set([...Object.keys(capabilitiesByDevice), ...(callDevice ? [callDevice] : [])])
  ).map((value) => ({ value, label: value }));
  const selectedCallAction = sampleGet(sample, "call.action");
  const callActionOptions = Array.from(
    new Set([
      ...(capabilitiesByDevice[callDevice] ?? []).map((member) => member.name),
      ...(selectedCallAction ? [selectedCallAction] : []),
    ])
  ).map((value) => ({ value, label: value }));
  const telemetryExtraEntries =
    sampleKind === "telemetry"
      ? sample
          .filter(
            (entry) =>
              entry.name.startsWith("telemetry.") &&
              entry.name !== "telemetry.device" &&
              entry.name !== "telemetry.signal" &&
              entry.name !== "telemetry.max_age_s"
          )
          .map((entry) => ({
            name: entry.name.slice("telemetry.".length),
            value: entry.value,
          }))
      : [];
  const callExtraEntries =
    sampleKind === "call"
      ? sample
          .filter(
            (entry) =>
              entry.name.startsWith("call.") &&
              entry.name !== "call.device" &&
              entry.name !== "call.action"
          )
          .map((entry) => ({
            name: entry.name.slice("call.".length),
            value: entry.value,
          }))
      : [];

  const updateWaitUntil = (
    nextTimeoutS: string,
    nextEveryS: string,
    nextSample: SequencerOutlineMetadataEntry[],
    nextCondition: SequencerOutlineMetadataEntry[]
  ) => {
    onYamlTextChange(
      applyEditedWaitUntilStep(
        yamlText,
        node,
        nextTimeoutS,
        nextEveryS,
        nextSample,
        nextCondition
      )
    );
  };

  return (
    <Card radius="sm" p="xs" style={cardStyle}>
      <Stack gap={8}>
        <TextInput
          size="xs"
          label="Timeout (s)"
          value={renderValue(node.waitUntilDetail.timeoutS)}
          onChange={(event) =>
            updateWaitUntil(
              event.currentTarget.value,
              renderValue(node.waitUntilDetail.everyS),
              sample,
              condition
            )
          }
        />
        <TextInput
          size="xs"
          label="Polling interval (s)"
          value={renderValue(node.waitUntilDetail.everyS)}
          onChange={(event) =>
            updateWaitUntil(
              renderValue(node.waitUntilDetail.timeoutS),
              event.currentTarget.value,
              sample,
              condition
            )
          }
        />
        <Card radius="sm" p="xs" style={cardStyle}>
          <Stack gap={8}>
            <Select
              size="xs"
              label="Sample source"
              data={[
                { value: "telemetry", label: "telemetry" },
                { value: "call", label: "call" },
                { value: "custom", label: "custom" },
              ]}
              value={sampleKind}
              allowDeselect={false}
              searchable={false}
              comboboxProps={{ withinPortal: false }}
              onChange={(value) =>
                updateWaitUntil(
                  renderValue(node.waitUntilDetail.timeoutS),
                  renderValue(node.waitUntilDetail.everyS),
                  withSampleKind((value as WaitUntilSampleKind) ?? "custom", sample),
                  condition
                )
              }
            />
            {sampleKind === "telemetry" ? (
              <>
                <Group grow align="flex-end">
                  {telemetryDeviceOptions.length > 0 ? (
                    <Select
                      size="xs"
                      label="Device"
                      data={telemetryDeviceOptions}
                      value={sampleGet(sample, "telemetry.device")}
                      allowDeselect={false}
                      searchable
                      comboboxProps={{ withinPortal: false }}
                      onChange={(value) =>
                        updateWaitUntil(
                          renderValue(node.waitUntilDetail.timeoutS),
                          renderValue(node.waitUntilDetail.everyS),
                          sampleSet(
                            sampleSet(sample, "telemetry.signal", ""),
                            "telemetry.device",
                            value ?? ""
                          ),
                          condition
                        )
                      }
                    />
                  ) : (
                    <TextInput
                      size="xs"
                      label="Device"
                      value={sampleGet(sample, "telemetry.device")}
                      onChange={(event) =>
                        updateWaitUntil(
                          renderValue(node.waitUntilDetail.timeoutS),
                          renderValue(node.waitUntilDetail.everyS),
                          sampleSet(sample, "telemetry.device", event.currentTarget.value),
                          condition
                        )
                      }
                    />
                  )}
                  {telemetrySignalOptions.length > 0 ? (
                    <Select
                      size="xs"
                      label="Signal"
                      data={telemetrySignalOptions}
                      value={sampleGet(sample, "telemetry.signal")}
                      allowDeselect={false}
                      searchable
                      comboboxProps={{ withinPortal: false }}
                      onChange={(value) =>
                        updateWaitUntil(
                          renderValue(node.waitUntilDetail.timeoutS),
                          renderValue(node.waitUntilDetail.everyS),
                          sampleSet(sample, "telemetry.signal", value ?? ""),
                          condition
                        )
                      }
                    />
                  ) : (
                    <TextInput
                      size="xs"
                      label="Signal"
                      value={sampleGet(sample, "telemetry.signal")}
                      onChange={(event) =>
                        updateWaitUntil(
                          renderValue(node.waitUntilDetail.timeoutS),
                          renderValue(node.waitUntilDetail.everyS),
                          sampleSet(sample, "telemetry.signal", event.currentTarget.value),
                          condition
                        )
                      }
                    />
                  )}
                </Group>
                <TextInput
                  size="xs"
                  label="Max age (s)"
                  value={sampleGet(sample, "telemetry.max_age_s")}
                  onChange={(event) =>
                    updateWaitUntil(
                      renderValue(node.waitUntilDetail.timeoutS),
                      renderValue(node.waitUntilDetail.everyS),
                      sampleSet(sample, "telemetry.max_age_s", event.currentTarget.value),
                      condition
                    )
                  }
                />
                <KeyValueChipList
                  entries={telemetryExtraEntries}
                  onChange={(nextEntries) =>
                    updateWaitUntil(
                      renderValue(node.waitUntilDetail.timeoutS),
                      renderValue(node.waitUntilDetail.everyS),
                      [
                        ...sample.filter(
                          (entry) =>
                            !entry.name.startsWith("telemetry.") ||
                            entry.name === "telemetry.device" ||
                            entry.name === "telemetry.signal" ||
                            entry.name === "telemetry.max_age_s"
                        ),
                        ...nextEntries
                          .map((entry) => ({
                            name: `telemetry.${entry.name.trim()}`,
                            value: entry.value,
                          }))
                          .filter((entry) => entry.name !== "telemetry."),
                      ],
                      condition
                    )
                  }
                  title="Telemetry config"
                  addLabel="Add field"
                  emptyLabel="No additional telemetry config."
                  nameLabel="Config key"
                  valueLabel="Config value"
                  removeLabel="Remove config field"
                  nextNamePrefix="field"
                />
              </>
            ) : null}
            {sampleKind === "call" ? (
              <>
                <Group grow align="flex-end">
                  {callDeviceOptions.length > 0 ? (
                    <Select
                      size="xs"
                      label="Device"
                      data={callDeviceOptions}
                      value={sampleGet(sample, "call.device")}
                      allowDeselect={false}
                      searchable
                      comboboxProps={{ withinPortal: false }}
                      onChange={(value) =>
                        updateWaitUntil(
                          renderValue(node.waitUntilDetail.timeoutS),
                          renderValue(node.waitUntilDetail.everyS),
                          [
                            ...sample.filter(
                              (entry) =>
                                entry.name !== "call.device" &&
                                entry.name !== "call.action" &&
                                !entry.name.startsWith("call.params.")
                            ),
                            { name: "call.device", value: value ?? "" },
                          ],
                          condition
                        )
                      }
                    />
                  ) : (
                    <TextInput
                      size="xs"
                      label="Device"
                      value={sampleGet(sample, "call.device")}
                      onChange={(event) =>
                        updateWaitUntil(
                          renderValue(node.waitUntilDetail.timeoutS),
                          renderValue(node.waitUntilDetail.everyS),
                          sampleSet(sample, "call.device", event.currentTarget.value),
                          condition
                        )
                      }
                    />
                  )}
                  {callActionOptions.length > 0 ? (
                    <Select
                      size="xs"
                      label="Action"
                      data={callActionOptions}
                      value={sampleGet(sample, "call.action")}
                      allowDeselect={false}
                      searchable
                      comboboxProps={{ withinPortal: false }}
                      onChange={(value) =>
                        updateWaitUntil(
                          renderValue(node.waitUntilDetail.timeoutS),
                          renderValue(node.waitUntilDetail.everyS),
                          sampleSet(sample, "call.action", value ?? ""),
                          condition
                        )
                      }
                    />
                  ) : (
                    <TextInput
                      size="xs"
                      label="Action"
                      value={sampleGet(sample, "call.action")}
                      onChange={(event) =>
                        updateWaitUntil(
                          renderValue(node.waitUntilDetail.timeoutS),
                          renderValue(node.waitUntilDetail.everyS),
                          sampleSet(sample, "call.action", event.currentTarget.value),
                          condition
                        )
                      }
                    />
                  )}
                </Group>
                <KeyValueChipList
                  entries={callExtraEntries}
                  onChange={(nextEntries) =>
                    updateWaitUntil(
                      renderValue(node.waitUntilDetail.timeoutS),
                      renderValue(node.waitUntilDetail.everyS),
                      [
                        ...sample.filter(
                          (entry) =>
                            !entry.name.startsWith("call.") ||
                            entry.name === "call.device" ||
                            entry.name === "call.action"
                        ),
                        ...nextEntries
                          .map((entry) => ({
                            name: `call.${entry.name.trim()}`,
                            value: entry.value,
                          }))
                          .filter((entry) => entry.name !== "call."),
                      ],
                      condition
                    )
                  }
                  title="Call config"
                  addLabel="Add field"
                  emptyLabel="No additional call config."
                  nameLabel="Config key"
                  valueLabel="Config value"
                  removeLabel="Remove config field"
                  nextNamePrefix="field"
                />
              </>
            ) : null}
            {sampleKind === "custom" ? (
              <KeyValueChipList
                entries={sample}
                onChange={(nextSample) =>
                  updateWaitUntil(
                    renderValue(node.waitUntilDetail.timeoutS),
                    renderValue(node.waitUntilDetail.everyS),
                    nextSample,
                    condition
                  )
                }
                title="Sample"
                addLabel="Add field"
                emptyLabel="No sample entries."
                nameLabel="Sample field name"
                valueLabel="Sample field value"
                removeLabel="Remove sample field"
                nextNamePrefix="field"
              />
            ) : null}
          </Stack>
        </Card>
        <ConditionBuilder
          entries={condition}
          onChange={(nextCondition) =>
            updateWaitUntil(
              renderValue(node.waitUntilDetail.timeoutS),
              renderValue(node.waitUntilDetail.everyS),
              sample,
              nextCondition
            )
          }
          title="Condition"
          addLabel="Add field"
          emptyLabel="No condition entries."
          nameLabel="Condition field name"
          valueLabel="Condition field value"
          removeLabel="Remove condition field"
          nextNamePrefix="field"
        />
      </Stack>
    </Card>
  );
}

export function SetStepEditor({
  node,
  yamlText,
  onYamlTextChange,
}: CommonProps) {
  if (!node.setDetail) {
    return null;
  }

  const setNameError = renderValue(node.setDetail.name).trim().length <= 0;
  const setValueError = renderValue(node.setDetail.value).trim().length <= 0;

  const updateSet = (nextName: string, nextValue: string) => {
    onYamlTextChange(
      applyEditedSetStep(
        yamlText,
        node,
        renderValue(node.setDetail?.device),
        nextName,
        nextValue
      )
    );
  };

  return (
    <Card radius="sm" p="xs" style={cardStyle}>
      <Stack gap={8}>
        <TextInput
          size="xs"
          label="Device"
          value={renderValue(node.setDetail.device)}
          readOnly
        />
        <TextInput
          size="xs"
          label="Field"
          value={renderValue(node.setDetail.name)}
          error={setNameError ? "Field name is required." : undefined}
          onChange={(event) =>
            updateSet(event.currentTarget.value, renderValue(node.setDetail?.value))
          }
        />
        <TextInput
          size="xs"
          label="Value"
          value={renderValue(node.setDetail.value)}
          error={setValueError ? "Value is required." : undefined}
          onChange={(event) =>
            updateSet(renderValue(node.setDetail?.name), event.currentTarget.value)
          }
        />
      </Stack>
    </Card>
  );
}

export function AssignStepEditor({
  node,
  yamlText,
  onYamlTextChange,
}: CommonProps) {
  if (!node.assignDetail) {
    return null;
  }

  const duplicateNames = duplicateNameSet(node.assignDetail.entries);
  const blankNames = node.assignDetail.entries.filter((entry) =>
    renderValue(entry.name).trim().length <= 0
  ).length;
  const issueCount = duplicateNames.size + blankNames;

  return (
    <Card radius="sm" p="xs" style={cardStyle}>
      <Stack gap={8}>
        <KeyValueChipList
          entries={node.assignDetail.entries}
          onChange={(nextEntries) =>
            onYamlTextChange(applyEditedAssignStep(yamlText, node, nextEntries))
          }
          title="Assignments"
          addLabel="Add assignment"
          emptyLabel="No assignments."
          nameLabel="Variable name"
          valueLabel="Value"
          removeLabel="Remove assignment"
          nextNamePrefix="var"
          issueCount={issueCount}
          issueText={
            issueCount > 0
              ? duplicateNames.size > 0
                ? "Assignment variable names must be unique."
                : "Assignment variable names are required."
              : null
          }
        />
      </Stack>
    </Card>
  );
}

export function IfStepEditor({
  node,
  yamlText,
  onYamlTextChange,
}: CommonProps) {
  if (!node.ifDetail) {
    return null;
  }

  const condition = node.ifDetail.condition;
  const updateCondition = (nextCondition: SequencerOutlineMetadataEntry[]) => {
    onYamlTextChange(applyEditedIfStep(yamlText, node, nextCondition));
  };

  return (
    <Card radius="sm" p="xs" style={cardStyle}>
      <Stack gap={8}>
        <ConditionBuilder
          entries={condition}
          onChange={updateCondition}
          title="Condition"
          addLabel="Add field"
          emptyLabel="No condition entries."
          nameLabel="Condition field name"
          valueLabel="Condition field value"
          removeLabel="Remove condition field"
          nextNamePrefix="field"
        />
        <Group gap={6} wrap="wrap">
          <Text size="xs" c="dimmed">
            Then: {node.ifDetail.thenCount}
          </Text>
          <Text size="xs" c="dimmed">
            Else: {node.ifDetail.elseCount}
          </Text>
        </Group>
        <Text size="xs" c="dimmed">
          Edit then/else branches from the step tree using insert/move/delete actions on child steps.
        </Text>
      </Stack>
    </Card>
  );
}

export function SetContextStepEditor({
  node,
  yamlText,
  onYamlTextChange,
  streamCatalog,
}: SetContextProps) {
  if (!node.setContextDetail) {
    return null;
  }

  const streams = node.setContextDetail.streams;
  const fields = node.setContextDetail.fields;
  const knownStreamTargets = Array.from(
    new Set(
      streamCatalog
        .map((entry) => ({
          device: String(entry.device_id ?? "").trim(),
          stream: String(entry.stream ?? "").trim(),
        }))
        .filter((entry) => entry.device.length > 0 && entry.stream.length > 0)
        .map((entry) => `${entry.device}|${entry.stream}`)
    )
  )
    .map((key) => {
      const [device, stream] = key.split("|");
      return { device, stream };
    })
    .sort((a, b) =>
      a.device === b.device
        ? a.stream.localeCompare(b.stream)
        : a.device.localeCompare(b.device)
    );
  const knownDeviceIds = new Set(knownStreamTargets.map((entry) => entry.device));
  const fieldDuplicates = duplicateNameSet(fields);
  const invalidStreamCount = streams.filter(
    (entry) =>
      String(entry.device ?? "").trim().length <= 0 ||
      String(entry.stream ?? "").trim().length <= 0
  ).length;
  const fieldIssueCount = fieldDuplicates.size;
  const updateSetContext = (
    nextStreams: SequencerSetContextStreamDetail[],
    nextFields: SequencerOutlineMetadataEntry[]
  ) => {
    onYamlTextChange(applyEditedSetContextStep(yamlText, node, nextStreams, nextFields));
  };

  const updateStream = (
    index: number,
    patch: Partial<SequencerSetContextStreamDetail>
  ) => {
    updateSetContext(
      streams.map((entry, entryIndex) =>
        entryIndex === index ? { ...entry, ...patch } : entry
      ),
      fields
    );
  };

  const removeStream = (index: number) => {
    updateSetContext(
      streams.filter((_, entryIndex) => entryIndex !== index),
      fields
    );
  };

  const addStream = () => {
    updateSetContext(
      [...streams, { device: "", stream: "" }],
      fields
    );
  };

  return (
    <Card radius="sm" p="xs" style={cardStyle}>
      <Stack gap={8}>
        <Stack gap={6}>
          <Group justify="space-between" align="center">
            <Group gap={6} align="center">
              <Text size="xs" fw={600}>
                Streams
              </Text>
              {invalidStreamCount > 0 ? (
                <Badge size="xs" color="red" variant="light">
                  {invalidStreamCount} issue{invalidStreamCount === 1 ? "" : "s"}
                </Badge>
              ) : null}
            </Group>
            <Button
              size="compact-xs"
              variant="light"
              leftSection={<IconPlus size={14} />}
              onClick={addStream}
            >
              Add stream
            </Button>
          </Group>
          {streams.length <= 0 ? (
            <Text size="xs" c="dimmed">
              No streams. Add a stream target to apply context to.
            </Text>
          ) : (
            <Stack gap={6}>
              {streams.map((entry, index) => (
                <Stack key={`stream-${index}`} gap={4}>
                  {(() => {
                    const selectedDevice = String(entry.device ?? "").trim();
                    const selectedStream = String(entry.stream ?? "").trim();
                    const streamsForDevice = knownStreamTargets
                      .filter((target) => target.device === selectedDevice)
                      .map((target) => target.stream);
                    const deviceOptions = Array.from(
                      new Set([
                        ...knownStreamTargets.map((target) => target.device),
                        ...(selectedDevice ? [selectedDevice] : []),
                      ])
                    )
                      .sort((a, b) => a.localeCompare(b))
                      .map((value) => ({ value, label: value }));
                    const streamOptions = Array.from(
                      new Set([...streamsForDevice, ...(selectedStream ? [selectedStream] : [])])
                    )
                      .sort((a, b) => a.localeCompare(b))
                      .map((value) => ({ value, label: value }));
                    const selectedDeviceMissing =
                      selectedDevice.length > 0 &&
                      knownDeviceIds.size > 0 &&
                      !knownDeviceIds.has(selectedDevice);
                    const selectedStreamMissing =
                      selectedStream.length > 0 &&
                      streamsForDevice.length > 0 &&
                      !streamsForDevice.includes(selectedStream);
                    return (
                      <>
                        <KeyValueChipRow
                          nameControl={
                            deviceOptions.length > 0 ? (
                              <Select
                                size="xs"
                                aria-label="Stream device"
                                placeholder="device"
                                variant="unstyled"
                                data={deviceOptions}
                                value={entry.device ?? ""}
                                allowDeselect={false}
                                searchable
                                comboboxProps={{ withinPortal: false }}
                                onChange={(value) =>
                                  updateStream(index, {
                                    device: value ?? "",
                                    stream: "",
                                  })
                                }
                              />
                            ) : (
                              <TextInput
                                size="xs"
                                aria-label="Stream device"
                                placeholder="device"
                                variant="unstyled"
                                value={entry.device ?? ""}
                                onChange={(event) =>
                                  updateStream(index, { device: event.currentTarget.value })
                                }
                              />
                            )
                          }
                          valueControl={
                            streamOptions.length > 0 ? (
                              <Select
                                size="xs"
                                aria-label="Stream name"
                                placeholder="stream"
                                variant="unstyled"
                                data={streamOptions}
                                value={entry.stream ?? ""}
                                allowDeselect={false}
                                searchable
                                comboboxProps={{ withinPortal: false }}
                                onChange={(value) =>
                                  updateStream(index, { stream: value ?? "" })
                                }
                              />
                            ) : (
                              <TextInput
                                size="xs"
                                aria-label="Stream name"
                                placeholder="stream"
                                variant="unstyled"
                                value={entry.stream ?? ""}
                                onChange={(event) =>
                                  updateStream(index, { stream: event.currentTarget.value })
                                }
                              />
                            )
                          }
                          removeLabel="Remove stream"
                          onRemove={() => removeStream(index)}
                        />
                        {String(entry.device ?? "").trim().length <= 0 ||
                        String(entry.stream ?? "").trim().length <= 0 ? (
                          <Text size="xs" c="red">
                            Both device and stream are required.
                          </Text>
                        ) : null}
                        {selectedDeviceMissing ? (
                          <Text size="xs" c="orange">
                            Selected device is not in the current stream catalog.
                          </Text>
                        ) : null}
                        {selectedStreamMissing ? (
                          <Text size="xs" c="orange">
                            Selected stream is not present for the chosen device.
                          </Text>
                        ) : null}
                      </>
                    );
                  })()}
                </Stack>
              ))}
            </Stack>
          )}
        </Stack>
        <KeyValueChipList
          entries={fields}
          onChange={(nextFields) => updateSetContext(streams, nextFields)}
          title="Fields"
          addLabel="Add field"
          emptyLabel="No context fields."
          nameLabel="Context field name"
          valueLabel="Context field value"
          removeLabel="Remove context field"
          nextNamePrefix="field"
        />
        {fieldIssueCount > 0 ? (
          <Text size="xs" c="red">
            Field names must be unique.
          </Text>
        ) : null}
      </Stack>
    </Card>
  );
}

export function WhileStepEditor({
  node,
  yamlText,
  onYamlTextChange,
}: CommonProps) {
  if (!node.whileDetail) {
    return null;
  }

  const condition = node.whileDetail.condition;
  const updateCondition = (nextCondition: SequencerOutlineMetadataEntry[]) => {
    onYamlTextChange(applyEditedWhileStep(yamlText, node, nextCondition));
  };

  return (
    <Card radius="sm" p="xs" style={cardStyle}>
      <Stack gap={8}>
        <ConditionBuilder
          entries={condition}
          onChange={updateCondition}
          title="Condition"
          addLabel="Add field"
          emptyLabel="No condition entries."
          nameLabel="Loop condition field name"
          valueLabel="Loop condition field value"
          removeLabel="Remove loop condition field"
          nextNamePrefix="field"
        />
        <Text size="xs" c="dimmed">
          Edit the loop body from the step tree using insert/move/delete actions on child steps.
        </Text>
      </Stack>
    </Card>
  );
}
