import { Card, Group, Stack, Text, TextInput } from "@mantine/core";
import {
  applyEditedIfStep,
  applyEditedWaitUntilStep,
  applyEditedWhileStep,
} from "../editing";
import { renderValue } from "../editor_helpers";
import type {
  SequencerOutlineMetadataEntry,
  SequencerStepOutlineNode,
} from "../types";
import { KeyValueChipList } from "./KeyValueChipList";

type CommonProps = {
  node: SequencerStepOutlineNode;
  yamlText: string;
  onYamlTextChange: (value: string) => void;
};

const cardStyle = {
  border: "1px solid var(--card-border)",
  background: "rgba(148, 163, 184, 0.04)",
} as const;

export function WaitUntilStepEditor({
  node,
  yamlText,
  onYamlTextChange,
}: CommonProps) {
  if (!node.waitUntilDetail) {
    return null;
  }

  const sample = node.waitUntilDetail.sample;
  const condition = node.waitUntilDetail.condition;
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
        <KeyValueChipList
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
        <KeyValueChipList
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
          Branch bodies remain unchanged in this phase.
        </Text>
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
        <KeyValueChipList
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
          The nested body remains unchanged in this phase.
        </Text>
      </Stack>
    </Card>
  );
}
