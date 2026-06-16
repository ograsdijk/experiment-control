import { Badge, Button, Group, Select, Stack, Text, TextInput } from "@mantine/core";
import { IconPlus } from "@tabler/icons-react";
import type { SequencerOutlineMetadataEntry } from "../types";
import { KeyValueChipRow } from "./KeyValueChipRow";

type SelectOption = {
  value: string;
  label: string;
};

type Props = {
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>;
  onChange: (entries: SequencerOutlineMetadataEntry[]) => void;
  title: string;
  addLabel?: string;
  emptyLabel?: string;
  addEmptyHint?: string;
  nameLabel?: string;
  valueLabel?: string;
  removeLabel?: string;
  nextNamePrefix?: string;
  defaultNewValue?: string;
  valueOptions?: ReadonlyArray<SelectOption>;
  issueCount?: number;
  issueText?: string | null;
  onAdd?: () => void;
  valuePlaceholderResolver?: (entry: SequencerOutlineMetadataEntry) => string;
};

function nextEntryName(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>,
  prefix: string
): string {
  const existing = new Set(entries.map((entry) => entry.name));
  let index = existing.size + 1;
  while (existing.has(`${prefix}_${index}`)) {
    index += 1;
  }
  return `${prefix}_${index}`;
}

export function KeyValueChipList({
  entries,
  onChange,
  title,
  addLabel = "Add",
  emptyLabel = "No entries.",
  addEmptyHint,
  nameLabel = "Name",
  valueLabel = "Value",
  removeLabel = "Remove entry",
  nextNamePrefix = "field",
  defaultNewValue = '""',
  valueOptions,
  issueCount = 0,
  issueText = null,
  onAdd,
  valuePlaceholderResolver,
}: Props) {
  const updateEntry = (index: number, patch: Partial<SequencerOutlineMetadataEntry>) => {
    onChange(
      entries.map((entry, entryIndex) =>
        entryIndex === index ? { ...entry, ...patch } : entry
      )
    );
  };

  const removeEntry = (index: number) => {
    onChange(entries.filter((_, entryIndex) => entryIndex !== index));
  };

  const addEntry = () => {
    onChange([
      ...entries,
      {
        name: nextEntryName(entries, nextNamePrefix),
        value: defaultNewValue,
      },
    ]);
  };

  return (
    <Stack gap={6}>
      <Group justify="space-between" align="center">
        <Group gap={6} align="center">
          <Text size="xs" fw={600}>
            {title}
          </Text>
          {issueCount > 0 ? (
            <Badge size="xs" color="red" variant="light">
              {issueCount} issue{issueCount === 1 ? "" : "s"}
            </Badge>
          ) : null}
        </Group>
        <Button
          size="compact-xs"
          variant="light"
          leftSection={<IconPlus size={14} />}
          onClick={onAdd ?? addEntry}
        >
          {addLabel}
        </Button>
      </Group>
      {entries.length <= 0 ? (
        <Text size="xs" c="dimmed">
          {emptyLabel}
          {addEmptyHint ? ` ${addEmptyHint}` : ""}
        </Text>
      ) : (
        <Stack gap={6}>
          {entries.map((entry, index) => (
            <KeyValueChipRow
              key={`entry-${index}`}
              nameControl={
                <TextInput
                  size="xs"
                  aria-label={nameLabel}
                  placeholder="name"
                  variant="unstyled"
                  value={entry.name}
                  onChange={(event) =>
                    updateEntry(index, { name: event.currentTarget.value })
                  }
                />
              }
              valueControl={
                valueOptions && valueOptions.length > 0 ? (
                  <Select
                    size="xs"
                    aria-label={valueLabel}
                    placeholder={valuePlaceholderResolver ? valuePlaceholderResolver(entry) : "value"}
                    variant="unstyled"
                    data={Array.from(
                      new Map(
                        [
                          ...valueOptions,
                          ...(entry.value
                            ? [{ value: entry.value, label: entry.value }]
                            : []),
                        ].map((option) => [option.value, option] as const)
                      ).values()
                    )}
                    value={entry.value ?? ""}
                    allowDeselect={false}
                    searchable={false}
                    comboboxProps={{ withinPortal: false }}
                    onChange={(value) => {
                      if (value === null) {
                        return;
                      }
                      updateEntry(index, { value });
                    }}
                  />
                ) : (
                  <TextInput
                    size="xs"
                    aria-label={valueLabel}
                    placeholder={valuePlaceholderResolver ? valuePlaceholderResolver(entry) : "value"}
                    variant="unstyled"
                    value={entry.value ?? ""}
                    onChange={(event) =>
                      updateEntry(index, { value: event.currentTarget.value })
                    }
                  />
                )
              }
              removeLabel={removeLabel}
              onRemove={() => removeEntry(index)}
            />
          ))}
        </Stack>
      )}
      {issueText ? (
        <Text size="xs" c="red">
          {issueText}
        </Text>
      ) : null}
    </Stack>
  );
}
