import { useEffect, useMemo, useState } from "react";
import { fetchProcesses } from "../../api";
import type { ProcessStatus } from "../../types";

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

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const next = await fetchProcesses();
      if (alive) {
        setProcesses(next);
      }
    };
    void load();
    if (intervalMs <= 0) {
      return () => {
        alive = false;
      };
    }
    const timer = window.setInterval(() => void load(), intervalMs);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [intervalMs]);

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
