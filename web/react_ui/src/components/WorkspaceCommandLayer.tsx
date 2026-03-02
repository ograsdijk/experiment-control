import type { ComponentProps } from "react";
import { DaqWorkspacesModal } from "./DaqWorkspacesModal";
import { DeviceCommandModal } from "./DeviceCommandModal";

type Props = {
  daq: ComponentProps<typeof DaqWorkspacesModal>;
  deviceCommand: ComponentProps<typeof DeviceCommandModal>;
};

export function WorkspaceCommandLayer({ daq, deviceCommand }: Props) {
  return (
    <>
      <DaqWorkspacesModal {...daq} />
      <DeviceCommandModal {...deviceCommand} />
    </>
  );
}
