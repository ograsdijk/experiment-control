import type { MutableRefObject } from "react";
import { clampCommandHistoryLimit } from "../features/commands/utils";
import type { CommandHistoryEntry } from "../features/commands/types";
import type { DeviceStatus } from "../types";
import { CommandHistoryModal } from "./CommandHistoryModal";

type Props = {
  opened: boolean;
  onClose: () => void;
  filteredRows: CommandHistoryEntry[];
  devices: DeviceStatus[];
  totalRows: number;
  persistLimit: number;
  persistLimitMin: number;
  persistLimitMax: number;
  persistLimitBounds: { min: number; max: number };
  onPersistLimitChange: (value: number) => void;
  autoScroll: boolean;
  onAutoScrollChange: (value: boolean) => void;
  onClear: () => void;
  targetFilter: string;
  onTargetFilterChange: (value: string) => void;
  statusFilter: string;
  onStatusFilterChange: (value: string) => void;
  sourceFilter: string;
  onSourceFilterChange: (value: string) => void;
  sourceOptions: string[];
  textFilter: string;
  onTextFilterChange: (value: string) => void;
  viewportRef: MutableRefObject<HTMLDivElement | null>;
  onCopyJson: (label: string, payload: unknown) => void;
};

export function CommandHistoryModalContainer({
  persistLimitBounds,
  onPersistLimitChange,
  ...props
}: Props) {
  return (
    <CommandHistoryModal
      {...props}
      onPersistLimitChange={(value) =>
        onPersistLimitChange(clampCommandHistoryLimit(value, persistLimitBounds))
      }
    />
  );
}
