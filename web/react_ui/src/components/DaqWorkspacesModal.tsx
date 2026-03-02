import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Group,
  Modal,
  MultiSelect,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
} from "@mantine/core";
import { IconTrash } from "@tabler/icons-react";
import type { CSSProperties, MutableRefObject } from "react";
import { DagGraphPreview } from "./DagGraphPreview";
import { fileNameFromPath } from "../features/runtime/helpers";
import {
  SPECIAL_SAMPLE_INDEX_INPUT,
  STREAM_DAG_INPUT_KINDS,
  STREAM_DAG_OP_OPTIONS,
  STREAM_DAG_OPS,
  coerceDagParamValue,
  nodeKindFromOp,
} from "../features/stream/dag";
import {
  dagOutputKindColor,
  inferChannelCountFromShape,
  normalizeShape,
  streamTargetKey,
} from "../features/stream/utils";
import type {
  StreamAnalysisWorkspaceConfig,
  StreamCatalogEntry,
  StreamDagNodeConfig,
  StreamDagOutputConfig,
  StreamWorkspaceStoreStatus,
} from "../features/stream/types";

type SelectOption = { value: string; label: string };

type Props = {
  opened: boolean;
  onClose: () => void;
  streamWorkspaceOptions: SelectOption[];
  streamCatalogByKey: Map<string, StreamCatalogEntry>;
  daqWorkspaceId: string | null;
  onWorkspaceChange: (value: string | null) => void;
  onCreateWorkspace: () => void;
  workspaceStoreStatus: StreamWorkspaceStoreStatus;
  daqWorkspace: StreamAnalysisWorkspaceConfig | null;
  daqDraftName: string;
  onDraftNameChange: (value: string) => void;
  daqDraftEnabled: boolean;
  onDraftEnabledChange: (value: boolean) => void;
  daqSectionCardStyle: CSSProperties;
  daqNodeCardBaseStyle: CSSProperties;
  daqDraftNodes: StreamDagNodeConfig[];
  daqDraftOutputs: StreamDagOutputConfig[];
  daqFocusedNodeId: string | null;
  daqNodeCardRefs: MutableRefObject<Map<string, HTMLDivElement>>;
  daqResetNodeBusyId: string | null;
  streamAnalysisRpcReady: boolean;
  onResetDaqNodeAggregate: (nodeId: string) => Promise<unknown> | void;
  onAddNode: () => void;
  onRemoveNode: (index: number) => void;
  onSetNodeId: (index: number, value: string) => void;
  onSetNodeOp: (index: number, value: string | null) => void;
  onSetNodeInput: (index: number, port: string, value: string | null) => void;
  onSetNodeParam: (index: number, paramName: string, value: string) => void;
  onAddOutput: () => void;
  onRemoveOutput: (index: number) => void;
  onSetOutputId: (index: number, outputId: string) => void;
  onSetOutputNode: (index: number, nodeId: string | null) => void;
  daqPublishableNodeOptions: SelectOption[];
  daqResettableNodeIds: Set<string>;
  onFocusNodeCard: (nodeId: string) => void;
  onReloadStore: () => Promise<unknown> | void;
  onSaveStore: () => Promise<unknown> | void;
  workspaceStoreBusyAction: "save" | "reload" | null;
  onApplyWorkspace: () => Promise<unknown> | void;
};

export function DaqWorkspacesModal({
  opened,
  onClose,
  streamWorkspaceOptions,
  streamCatalogByKey,
  daqWorkspaceId,
  onWorkspaceChange,
  onCreateWorkspace,
  workspaceStoreStatus,
  daqWorkspace,
  daqDraftName,
  onDraftNameChange,
  daqDraftEnabled,
  onDraftEnabledChange,
  daqSectionCardStyle,
  daqNodeCardBaseStyle,
  daqDraftNodes,
  daqDraftOutputs,
  daqFocusedNodeId,
  daqNodeCardRefs,
  daqResetNodeBusyId,
  streamAnalysisRpcReady,
  onResetDaqNodeAggregate,
  onAddNode,
  onRemoveNode,
  onSetNodeId,
  onSetNodeOp,
  onSetNodeInput,
  onSetNodeParam,
  onAddOutput,
  onRemoveOutput,
  onSetOutputId,
  onSetOutputNode,
  daqPublishableNodeOptions,
  daqResettableNodeIds,
  onFocusNodeCard,
  onReloadStore,
  onSaveStore,
  workspaceStoreBusyAction,
  onApplyWorkspace,
}: Props) {
  const parseChannelIndices = (raw: unknown): string[] => {
    if (Array.isArray(raw)) {
      return raw.map((item) => String(item).trim()).filter((item) => item.length > 0);
    }
    const text = String(raw ?? "").trim();
    if (!text) {
      return [];
    }
    return text
      .split(/[\s,;]+/)
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
  };

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={
        <Group gap={8} wrap="wrap">
          <Text fw={600}>DAG Workspaces</Text>
          <Badge variant="light" color={workspaceStoreStatus.dirty ? "yellow" : "teal"}>
            {workspaceStoreStatus.dirty ? "Unsaved changes" : "Saved"}
          </Badge>
          <Badge variant="light" color="gray">
            Workspaces: {workspaceStoreStatus.workspaceCount}
          </Badge>
        </Group>
      }
      centered
      size="clamp(56rem, 92vw, 96rem)"
    >
      <Stack gap="md">
        <Group align="end" gap="sm">
          <Select
            label="Workspace"
            data={streamWorkspaceOptions}
            value={daqWorkspaceId}
            onChange={onWorkspaceChange}
            searchable
            flex={1}
          />
          <Button variant="light" onClick={onCreateWorkspace}>
            New workspace
          </Button>
        </Group>
        <Group gap={8} wrap="wrap">
          <Badge
            variant="light"
            color={workspaceStoreStatus.path ? "indigo" : "gray"}
            title={workspaceStoreStatus.path ?? undefined}
            style={{ maxWidth: 320 }}
          >
            <span
              style={{
                display: "inline-block",
                maxWidth: "100%",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                verticalAlign: "bottom",
              }}
            >
              {workspaceStoreStatus.path
                ? `File: ${fileNameFromPath(workspaceStoreStatus.path) ?? workspaceStoreStatus.path}`
                : "File: not configured"}
            </span>
          </Badge>
          {workspaceStoreStatus.path ? (
            <Text
              size="xs"
              c="dimmed"
              maw={460}
              title={workspaceStoreStatus.path}
              style={{
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {workspaceStoreStatus.path}
            </Text>
          ) : null}
          {workspaceStoreStatus.lastError ? (
            <Badge variant="light" color="red">
              {workspaceStoreStatus.lastError}
            </Badge>
          ) : null}
        </Group>
        {daqWorkspace ? (
          <>
            <TextInput
              label="Workspace name"
              value={daqDraftName}
              onChange={(event) => onDraftNameChange(event.currentTarget.value)}
            />
            <Switch
              checked={daqDraftEnabled}
              onChange={(event) => onDraftEnabledChange(event.currentTarget.checked)}
              label="Workspace enabled"
            />
            <Text size="xs" c="dimmed">
              Build and branch your DAG by wiring node inputs to upstream node IDs.
              Only outputs listed below are published to the websocket stream.
            </Text>

            <Card radius="md" p="sm" style={daqSectionCardStyle}>
              <Stack gap="sm">
                <Group justify="space-between">
                  <Text fw={600} size="sm">
                    Graph nodes
                  </Text>
                  <Button size="xs" variant="light" onClick={onAddNode}>
                    Add node
                  </Button>
                </Group>
                {daqDraftNodes.length <= 0 ? (
                  <Text size="sm" c="dimmed">
                    No nodes configured.
                  </Text>
                ) : (
                  daqDraftNodes.map((node, index) => {
                    const spec = STREAM_DAG_OPS[node.op];
                    const isFocused = daqFocusedNodeId === node.id;
                    return (
                      <Card
                        key={`daq-node-${index}`}
                        ref={(element) => {
                          if (element) {
                            daqNodeCardRefs.current.set(node.id, element);
                          } else {
                            daqNodeCardRefs.current.delete(node.id);
                          }
                        }}
                        radius="md"
                        p="sm"
                        style={{
                          ...daqNodeCardBaseStyle,
                          border: isFocused
                            ? "1px solid rgba(13, 148, 136, 0.9)"
                            : daqNodeCardBaseStyle.border,
                          background: isFocused
                            ? "rgba(13, 148, 136, 0.09)"
                            : daqNodeCardBaseStyle.background,
                          boxShadow: isFocused
                            ? "0 0 0 2px rgba(20, 184, 166, 0.35)"
                            : "none",
                          transition:
                            "background-color 120ms ease, border-color 120ms ease, box-shadow 120ms ease",
                        }}
                      >
                        <Stack gap="xs">
                          <Group justify="space-between" align="center">
                            <Badge variant="light" color="gray">
                              {node.op}
                            </Badge>
                            <Group gap={6}>
                              <Badge
                                variant="light"
                                color={dagOutputKindColor(spec.outputKind)}
                              >
                                out: {spec.outputKind}
                              </Badge>
                              {node.op === "aggregate.bin_stats" ||
                              node.op === "aggregate.bin2d_stats" ? (
                                <Button
                                  size="compact-xs"
                                  variant="light"
                                  color="red"
                                  loading={daqResetNodeBusyId === node.id}
                                  disabled={!streamAnalysisRpcReady || !daqWorkspaceId}
                                  onClick={() => {
                                    void onResetDaqNodeAggregate(node.id);
                                  }}
                                >
                                  Clear bins
                                </Button>
                              ) : null}
                              <ActionIcon
                                size="sm"
                                variant="light"
                                color="red"
                                onClick={() => onRemoveNode(index)}
                              >
                                <IconTrash size={14} />
                              </ActionIcon>
                            </Group>
                          </Group>
                          <Group gap="sm" align="end" wrap="wrap">
                            <TextInput
                              label="Node id"
                              value={node.id}
                              onChange={(event) =>
                                onSetNodeId(index, event.currentTarget.value)
                              }
                              style={{ flex: "1 1 180px" }}
                            />
                            <Select
                              label="Operator"
                              data={STREAM_DAG_OP_OPTIONS}
                              value={node.op}
                              onChange={(value) => onSetNodeOp(index, value)}
                              comboboxProps={{ zIndex: 500 }}
                              style={{ flex: "1 1 220px" }}
                            />
                            {node.op === "source.stream" ? (
                              <Select
                                label="channel_mode"
                                data={[
                                  { value: "single", label: "single" },
                                  { value: "average", label: "average" },
                                  { value: "sum", label: "sum" },
                                ]}
                                value={(() => {
                                  const raw = String(
                                    node.params.channel_mode ?? "single"
                                  )
                                    .trim()
                                    .toLowerCase();
                                  if (raw === "sum") {
                                    return "sum";
                                  }
                                  if (raw === "average" || raw === "mean") {
                                    return "average";
                                  }
                                  return "single";
                                })()}
                                onChange={(next) =>
                                  onSetNodeParam(
                                    index,
                                    "channel_mode",
                                    String(next ?? "single")
                                  )
                                }
                                comboboxProps={{ zIndex: 500 }}
                                style={{ flex: "0 0 140px" }}
                              />
                            ) : null}
                          </Group>
                          {node.op === "source.stream" ? (
                            <Group gap="sm" align="end" wrap="wrap">
                              {(() => {
                                const streamEntries = [...streamCatalogByKey.values()]
                                  .map((entry) => {
                                    const deviceId = String(entry.device_id ?? "").trim();
                                    const stream = String(entry.stream ?? "").trim();
                                    if (!deviceId || !stream) {
                                      return null;
                                    }
                                    return {
                                      deviceId,
                                      stream,
                                      shape: normalizeShape(entry.shape),
                                    };
                                  })
                                  .filter(
                                    (
                                      entry
                                    ): entry is {
                                      deviceId: string;
                                      stream: string;
                                      shape: number[];
                                    } => entry !== null
                                  );

                                const deviceOptions = Array.from(
                                  new Set(streamEntries.map((entry) => entry.deviceId))
                                )
                                  .sort((a, b) => a.localeCompare(b))
                                  .map((deviceId) => ({
                                    value: deviceId,
                                    label: deviceId,
                                  }));

                                const selectedDevice = String(
                                  node.params.device_id ?? ""
                                ).trim();
                                const streamOptions = streamEntries
                                  .filter((entry) => entry.deviceId === selectedDevice)
                                  .sort((a, b) => a.stream.localeCompare(b.stream))
                                  .map((entry) => ({
                                    value: entry.stream,
                                    label:
                                      entry.shape.length > 0
                                        ? `${entry.stream} [${entry.shape.join("x")}]`
                                        : entry.stream,
                                  }));

                                const selectedStream = String(
                                  node.params.stream ?? ""
                                ).trim();
                                const selectedEntry =
                                  selectedDevice && selectedStream
                                    ? streamCatalogByKey.get(
                                        streamTargetKey(selectedDevice, selectedStream)
                                      )
                                    : undefined;
                                const channelCount = inferChannelCountFromShape(
                                  normalizeShape(selectedEntry?.shape)
                                );
                                const modeRaw = String(
                                  node.params.channel_mode ?? "single"
                                )
                                  .trim()
                                  .toLowerCase();
                                const mode =
                                  modeRaw === "sum"
                                    ? "sum"
                                    : modeRaw === "average" || modeRaw === "mean"
                                      ? "average"
                                      : "single";
                                const channelOptions = Array.from(
                                  { length: channelCount },
                                  (_, idx) => ({
                                    value: String(idx),
                                    label: `ch ${idx}`,
                                  })
                                );
                                const parsedChannels = parseChannelIndices(
                                  node.params.channel_indices
                                );
                                const channelIndexFallback = Number(
                                  node.params.channel_index
                                );
                                const normalizedChannels =
                                  parsedChannels.length > 0
                                    ? parsedChannels
                                    : Number.isFinite(channelIndexFallback) &&
                                        channelIndexFallback >= 0
                                      ? [String(Math.trunc(channelIndexFallback))]
                                      : [];
                                const validSelectedChannels = normalizedChannels.filter(
                                  (value) =>
                                    channelOptions.some((option) => option.value === value)
                                );
                                const singleValue =
                                  validSelectedChannels[0] ??
                                  (channelOptions[0]?.value ?? "0");
                                return (
                                  <>
                                    <Select
                                      label="device_id"
                                      data={deviceOptions}
                                      value={selectedDevice || null}
                                      onChange={(next) => {
                                        const deviceId = String(next ?? "").trim();
                                        onSetNodeParam(index, "device_id", deviceId);
                                        if (!deviceId) {
                                          onSetNodeParam(index, "stream", "");
                                          return;
                                        }
                                        const currentStream = String(
                                          node.params.stream ?? ""
                                        ).trim();
                                        if (
                                          currentStream &&
                                          !streamCatalogByKey.has(
                                            streamTargetKey(deviceId, currentStream)
                                          )
                                        ) {
                                          onSetNodeParam(index, "stream", "");
                                        }
                                      }}
                                      comboboxProps={{ zIndex: 500 }}
                                      searchable
                                      clearable
                                      style={{ flex: "1 1 180px" }}
                                    />
                                    <Select
                                      label="stream"
                                      data={streamOptions}
                                      value={selectedStream || null}
                                      onChange={(next) =>
                                        onSetNodeParam(
                                          index,
                                          "stream",
                                          String(next ?? "")
                                        )
                                      }
                                      comboboxProps={{ zIndex: 500 }}
                                      searchable
                                      clearable
                                      disabled={!selectedDevice}
                                      placeholder={
                                        selectedDevice
                                          ? "Select stream"
                                          : "Select device first"
                                      }
                                      style={{ flex: "1 1 220px" }}
                                    />
                                    {channelCount <= 1 ? (
                                      <TextInput
                                        label="channel"
                                        value="ch 0"
                                        disabled
                                        style={{ flex: "0 0 160px" }}
                                      />
                                    ) : mode === "single" ? (
                                      <Select
                                        label="channel"
                                        data={channelOptions}
                                        value={singleValue}
                                        onChange={(next) => {
                                          const value =
                                            String(next ?? channelOptions[0]?.value ?? "0");
                                          onSetNodeParam(index, "channel_indices", value);
                                          onSetNodeParam(index, "channel_index", value);
                                        }}
                                        comboboxProps={{ zIndex: 500 }}
                                        style={{ flex: "0 0 180px" }}
                                      />
                                    ) : (
                                      <MultiSelect
                                        label="channels"
                                        data={channelOptions}
                                        searchable
                                        clearable
                                        value={validSelectedChannels}
                                        placeholder="Empty = all channels"
                                        onChange={(values) => {
                                          onSetNodeParam(
                                            index,
                                            "channel_indices",
                                            values.join(",")
                                          );
                                          if (values.length > 0) {
                                            onSetNodeParam(
                                              index,
                                              "channel_index",
                                              values[0]
                                            );
                                          }
                                        }}
                                        style={{ flex: "1 1 220px" }}
                                      />
                                    )}
                                  </>
                                );
                              })()}
                            </Group>
                          ) : null}
                          {[...spec.inputs, ...(spec.optionalInputs ?? [])].length > 0 ? (
                            <Group gap="sm" align="end" wrap="wrap">
                              {[...spec.inputs, ...(spec.optionalInputs ?? [])].map((port) => {
                                const isOptional = !spec.inputs.includes(port);
                                const expectedKind =
                                  STREAM_DAG_INPUT_KINDS[node.op]?.[port] ?? null;
                                const inputNodeOptions = daqDraftNodes
                                  .filter((candidate, candidateIdx) => {
                                    if (candidateIdx === index) {
                                      return false;
                                    }
                                    if (!expectedKind) {
                                      return true;
                                    }
                                    return nodeKindFromOp(candidate.op) === expectedKind;
                                  })
                                  .map((candidate) => ({
                                    value: candidate.id,
                                    label: `${candidate.id} (${candidate.op})`,
                                  }));
                                if (
                                  node.op === "fit.curve_1d" &&
                                  port === "x"
                                ) {
                                  inputNodeOptions.unshift({
                                    value: SPECIAL_SAMPLE_INDEX_INPUT,
                                    label: "sample_index",
                                  });
                                }
                                return (
                                  <Select
                                    key={`${node.id}:${port}`}
                                    label={`input.${port}${isOptional ? " (optional)" : ""}`}
                                    placeholder={
                                      expectedKind
                                        ? isOptional
                                          ? `Optional ${expectedKind} source`
                                          : `Select ${expectedKind} source`
                                        : "Select source node"
                                    }
                                    data={inputNodeOptions}
                                    value={node.inputs[port] || null}
                                    onChange={(value) =>
                                      onSetNodeInput(index, port, value)
                                    }
                                    comboboxProps={{ zIndex: 500 }}
                                    clearable
                                    searchable
                                    nothingFoundMessage="No compatible source nodes"
                                    style={{ flex: "1 1 180px" }}
                                  />
                                );
                              })}
                            </Group>
                          ) : null}
                          {spec.params.length > 0 ? (
                            <Group gap="sm" align="end" wrap="wrap">
                              {spec.params.map((field) => {
                                if (
                                  node.op === "source.stream" &&
                                  (field.name === "device_id" ||
                                    field.name === "stream" ||
                                    field.name === "channel_mode" ||
                                    field.name === "channel_index" ||
                                    field.name === "channel_indices")
                                ) {
                                  return null;
                                }
                                const autoRangeEnabledX =
                                  node.op === "aggregate.bin_stats"
                                    ? coerceDagParamValue(
                                        node.params.auto_range,
                                        "boolean"
                                      ) === true
                                    : node.op === "aggregate.bin2d_stats"
                                      ? coerceDagParamValue(
                                          node.params.x_auto_range,
                                          "boolean"
                                        ) === true
                                      : false;
                                const autoRangeEnabledY =
                                  node.op === "aggregate.bin2d_stats" &&
                                  coerceDagParamValue(
                                    node.params.y_auto_range,
                                    "boolean"
                                  ) === true;
                                if (
                                  (autoRangeEnabledX &&
                                    (field.name === "x_min" ||
                                      field.name === "x_max")) ||
                                  (autoRangeEnabledY &&
                                    (field.name === "y_min" ||
                                      field.name === "y_max"))
                                ) {
                                  return null;
                                }
                                if (field.kind === "boolean") {
                                  return (
                                    <Switch
                                      key={`${node.id}:${field.name}`}
                                      label={field.label}
                                      checked={
                                        coerceDagParamValue(
                                          node.params[field.name],
                                          "boolean"
                                        ) === true
                                      }
                                      onChange={(event) =>
                                        onSetNodeParam(
                                          index,
                                          field.name,
                                          event.currentTarget.checked ? "true" : "false"
                                        )
                                      }
                                    />
                                  );
                                }
                                if (
                                  Array.isArray(field.options) &&
                                  field.options.length > 0
                                ) {
                                  const currentValue = String(
                                    node.params[field.name] ?? ""
                                  ).trim();
                                  const options = field.options;
                                  const hasCurrent = options.some(
                                    (option) => option.value === currentValue
                                  );
                                  const selectedValue = hasCurrent
                                    ? currentValue
                                    : options[0]?.value ?? "";
                                  return (
                                    <Select
                                      key={`${node.id}:${field.name}`}
                                      label={field.label}
                                      data={options}
                                      value={selectedValue || null}
                                      onChange={(value) =>
                                        onSetNodeParam(
                                          index,
                                          field.name,
                                          String(value ?? options[0]?.value ?? "")
                                        )
                                      }
                                      comboboxProps={{ zIndex: 500 }}
                                      style={{ flex: "1 1 160px" }}
                                    />
                                  );
                                }
                                return (
                                  <TextInput
                                    key={`${node.id}:${field.name}`}
                                    label={field.label}
                                    value={String(node.params[field.name] ?? "")}
                                    placeholder={field.placeholder}
                                    onChange={(event) =>
                                      onSetNodeParam(
                                        index,
                                        field.name,
                                        event.currentTarget.value
                                      )
                                    }
                                    style={{ flex: "1 1 160px" }}
                                  />
                                );
                              })}
                            </Group>
                          ) : (
                            <Text size="xs" c="dimmed">
                              No parameters.
                            </Text>
                          )}
                        </Stack>
                      </Card>
                    );
                  })
                )}
              </Stack>
            </Card>

            <Card radius="md" p="sm" style={daqSectionCardStyle}>
              <Stack gap="sm">
                <Group justify="space-between">
                  <Text fw={600} size="sm">
                    Published outputs
                  </Text>
                  <Button size="xs" variant="light" onClick={onAddOutput}>
                    Add output
                  </Button>
                </Group>
                {daqDraftOutputs.length <= 0 ? (
                  <Text size="sm" c="dimmed">
                    No outputs exposed. Panels can only bind to exposed outputs.
                  </Text>
                ) : (
                  daqDraftOutputs.map((output, index) => {
                    const node = daqDraftNodes.find((item) => item.id === output.nodeId);
                    const kind = node ? nodeKindFromOp(node.op) : null;
                    return (
                      <Group key={`daq-output-${index}`} gap="sm" align="end" wrap="wrap">
                        <TextInput
                          label="output_id"
                          value={output.outputId}
                          onChange={(event) =>
                            onSetOutputId(index, event.currentTarget.value)
                          }
                          style={{ flex: "1 1 160px" }}
                        />
                        <Select
                          label="node_id"
                          data={daqPublishableNodeOptions}
                          value={output.nodeId || null}
                          onChange={(value) => onSetOutputNode(index, value)}
                          comboboxProps={{ zIndex: 500 }}
                          searchable
                          style={{ flex: "1 1 220px" }}
                        />
                        <Badge variant="light" color={dagOutputKindColor(kind)}>
                          {kind ?? "unknown"}
                        </Badge>
                        <ActionIcon
                          size="sm"
                          variant="light"
                          color="red"
                          onClick={() => onRemoveOutput(index)}
                        >
                          <IconTrash size={14} />
                        </ActionIcon>
                      </Group>
                    );
                  })
                )}
              </Stack>
            </Card>

            <Card radius="md" p="sm" style={daqSectionCardStyle}>
              <Stack gap={4}>
                <Text fw={600} size="sm">
                  Graph preview
                </Text>
                <DagGraphPreview
                  nodes={daqDraftNodes}
                  outputs={daqDraftOutputs.map((output) => ({
                    outputId: output.outputId,
                    nodeId: output.nodeId,
                  }))}
                  onNodeClick={onFocusNodeCard}
                  resettableNodeIds={daqResettableNodeIds}
                  resetNodeBusyId={daqResetNodeBusyId}
                  onResetNode={(nodeId) => {
                    void onResetDaqNodeAggregate(nodeId);
                  }}
                  height={520}
                />
              </Stack>
            </Card>
          </>
        ) : (
          <Text size="sm" c="dimmed">
            No workspace selected.
          </Text>
        )}
        <Group justify="flex-end">
          <Button variant="light" onClick={onClose}>
            Close
          </Button>
          <Button
            variant="light"
            onClick={() => {
              void onReloadStore();
            }}
            disabled={!streamAnalysisRpcReady || workspaceStoreBusyAction !== null}
            loading={workspaceStoreBusyAction === "reload"}
          >
            Reload from file
          </Button>
          <Button
            variant="light"
            onClick={() => {
              void onSaveStore();
            }}
            disabled={!streamAnalysisRpcReady || workspaceStoreBusyAction !== null}
            loading={workspaceStoreBusyAction === "save"}
          >
            Save to file
          </Button>
          <Button
            onClick={() => {
              void onApplyWorkspace();
            }}
            disabled={!daqWorkspace}
          >
            Apply workspace
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
