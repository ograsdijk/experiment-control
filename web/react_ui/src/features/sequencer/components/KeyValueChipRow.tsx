import { ActionIcon } from "@mantine/core";
import { IconTrash } from "@tabler/icons-react";
import type { ReactNode } from "react";

type Props = {
  nameControl: ReactNode;
  valueControl: ReactNode;
  removeLabel: string;
  onRemove: () => void;
};

export function KeyValueChipRow({
  nameControl,
  valueControl,
  removeLabel,
  onRemove,
}: Props) {
  return (
    <div className="sequencer-var-chip">
      <div className="sequencer-var-segment sequencer-var-name">{nameControl}</div>
      <div className="sequencer-var-segment sequencer-var-value">{valueControl}</div>
      <div className="sequencer-var-segment sequencer-var-remove">
        <ActionIcon
          size="sm"
          variant="subtle"
          color="red"
          aria-label={removeLabel}
          onClick={onRemove}
        >
          <IconTrash size={14} />
        </ActionIcon>
      </div>
    </div>
  );
}
