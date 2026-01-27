import {
  Badge,
  Button,
  Group,
  Modal,
  SegmentedControl,
  Select,
  Stack,
  Text,
} from "@mantine/core";
import type { PlotStreamBin2dPanelState } from "../features/stream/types";
import type { Bin2dReducer } from "./StreamBin2dPanel";
import { DEFAULT_BIN2D_REDUCER } from "../features/stream/utils";

type SelectOption = { value: string; label: string };

type Props = {
  opened: boolean;
  onClose: () => void;
  panel: PlotStreamBin2dPanelState | null;
  streamWorkspaceOptions: ReadonlyArray<SelectOption>;
  outputOptions: ReadonlyArray<SelectOption>;
  xAxisLabel: string;
  yAxisLabel: string;
  onSetWorkspace: (panelId: string, workspaceId: string | null) => void;
  onSetOutput: (panelId: string, outputId: string | null) => void;
  onSetReducer: (panelId: string, reducer: Bin2dReducer) => void;
};

export function StreamBin2dOptionsModal({
  opened,
  onClose,
  panel,
  streamWorkspaceOptions,
  outputOptions,
  xAxisLabel,
  yAxisLabel,
  onSetWorkspace,
  onSetOutput,
  onSetReducer,
}: Props) {
  const title = `2D bins options ${panel?.title ?? ""}`;
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
              placeholder="Select hist2d output"
              comboboxProps={{ zIndex: 500 }}
              data={outputOptions}
              value={panel.outputId}
              onChange={(value) => onSetOutput(panel.id, value)}
            />
            <Group gap="sm" align="center" wrap="wrap">
              <SegmentedControl
                size="sm"
                value={panel.reducer}
                onChange={(value) =>
                  onSetReducer(
                    panel.id,
                    (value as Bin2dReducer) ?? DEFAULT_BIN2D_REDUCER
                  )
                }
                data={[
                  { value: "mean", label: "Mean" },
                  { value: "max", label: "Max" },
                  { value: "min", label: "Min" },
                  { value: "count", label: "Count" },
                  { value: "std", label: "Std" },
                  { value: "sem", label: "Sem" },
                  { value: "sum", label: "Sum" },
                ]}
              />
            </Group>
            <Badge variant="light" color="indigo">
              x-axis: {xAxisLabel}
            </Badge>
            <Badge variant="light" color="indigo">
              y-axis: {yAxisLabel}
            </Badge>
            <Text size="xs" c="dimmed">
              Z-axis auto/manual stays in the panel header for quick access.
            </Text>
          </>
        ) : (
          <Text size="sm" c="dimmed">
            Select a stream 2D bins panel to edit options.
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
