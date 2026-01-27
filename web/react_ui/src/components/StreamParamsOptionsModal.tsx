import { Button, Group, Modal, MultiSelect, Select, Stack, Text } from "@mantine/core";
import type { PlotStreamParamsPanelState } from "../features/stream/types";

type SelectOption = { value: string; label: string };

type Props = {
  opened: boolean;
  onClose: () => void;
  panel: PlotStreamParamsPanelState | null;
  streamWorkspaceOptions: ReadonlyArray<SelectOption>;
  outputOptions: ReadonlyArray<SelectOption>;
  onSetWorkspace: (panelId: string, workspaceId: string | null) => void;
  onSetOutputs: (panelId: string, outputIds: string[]) => void;
};

export function StreamParamsOptionsModal({
  opened,
  onClose,
  panel,
  streamWorkspaceOptions,
  outputOptions,
  onSetWorkspace,
  onSetOutputs,
}: Props) {
  return (
    <Modal opened={opened} onClose={onClose} title={`Params options ${panel?.title ?? ""}`} size="lg" centered>
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
            <MultiSelect
              size="sm"
              searchable
              clearable
              placeholder="Select outputs"
              comboboxProps={{ zIndex: 500 }}
              data={outputOptions}
              value={panel.outputIds}
              onChange={(value) => onSetOutputs(panel.id, value)}
            />
          </>
        ) : (
          <Text size="sm" c="dimmed">
            Select a params panel to edit options.
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
