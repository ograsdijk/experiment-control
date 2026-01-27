import {
  Button,
  Group,
  Modal,
  MultiSelect,
  NumberInput,
  SegmentedControl,
  Select,
  Stack,
  Text,
} from "@mantine/core";
import type {
  PlotStreamPanelState,
  PlotStreamWaterfallPanelState,
  StreamTraceAverageMode,
  StreamTraceDecimator,
  StreamTraceSourceMode,
} from "../features/stream/types";
import {
  DEFAULT_TRACE_AVERAGE_MODE,
  DEFAULT_TRACE_DECIMATOR,
  inferChannelCountFromShape,
  streamTargetKey,
} from "../features/stream/utils";

type SelectOption = { value: string; label: string };

type Props = {
  opened: boolean;
  onClose: () => void;
  panel: PlotStreamPanelState | PlotStreamWaterfallPanelState | null;
  streamTargetOptions: ReadonlyArray<SelectOption>;
  streamWorkspaceOptions: ReadonlyArray<SelectOption>;
  traceOutputOptions: ReadonlyArray<SelectOption>;
  overlayTraceOutputOptions: ReadonlyArray<SelectOption>;
  onSetSourceMode: (panelId: string, mode: StreamTraceSourceMode) => void;
  onSetOverlayCount: (panelId: string, value: number) => void;
  onSetRollingWindow: (panelId: string, value: number) => void;
  onSetAverageMode: (panelId: string, mode: StreamTraceAverageMode) => void;
  onRawTargetKeyChange: (panelId: string, targetKey: string | null) => void;
  onSetChannelIndex: (panelId: string, value: number) => void;
  onSetWorkspace: (panelId: string, workspaceId: string | null) => void;
  onSetOutput: (panelId: string, outputId: string | null) => void;
  onSetOverlayOutputs: (panelId: string, outputIds: string[]) => void;
  onSetTraceDecimator: (panelId: string, decimator: StreamTraceDecimator) => void;
  onSetTraceMaxPoints: (panelId: string, value: number) => void;
  onSetTraceMaxFps: (panelId: string, value: number) => void;
};

export function StreamTraceOptionsModal({
  opened,
  onClose,
  panel,
  streamTargetOptions,
  streamWorkspaceOptions,
  traceOutputOptions,
  overlayTraceOutputOptions,
  onSetSourceMode,
  onSetOverlayCount,
  onSetRollingWindow,
  onSetAverageMode,
  onRawTargetKeyChange,
  onSetChannelIndex,
  onSetWorkspace,
  onSetOutput,
  onSetOverlayOutputs,
  onSetTraceDecimator,
  onSetTraceMaxPoints,
  onSetTraceMaxFps,
}: Props) {
  const isWaterfall = panel?.kind === "stream_waterfall";
  const title = `Trace options ${panel?.title ?? ""}`;
  const channelCount = inferChannelCountFromShape(panel?.stream?.shape);
  const selectedRawTargetKey =
    panel?.stream != null
      ? streamTargetKey(panel.stream.deviceId, panel.stream.stream)
      : null;

  return (
    <Modal opened={opened} onClose={onClose} title={title} size="lg" centered>
      <Stack gap="md">
        {panel ? (
          <>
            <Group gap="sm" align="center" wrap="wrap">
              <SegmentedControl
                size="sm"
                value={panel.sourceMode}
                onChange={(value) =>
                  onSetSourceMode(panel.id, value === "dag" ? "dag" : "raw")
                }
                data={[
                  { value: "raw", label: "Raw" },
                  { value: "dag", label: "DAG" },
                ]}
              />
              <Group gap={6} align="center">
                <Text size="xs" c="dimmed">
                  {isWaterfall ? "Rows" : "Overlay N"}
                </Text>
                <NumberInput
                  size="xs"
                  w={88}
                  min={1}
                  max={isWaterfall ? 600 : 80}
                  value={panel.overlayCount}
                  onChange={(value) => onSetOverlayCount(panel.id, Number(value))}
                />
              </Group>
              <Group gap={6} align="center">
                <Text size="xs" c="dimmed">
                  Avg
                </Text>
                <NumberInput
                  size="xs"
                  w={88}
                  min={1}
                  max={200}
                  value={panel.rollingWindow}
                  onChange={(value) => onSetRollingWindow(panel.id, Number(value))}
                />
              </Group>
              <SegmentedControl
                size="xs"
                value={panel.averageMode}
                onChange={(value) =>
                  onSetAverageMode(
                    panel.id,
                    (value as StreamTraceAverageMode) ?? DEFAULT_TRACE_AVERAGE_MODE
                  )
                }
                data={[
                  { value: "block", label: "Block" },
                  { value: "rolling", label: "Rolling" },
                ]}
              />
            </Group>

            {panel.sourceMode === "raw" ? (
              <Stack gap="xs">
                <Select
                  size="sm"
                  searchable
                  clearable
                  placeholder="Select stream"
                  comboboxProps={{ zIndex: 500 }}
                  data={streamTargetOptions}
                  value={selectedRawTargetKey}
                  onChange={(value) => onRawTargetKeyChange(panel.id, value)}
                />
                {channelCount <= 1 ? (
                  <Text size="xs" c="dimmed">
                    Single-channel stream
                  </Text>
                ) : (
                  <Group gap={6} align="center">
                    <Text size="xs" c="dimmed">
                      Channel
                    </Text>
                    <NumberInput
                      size="xs"
                      w={100}
                      min={0}
                      max={Math.max(0, channelCount - 1)}
                      value={panel.channelIndex}
                      onChange={(value) => onSetChannelIndex(panel.id, Number(value))}
                    />
                  </Group>
                )}
              </Stack>
            ) : (
              <Stack gap="xs">
                <Select
                  size="sm"
                  searchable
                  placeholder="Select workspace"
                  comboboxProps={{ zIndex: 500 }}
                  data={streamWorkspaceOptions}
                  value={panel.workspaceId}
                  onChange={(value) => onSetWorkspace(panel.id, value)}
                />
                <Select
                  size="sm"
                  searchable
                  clearable
                  placeholder="Select trace output"
                  comboboxProps={{ zIndex: 500 }}
                  data={traceOutputOptions}
                  value={panel.outputId}
                  onChange={(value) => onSetOutput(panel.id, value)}
                />
                {panel.kind === "stream_raw" ? (
                  <MultiSelect
                    size="sm"
                    searchable
                    clearable
                    placeholder="Optional overlay outputs"
                    comboboxProps={{ zIndex: 500 }}
                    data={overlayTraceOutputOptions}
                    value={panel.overlayOutputIds}
                    onChange={(value) => onSetOverlayOutputs(panel.id, value)}
                  />
                ) : null}
              </Stack>
            )}

            <Group gap="sm" align="center" wrap="wrap">
              <Select
                size="sm"
                w={140}
                placeholder="Decimator"
                comboboxProps={{ zIndex: 500 }}
                data={[
                  { value: "stride", label: "Stride" },
                  { value: "mean", label: "Mean" },
                  { value: "minmax", label: "Min-Max" },
                  { value: "m4", label: "M4" },
                ]}
                value={panel.traceDecimator}
                onChange={(value) =>
                  onSetTraceDecimator(
                    panel.id,
                    (value as StreamTraceDecimator) ?? DEFAULT_TRACE_DECIMATOR
                  )
                }
              />
              <Group gap={6} align="center">
                <Text size="xs" c="dimmed">
                  Max points
                </Text>
                <NumberInput
                  size="xs"
                  w={110}
                  min={32}
                  max={20000}
                  value={panel.traceMaxPoints}
                  onChange={(value) => onSetTraceMaxPoints(panel.id, Number(value))}
                />
              </Group>
              <Group gap={6} align="center">
                <Text size="xs" c="dimmed">
                  Max Hz
                </Text>
                <NumberInput
                  size="xs"
                  w={95}
                  min={0.5}
                  max={120}
                  step={0.5}
                  decimalScale={1}
                  value={panel.traceMaxFps}
                  onChange={(value) => onSetTraceMaxFps(panel.id, Number(value))}
                />
              </Group>
            </Group>

            <Text size="xs" c="dimmed">
              Y-axis auto/manual stays in the panel header for quick access.
            </Text>
          </>
        ) : (
          <Text size="sm" c="dimmed">
            Select a stream trace panel to edit options.
          </Text>
        )}
        <Group justify="flex-end">
          <Button variant="light" onClick={onClose}>
            Close
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
