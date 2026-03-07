import { Card, Stack, Text, TextInput } from "@mantine/core";
import {
  applyEditedRepeatStep,
  applyEditedSleepStep,
} from "../editing";
import { renderValue } from "../editor_helpers";
import type { SequencerStepOutlineNode } from "../types";

type CommonProps = {
  node: SequencerStepOutlineNode;
  yamlText: string;
  onYamlTextChange: (value: string) => void;
};

const cardStyle = {
  border: "1px solid var(--card-border)",
  background: "rgba(148, 163, 184, 0.04)",
} as const;

export function SleepStepEditor({
  node,
  yamlText,
  onYamlTextChange,
}: CommonProps) {
  if (!node.sleepDetail) {
    return null;
  }

  return (
    <Card radius="sm" p="xs" style={cardStyle}>
      <Stack gap={8}>
        <TextInput
          size="xs"
          label="Duration"
          value={renderValue(node.sleepDetail.duration)}
          onChange={(event) =>
            onYamlTextChange(
              applyEditedSleepStep(yamlText, node, event.currentTarget.value)
            )
          }
        />
      </Stack>
    </Card>
  );
}

export function RepeatStepEditor({
  node,
  yamlText,
  onYamlTextChange,
}: CommonProps) {
  if (!node.repeatDetail) {
    return null;
  }

  return (
    <Card radius="sm" p="xs" style={cardStyle}>
      <Stack gap={8}>
        <TextInput
          size="xs"
          label="Times"
          value={renderValue(node.repeatDetail.times)}
          onChange={(event) =>
            onYamlTextChange(
              applyEditedRepeatStep(yamlText, node, event.currentTarget.value)
            )
          }
        />
        <Text size="xs" c="dimmed">
          Edit the repeat body from the step tree using insert/move/delete actions on child steps.
        </Text>
      </Stack>
    </Card>
  );
}
