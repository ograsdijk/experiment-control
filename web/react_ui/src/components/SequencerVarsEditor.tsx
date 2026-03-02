import { ActionIcon, Button, Card, Group, Stack, Text, TextInput } from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import type { SequencerOutlineMetadataEntry } from "../features/sequencer/types";

type Props = {
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>;
  onChange: (entries: SequencerOutlineMetadataEntry[]) => void;
};

function nextVarName(entries: ReadonlyArray<SequencerOutlineMetadataEntry>): string {
  const existing = new Set(entries.map((entry) => entry.name));
  let index = existing.size + 1;
  while (existing.has(`var_${index}`)) {
    index += 1;
  }
  return `var_${index}`;
}

export function SequencerVarsEditor({ entries, onChange }: Props) {
  const updateEntry = (index: number, patch: Partial<SequencerOutlineMetadataEntry>) => {
    const next = entries.map((entry, entryIndex) =>
      entryIndex === index ? { ...entry, ...patch } : entry
    );
    onChange(next);
  };

  const removeEntry = (index: number) => {
    onChange(entries.filter((_, entryIndex) => entryIndex !== index));
  };

  const addEntry = () => {
    onChange([
      ...entries,
      {
        name: nextVarName(entries),
        value: '""',
      },
    ]);
  };

  return (
    <Card radius="sm" p="xs" style={{ border: "1px solid var(--card-border)" }}>
      <Stack gap={6}>
        <Group justify="space-between" align="center">
          <Text size="xs" fw={600}>
            Variables
          </Text>
          <Button
            size="compact-xs"
            variant="light"
            leftSection={<IconPlus size={14} />}
            onClick={addEntry}
          >
            Add
          </Button>
        </Group>
        {entries.length <= 0 ? (
          <Text size="xs" c="dimmed">
            No variables. Add one to create a top-level vars block.
          </Text>
        ) : (
          <Stack gap={6}>
            {entries.map((entry, index) => (
              <div key={`${entry.name}:${index}`} className="sequencer-var-chip">
                <div className="sequencer-var-segment sequencer-var-name">
                  <TextInput
                    size="xs"
                    aria-label="Variable name"
                    placeholder="name"
                    variant="unstyled"
                    value={entry.name}
                    onChange={(event) =>
                      updateEntry(index, { name: event.currentTarget.value })
                    }
                  />
                </div>
                <div className="sequencer-var-segment sequencer-var-value">
                  <TextInput
                    size="xs"
                    aria-label="Variable value"
                    placeholder="value"
                    variant="unstyled"
                    value={entry.value ?? ""}
                    onChange={(event) =>
                      updateEntry(index, { value: event.currentTarget.value })
                    }
                  />
                </div>
                <div className="sequencer-var-segment sequencer-var-remove">
                  <ActionIcon
                    size="sm"
                    variant="subtle"
                    color="red"
                    aria-label="Remove variable"
                    onClick={() => removeEntry(index)}
                  >
                    <IconTrash size={14} />
                  </ActionIcon>
                </div>
              </div>
            ))}
          </Stack>
        )}
      </Stack>
    </Card>
  );
}
