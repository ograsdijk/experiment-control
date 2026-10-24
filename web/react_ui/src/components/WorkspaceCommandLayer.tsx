import { lazy, Suspense, type ComponentProps } from "react";
import type { DaqWorkspacesModal as DaqWorkspacesModalComponent } from "./DaqWorkspacesModal";
import { DeviceCommandModal } from "./DeviceCommandModal";

// The DAQ workspace editor (and its DAG graph preview) only renders while
// open, so keep it out of the main chunk.
const DaqWorkspacesModal = lazy(() =>
  import("./DaqWorkspacesModal").then((module) => ({
    default: module.DaqWorkspacesModal,
  }))
);

type Props = {
  daq: ComponentProps<typeof DaqWorkspacesModalComponent>;
  deviceCommand: ComponentProps<typeof DeviceCommandModal>;
};

export function WorkspaceCommandLayer({ daq, deviceCommand }: Props) {
  return (
    <>
      {daq.opened ? (
        <Suspense fallback={null}>
          <DaqWorkspacesModal {...daq} />
        </Suspense>
      ) : null}
      <DeviceCommandModal {...deviceCommand} />
    </>
  );
}
