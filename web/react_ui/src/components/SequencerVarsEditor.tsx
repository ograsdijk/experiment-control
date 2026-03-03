import { Card } from "@mantine/core";
import { KeyValueChipList } from "../features/sequencer/components/KeyValueChipList";
import type { SequencerOutlineMetadataEntry } from "../features/sequencer/types";

type Props = {
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>;
  onChange: (entries: SequencerOutlineMetadataEntry[]) => void;
  title?: string;
  addLabel?: string;
  emptyLabel?: string;
  addEmptyHint?: string;
  nameLabel?: string;
  valueLabel?: string;
  removeLabel?: string;
  nextNamePrefix?: string;
  valueOptions?: ReadonlyArray<{ value: string; label: string }>;
};

export function SequencerVarsEditor({
  entries,
  onChange,
  title = "Variables",
  addLabel = "Add",
  emptyLabel = "No variables.",
  addEmptyHint = "Add one to create a top-level vars block.",
  nameLabel = "Variable name",
  valueLabel = "Variable value",
  removeLabel = "Remove variable",
  nextNamePrefix = "var",
  valueOptions,
}: Props) {
  return (
    <Card radius="sm" p="xs" style={{ border: "1px solid var(--card-border)" }}>
      <KeyValueChipList
        entries={entries}
        onChange={onChange}
        title={title}
        addLabel={addLabel}
        emptyLabel={emptyLabel}
        addEmptyHint={addEmptyHint}
        nameLabel={nameLabel}
        valueLabel={valueLabel}
        removeLabel={removeLabel}
        nextNamePrefix={nextNamePrefix}
        valueOptions={valueOptions}
      />
    </Card>
  );
}
