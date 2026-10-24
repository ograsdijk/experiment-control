import { useMemo, type ComponentProps, type Dispatch, type SetStateAction } from "react";
import { useFilteredLogRows, useLogs } from "../features/logs/LogsContext";
import { useFollowEdgeScroll } from "../features/logs/useFollowEdgeScroll";
import { logEntryKey } from "../features/logs/utils";
import { LogsModal } from "./LogsModal";

type BaseProps = Omit<
  ComponentProps<typeof LogsModal>,
  | "onReload"
  | "onClear"
  | "onToggleExpanded"
  | "onCopyMessage"
  | "filteredRows"
  | "totalRows"
>;

type Props = BaseProps & {
  loadLogTail: () => Promise<unknown> | void;
  setExpandedLogByKey: Dispatch<SetStateAction<Record<string, boolean>>>;
  copyTextToClipboard: (label: string, value: string) => Promise<unknown> | void;
};

export function LogsModalContainer({
  loadLogTail,
  setExpandedLogByKey,
  copyTextToClipboard,
  ...props
}: Props) {
  // The roster subscription and the filter derivation live here, not in
  // App, so live log lines only re-render this (open) modal.
  const { logStore } = useLogs();
  const { rows, filteredRows } = useFilteredLogRows();
  const itemKeys = useMemo(
    () => filteredRows.map((entry) => logEntryKey(entry)),
    [filteredRows]
  );
  useFollowEdgeScroll({
    viewportRef: props.viewportRef,
    opened: props.opened,
    enabled: props.autoScroll,
    newestFirst: props.sortNewestFirst,
    itemKeys,
    resetKey: `logs:${props.sortNewestFirst}`,
  });
  return (
    <LogsModal
      {...props}
      filteredRows={filteredRows}
      totalRows={rows.length}
      onReload={() => {
        void loadLogTail();
      }}
      onClear={() => {
        logStore.clear();
        setExpandedLogByKey({});
      }}
      onToggleExpanded={(entryKey) =>
        setExpandedLogByKey((prev) => ({
          ...prev,
          [entryKey]: !Boolean(prev[entryKey]),
        }))
      }
      onCopyMessage={(message) => {
        void copyTextToClipboard("Log message", message);
      }}
    />
  );
}
