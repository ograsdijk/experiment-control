import { Button, Group, Modal, NumberInput, Stack, Text } from "@mantine/core";

type Props = {
  opened: boolean;
  onClose: () => void;
  title: string;
  autoRange: { min: number; max: number } | null;
  draftMin: string | number;
  onDraftMinChange: (value: string | number) => void;
  draftMax: string | number;
  onDraftMaxChange: (value: string | number) => void;
  draftInvalid: boolean;
  onApply: () => void;
};

export function YAxisModal({
  opened,
  onClose,
  title,
  autoRange,
  draftMin,
  onDraftMinChange,
  draftMax,
  onDraftMaxChange,
  draftInvalid,
  onApply,
}: Props) {
  return (
    <Modal opened={opened} onClose={onClose} title={title} centered>
      <Stack gap="md">
        {autoRange ? (
          <Text size="xs" c="dimmed">
            Current auto limits: {autoRange.min.toFixed(4)} to{" "}
            {autoRange.max.toFixed(4)}
          </Text>
        ) : (
          <Text size="xs" c="dimmed">
            No data yet for auto limit suggestion.
          </Text>
        )}
        <Group gap="sm" align="end">
          <NumberInput
            label="Y min"
            value={draftMin}
            onChange={onDraftMinChange}
            flex={1}
          />
          <NumberInput
            label="Y max"
            value={draftMax}
            onChange={onDraftMaxChange}
            flex={1}
          />
        </Group>
        {draftInvalid && (
          <Text size="xs" c="red">
            Enter numeric limits where min is less than max.
          </Text>
        )}
        <Group justify="flex-end">
          <Button variant="light" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={onApply}>Apply</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
