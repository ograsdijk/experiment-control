import {
  Badge,
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
import type { UncertaintyMode } from "./StreamBinStatsPanel";
import type { PlotStreamBinStatsPanelState } from "../features/stream/types";
import { parseNumberInput } from "../features/stream/utils";

type SelectOption = { value: string; label: string };

type Props = {
  opened: boolean;
  onClose: () => void;
  panel: PlotStreamBinStatsPanelState | null;
  streamWorkspaceOptions: ReadonlyArray<SelectOption>;
  outputOptions: ReadonlyArray<SelectOption>;
  overlayTraceOutputOptions: ReadonlyArray<SelectOption>;
  xAxisLabel: string;
  onSetWorkspace: (panelId: string, workspaceId: string | null) => void;
  onSetOutput: (panelId: string, outputId: string | null) => void;
  onSetOverlayOutputs: (panelId: string, outputIds: string[]) => void;
  onSetUncertainty: (
    panelId: string,
    mode: UncertaintyMode,
    scale: number
  ) => void;
};

export function StreamBinStatsOptionsModal({
  opened,
  onClose,
  panel,
  streamWorkspaceOptions,
  outputOptions,
  overlayTraceOutputOptions,
  xAxisLabel,
  onSetWorkspace,
  onSetOutput,
  onSetOverlayOutputs,
  onSetUncertainty,
}: Props) {
  const title = `Bin stats options ${panel?.title ?? ""}`;
  return (
    <Modal opened={opened} onClose={onClose} title={title} size="lg" centered>
      <Stack gap="md">
        {panel ? (
          <>
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
              placeholder="Select hist output"
              comboboxProps={{ zIndex: 500 }}
              data={outputOptions}
              value={panel.outputId}
              onChange={(value) => onSetOutput(panel.id, value)}
            />
            <MultiSelect
              size="sm"
              searchable
              clearable
              placeholder="Optional overlay trace outputs"
              comboboxProps={{ zIndex: 500 }}
              data={overlayTraceOutputOptions}
              value={panel.overlayOutputIds}
              onChange={(value) => onSetOverlayOutputs(panel.id, value)}
            />
            <Group gap="sm" align="center" wrap="wrap">
              <SegmentedControl
                size="sm"
                value={panel.uncertaintyMode}
                onChange={(value) =>
                  onSetUncertainty(
                    panel.id,
                    value as UncertaintyMode,
                    panel.uncertaintyScale
                  )
                }
                data={[
                  { value: "std", label: "+/-k*std" },
                  { value: "sem", label: "+/-k*sem" },
                ]}
              />
              <Group gap={6} align="center">
                <Text size="xs" c="dimmed">
                  k
                </Text>
                <NumberInput
                  size="xs"
                  w={110}
                  min={0}
                  step={0.1}
                  value={panel.uncertaintyScale}
                  onChange={(value) => {
                    const next = parseNumberInput(value);
                    if (next === null) {
                      return;
                    }
                    onSetUncertainty(panel.id, panel.uncertaintyMode, next);
                  }}
                />
              </Group>
            </Group>
            <Badge variant="light" color="indigo">
              x-axis: {xAxisLabel}
            </Badge>
            <Text size="xs" c="dimmed">
              Y-axis auto/manual stays in the panel header for quick access.
            </Text>
          </>
        ) : (
          <Text size="sm" c="dimmed">
            Select a stream bin stats panel to edit options.
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
