import { Badge, Modal, Tabs, Tooltip } from "@mantine/core";
import type { ComponentProps } from "react";
import { InterlocksModal } from "./InterlocksModal";
import { WatchdogsPanel } from "./WatchdogsPanel";

type InterlocksPanelProps = Omit<
  ComponentProps<typeof InterlocksModal>,
  "opened" | "onClose" | "panelOnly"
>;

type WatchdogsPanelProps = ComponentProps<typeof WatchdogsPanel>;

type TabSummary = { color: string; tooltip: string };

type Props = {
  opened: boolean;
  onClose: () => void;
  interlocks: InterlocksPanelProps;
  watchdogs: WatchdogsPanelProps;
  /** Same summaries the header's Safety button is coloured from. */
  interlockSummary?: TabSummary;
  watchdogSummary?: TabSummary;
};

function TabCount({ count, summary }: { count: number; summary?: TabSummary }) {
  const badge = (
    <Badge size="xs" variant="light" color={summary?.color ?? "gray"}>
      {count}
    </Badge>
  );
  return summary ? (
    <Tooltip label={summary.tooltip} withArrow>
      {badge}
    </Tooltip>
  ) : (
    badge
  );
}

export function SafetyModal({
  opened,
  onClose,
  interlocks,
  watchdogs,
  interlockSummary,
  watchdogSummary,
}: Props) {
  const interlockCount = interlocks.processes.length;
  const watchdogCount = watchdogs.processes.length;
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Safety"
      size="clamp(56rem, 92vw, 96rem)"
      centered
    >
      <Tabs defaultValue="interlocks" keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab
            value="interlocks"
            rightSection={<TabCount count={interlockCount} summary={interlockSummary} />}
          >
            Interlocks
          </Tabs.Tab>
          <Tabs.Tab
            value="watchdogs"
            rightSection={<TabCount count={watchdogCount} summary={watchdogSummary} />}
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

