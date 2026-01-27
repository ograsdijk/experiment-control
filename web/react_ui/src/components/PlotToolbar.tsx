import { Button, Group, NumberInput, Text } from "@mantine/core";

type PlotToolbarProps = {
  title?: string;
  timeWindowS: number;
  onTimeWindowChange: (value: number) => void;
  onClear: () => void;
};

export function PlotToolbar({
  title = "Plot",
  timeWindowS,
  onTimeWindowChange,
  onClear,
}: PlotToolbarProps) {
  return (
    <Group justify="space-between" align="center">
      <Group gap="sm">
        <Text fw={600}>{title}</Text>
        <NumberInput
          size="xs"
          min={5}
          max={600}
          value={timeWindowS}
          onChange={(value) => onTimeWindowChange(Number(value))}
          label="Window (s)"
          w={140}
        />
      </Group>
      <Button size="xs" variant="light" onClick={onClear}>
        Clear
      </Button>
    </Group>
  );
}
