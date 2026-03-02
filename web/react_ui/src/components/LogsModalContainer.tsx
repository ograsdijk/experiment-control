import type { ComponentProps, Dispatch, MutableRefObject, SetStateAction } from "react";
import type { LogEntry } from "../types";
import { LogsModal } from "./LogsModal";

type BaseProps = Omit<
  ComponentProps<typeof LogsModal>,
  "onReload" | "onClear" | "onToggleExpanded" | "onCopyMessage"
>;

type Props = BaseProps & {
  loadLogTail: () => Promise<unknown> | void;
  logSeenRef: MutableRefObject<Set<string>>;
  setLogRows: Dispatch<SetStateAction<LogEntry[]>>;
  setExpandedLogByKey: Dispatch<SetStateAction<Record<string, boolean>>>;
  copyTextToClipboard: (label: string, value: string) => Promise<unknown> | void;
};

export function LogsModalContainer({
  loadLogTail,
  logSeenRef,
  setLogRows,
  setExpandedLogByKey,
  copyTextToClipboard,
  ...props
}: Props) {
  return (
    <LogsModal
      {...props}
      onReload={() => {
        void loadLogTail();
      }}
      onClear={() => {
        logSeenRef.current = new Set();
        setLogRows([]);
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
