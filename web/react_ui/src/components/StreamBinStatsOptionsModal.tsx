import { useEffect, useState, type ChangeEvent } from "react";
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
  Switch,
  Text,
  TextInput,
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
  fitOverlayOutputOptions: ReadonlyArray<SelectOption>;
  xAxisLabel: string;
  onSetWorkspace: (panelId: string, workspaceId: string | null) => void;
  onSetOutput: (panelId: string, outputId: string | null) => void;
  onSetOverlayOutputs: (panelId: string, outputIds: string[]) => void;
  onSetFitOverlayOutputs: (panelId: string, outputIds: string[]) => void;
  onSetUncertainty: (
    panelId: string,
    mode: UncertaintyMode,
    scale: number
  ) => void;
  onSetShowBinMarkers: (panelId: string, show: boolean) => void;
  onSetXAxisTransform: (
    panelId: string,
    xOffset: number,
    xScale: number
  ) => void;
};

export function StreamBinStatsOptionsModal({
  opened,
  onClose,
  panel,
  streamWorkspaceOptions,
  outputOptions,
  overlayTraceOutputOptions,
  fitOverlayOutputOptions,
  xAxisLabel,
  onSetWorkspace,
  onSetOutput,
  onSetOverlayOutputs,
  onSetFitOverlayOutputs,
  onSetUncertainty,
  onSetShowBinMarkers,
  onSetXAxisTransform,
}: Props) {
  const title = `Bin stats options ${panel?.title ?? ""}`;
  const [xOffsetDraft, setXOffsetDraft] = useState("0");
  const [xScaleDraft, setXScaleDraft] = useState("1");

  useEffect(() => {
    if (panel) {
      setXOffsetDraft(String(panel.xOffset));
      setXScaleDraft(String(panel.xScale));
    }
  }, [panel?.id]);

  const handleXOffsetChange = (event: ChangeEvent<HTMLInputElement>) => {
    const raw = event.currentTarget.value;
    setXOffsetDraft(raw);
    if (!panel) {
      return;
    }
    const parsed = parseNumberInput(raw);
    if (parsed === null) {
      return;
    }
    onSetXAxisTransform(panel.id, parsed, panel.xScale);
  };

  const handleXScaleChange = (event: ChangeEvent<HTMLInputElement>) => {
    const raw = event.currentTarget.value;
    setXScaleDraft(raw);
    if (!panel) {
      return;
    }
    const parsed = parseNumberInput(raw);
    if (parsed === null || parsed === 0) {
      return;
    }
    onSetXAxisTransform(panel.id, panel.xOffset, parsed);
  };
  return (
    <Modal opened={opened} onClose={onClose} title={title} size="clamp(42rem, 82vw, 64rem)" centered>
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
            <MultiSelect
              size="sm"
              searchable
              clearable
              placeholder="Optional overlay fit outputs"
              comboboxProps={{ zIndex: 500 }}
              data={fitOverlayOutputOptions}
              value={panel.fitOverlayOutputIds}
              onChange={(value) => onSetFitOverlayOutputs(panel.id, value)}
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
            <Switch
              size="sm"
              label="Show sampled bins"
              checked={panel.showBinMarkers}
              onChange={(event) =>
                onSetShowBinMarkers(panel.id, event.currentTarget.checked)
              }
            />
            <Group gap="sm" align="flex-end" wrap="wrap">
              <TextInput
                size="xs"
                w={140}
                label="x offset"
                type="number"
                step="any"
                value={xOffsetDraft}
                onChange={handleXOffsetChange}
              />
              <TextInput
                size="xs"
                w={140}
                label="x scale"
                type="number"
                step="any"
                value={xScaleDraft}
                onChange={handleXScaleChange}
              />
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
