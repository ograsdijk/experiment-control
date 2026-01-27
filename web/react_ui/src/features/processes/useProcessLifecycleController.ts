import { notifications } from "@mantine/notifications";
import { useCallback } from "react";
import { restartProcess, startProcess, stopProcess } from "../../api";
import { isHdfWriterProcess, isSequencerProcess } from "../runtime/helpers";
import type { ProcessStatus } from "../../types";

type ProcessAction = "start" | "stop" | "restart";

type UseProcessLifecycleControllerArgs = {
  processBusyById: Record<string, boolean>;
  setProcessBusy: (processId: string, busy: boolean) => void;
  invalidateProcessCapabilities: (processId: string) => void;
  refreshProcesses: () => Promise<ProcessStatus[]>;
  refreshHdfWriterStatus: (processId: string) => Promise<unknown>;
  refreshSequencerStatus: (processId: string) => Promise<unknown>;
};

export function useProcessLifecycleController({
  processBusyById,
  setProcessBusy,
  invalidateProcessCapabilities,
  refreshProcesses,
  refreshHdfWriterStatus,
  refreshSequencerStatus,
}: UseProcessLifecycleControllerArgs) {
  const handleProcessAction = useCallback(
    async (processId: string, action: ProcessAction) => {
      if (processBusyById[processId]) {
        return;
      }
      setProcessBusy(processId, true);
      try {
        const resp =
          action === "start"
            ? await startProcess(processId)
            : action === "stop"
              ? await stopProcess(processId)
              : await restartProcess(processId);
        if (!resp.ok) {
          notifications.show({
            color: "red",
            title: `Process ${action} failed`,
            message: resp.error?.message ?? resp.error?.code ?? "Unknown error",
          });
          return;
        }
        notifications.show({
          color: "teal",
          title: `Process ${action} requested`,
          message: processId,
        });
        invalidateProcessCapabilities(processId);
        const nextProcesses = await refreshProcesses();
        const refreshed = nextProcesses.find((item) => item.process_id === processId);
        if (
          refreshed &&
          isHdfWriterProcess(refreshed) &&
          ["RUNNING", "STARTING", "STOPPING"].includes(
            String(refreshed.state ?? "").toUpperCase()
          )
        ) {
          await refreshHdfWriterStatus(processId);
        }
        if (
          refreshed &&
          isSequencerProcess(refreshed) &&
          ["RUNNING", "STARTING", "STOPPING"].includes(
            String(refreshed.state ?? "").toUpperCase()
          )
        ) {
          await refreshSequencerStatus(processId);
        }
      } finally {
        setProcessBusy(processId, false);
      }
    },
    [
      invalidateProcessCapabilities,
      processBusyById,
      refreshHdfWriterStatus,
      refreshProcesses,
      refreshSequencerStatus,
      setProcessBusy,
    ]
  );

  return { handleProcessAction };
}
