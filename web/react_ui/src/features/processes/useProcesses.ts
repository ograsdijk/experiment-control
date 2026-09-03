import { useMemo, useState } from "react";
import { fetchProcesses } from "../../api";
import type { ProcessStatus } from "../../types";
import { sameProcessStatuses } from "../polling/pollEquality";
import { useAdaptivePolling } from "../polling/useAdaptivePolling";

export type UseProcessesOptions = {
  /** Polling period in milliseconds. Defaults to 5000. */
  intervalMs?: number;
};

export type UseProcessesResult = {
  processes: ProcessStatus[];
  byId: Record<string, ProcessStatus>;
};

/**
 * Minimal polling primitive for the process registry. Distinct from the
 * heavier `useProcessesController` which is gated on modal-open state and
 * carries capability-loading bookkeeping; this hook just keeps the latest
 * `/api/processes` list in state.
 */
export function useProcesses(options: UseProcessesOptions = {}): UseProcessesResult {
  const { intervalMs = 5000 } = options;
  const [processes, setProcesses] = useState<ProcessStatus[]>([]);

  useAdaptivePolling({
    enabled: true,
    intervalMs,
    poll: (signal) => fetchProcesses(signal),
    onValue: setProcesses,
    equality: sameProcessStatuses,
    refreshOnVisible: intervalMs > 0,
    endpoint: "/api/processes",
  });

  const byId = useMemo(() => {
    const out: Record<string, ProcessStatus> = {};
    for (const p of processes) {
      if (p.process_id) {
        out[p.process_id] = p;
      }
    }
    return out;
  }, [processes]);

  return { processes, byId };
}
