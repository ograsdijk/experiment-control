import { Badge, Modal, Tabs } from "@mantine/core";
import type { ComponentProps } from "react";
import { InterlocksModal } from "./InterlocksModal";
import { WatchdogsPanel } from "./WatchdogsPanel";

type InterlocksPanelProps = Omit<
  ComponentProps<typeof InterlocksModal>,
  "opened" | "onClose" | "panelOnly"
>;

type WatchdogsPanelProps = ComponentProps<typeof WatchdogsPanel>;

type Props = {
  opened: boolean;
  onClose: () => void;
  interlocks: InterlocksPanelProps;
  watchdogs: WatchdogsPanelProps;
};

export function SafetyModal({ opened, onClose, interlocks, watchdogs }: Props) {
  const interlockCount = interlocks.processes.length;
  const watchdogCount = watchdogs.processes.length;
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Safety"
      size="clamp(56rem, 92vw, 96rem)"
      centered
      zIndex={420}
    >
      <Tabs defaultValue="interlocks" keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab
            value="interlocks"
            rightSection={
              <Badge size="xs" variant="light" color="gray">
                {interlockCount}
              </Badge>
            }
          >
            Interlocks
          </Tabs.Tab>
          <Tabs.Tab
            value="watchdogs"
            rightSection={
              <Badge size="xs" variant="light" color="gray">
                {watchdogCount}
              </Badge>
            }
          >
            Watchdogs
          </Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="interlocks" pt="sm">
          <InterlocksModal
            opened
            onClose={() => {}}
            panelOnly
            {...interlocks}
          />
        </Tabs.Panel>
        <Tabs.Panel value="watchdogs" pt="sm">
          <WatchdogsPanel {...watchdogs} />
        </Tabs.Panel>
      </Tabs>
    </Modal>
  );
}

