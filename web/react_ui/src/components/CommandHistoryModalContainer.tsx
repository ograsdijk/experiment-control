import type { MutableRefObject } from "react";
import type { useCommandHistoryController } from "../features/commands/useCommandHistoryController";
import type { DeviceStatus } from "../types";
import { CommandHistoryModal } from "./CommandHistoryModal";

type CommandHistoryControllerState = ReturnType<typeof useCommandHistoryController>;

type Props = {
  opened: boolean;
  onClose: () => void;
  controller: CommandHistoryControllerState;
  devices: DeviceStatus[];
  colorScheme: "light" | "dark";
  viewportRef: MutableRefObject<HTMLDivElement | null>;
  onCopyJson: (label: string, payload: unknown) => void;
};

export function CommandHistoryModalContainer({
  opened,
  onClose,
  controller,
  devices,
  colorScheme,
  viewportRef,
  onCopyJson,
}: Props) {
  return (
    <CommandHistoryModal
      opened={opened}
      onClose={onClose}
      controller={controller}
      devices={devices}
      colorScheme={colorScheme}
      viewportRef={viewportRef}
      onCopyJson={onCopyJson}
    />
  );
}
