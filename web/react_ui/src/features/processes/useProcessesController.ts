import { useCallback, useEffect, useState } from "react";
import { fetchProcesses, type ApiResponse } from "../../api";
import type { CapabilityMember, ProcessStatus } from "../../types";

type UseProcessesControllerArgs = {
  callProcessFn: (
    processId: string,
    action: string,
    params: Record<string, unknown>
  ) => Promise<ApiResponse<unknown>>;
};

function extractCapabilityMembers(
  value: unknown
): CapabilityMember[] {
  if (!value || typeof value !== "object") {
    return [];
  }
  const direct = value as { members?: unknown };
  if (Array.isArray(direct.members)) {
    return direct.members as CapabilityMember[];
  }
  const nested = value as { result?: unknown };
  if (nested.result && typeof nested.result === "object") {
    const inner = nested.result as { members?: unknown };
    if (Array.isArray(inner.members)) {
      return inner.members as CapabilityMember[];
    }
  }
  return [];
}

function isProcessRpcReady(process: ProcessStatus | undefined): boolean {
  if (!process) {
    return false;
  }
  if (typeof process.registered === "boolean") {
    return process.registered;
  }
  const endpoint =
    typeof process.rpc_endpoint === "string" ? process.rpc_endpoint.trim() : "";
  return endpoint.length > 0;
}

export function useProcessesController({ callProcessFn }: UseProcessesControllerArgs) {
  const [processes, setProcesses] = useState<ProcessStatus[]>([]);
  const [processOpen, setProcessOpen] = useState(false);
  const [processBusyById, setProcessBusyById] = useState<
    Record<string, boolean>
  >({});
  const [capabilitiesByProcess, setCapabilitiesByProcess] = useState<
    Record<string, CapabilityMember[]>
  >({});
  const [processCapabilitiesErrorById, setProcessCapabilitiesErrorById] =
    useState<Record<string, string>>({});

  const refreshProcesses = useCallback(async () => {
    const next = await fetchProcesses();
    setProcesses(next);
    return next;
  }, []);

  const setProcessBusy = useCallback((processId: string, busy: boolean) => {
    setProcessBusyById((prev) => ({ ...prev, [processId]: busy }));
  }, []);

  const ensureProcessCapabilitiesLoaded = useCallback(
    async (processId: string) => {
      const existing = capabilitiesByProcess[processId] ?? [];
      if (existing.length > 0) {
        return existing;
      }
      const process = processes.find((item) => item.process_id === processId);
      if (process && !isProcessRpcReady(process)) {
        setProcessCapabilitiesErrorById((prev) => ({
          ...prev,
          [processId]: "Process RPC endpoint is not ready.",
        }));
        return [];
      }
      const resp = await callProcessFn(processId, "process.capabilities", {});
      const members = extractCapabilityMembers(resp.result);
      if (resp.ok && members.length > 0) {
        setCapabilitiesByProcess((prev) => ({ ...prev, [processId]: members }));
        setProcessCapabilitiesErrorById((prev) => {
          if (!(processId in prev)) {
            return prev;
          }
          const next = { ...prev };
          delete next[processId];
          return next;
        });
        return members;
      }
      const message =
        resp.error?.code ??
        resp.error?.message ??
        "Process RPC endpoint is not ready.";
      setProcessCapabilitiesErrorById((prev) => ({ ...prev, [processId]: message }));
      return [];
    },
    [callProcessFn, capabilitiesByProcess, processes]
  );

  const invalidateProcessCapabilities = useCallback((processId: string) => {
    setCapabilitiesByProcess((prev) => {
      if (!(processId in prev)) {
        return prev;
      }
      const next = { ...prev };
      delete next[processId];
      return next;
    });
    setProcessCapabilitiesErrorById((prev) => {
      if (!(processId in prev)) {
        return prev;
      }
      const next = { ...prev };
      delete next[processId];
      return next;
    });
  }, []);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const next = await fetchProcesses();
      if (alive) {
        setProcesses(next);
      }
    };
    void load();
    const interval = setInterval(() => {
      void load();
    }, 5000);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    if (!processOpen || processes.length === 0) {
      return;
    }
    let cancelled = false;
    const loadProcessCapabilities = async () => {
      const nextCaps: Record<string, CapabilityMember[]> = {};
      const errors: Record<string, string> = {};
      for (const process of processes) {
        const processId = process.process_id;
        const state = String(process.state ?? "").toUpperCase();
        const existing = capabilitiesByProcess[processId] ?? [];
        if (existing.length > 0) {
          continue;
        }
        if (!["RUNNING", "STARTING", "STOPPING"].includes(state)) {
          errors[processId] = "Process RPC is unavailable while process is stopped.";
          continue;
        }
        if (!isProcessRpcReady(process)) {
          errors[processId] = "Process RPC endpoint is not ready.";
          continue;
        }
        const resp = await callProcessFn(processId, "process.capabilities", {});
        if (cancelled) {
          return;
        }
        const members = extractCapabilityMembers(resp.result);
        if (resp.ok && members.length > 0) {
          nextCaps[processId] = members;
          continue;
        }
        errors[processId] =
          resp.error?.code ??
          resp.error?.message ??
          "Process RPC endpoint is not ready.";
      }
      if (cancelled) {
        return;
      }
      if (Object.keys(nextCaps).length > 0) {
        setCapabilitiesByProcess((prev) => ({ ...prev, ...nextCaps }));
      }
      if (Object.keys(nextCaps).length > 0 || Object.keys(errors).length > 0) {
        setProcessCapabilitiesErrorById((prev) => {
          const next = { ...prev };
          for (const processId of Object.keys(nextCaps)) {
            delete next[processId];
          }
          for (const [processId, message] of Object.entries(errors)) {
            next[processId] = message;
          }
          return next;
        });
      }
    };
    void loadProcessCapabilities();
    return () => {
      cancelled = true;
    };
  }, [callProcessFn, processOpen, processes, capabilitiesByProcess]);

  return {
    processes,
    processOpen,
    setProcessOpen,
    processBusyById,
    setProcessBusy,
    capabilitiesByProcess,
    processCapabilitiesErrorById,
    refreshProcesses,
    ensureProcessCapabilitiesLoaded,
    invalidateProcessCapabilities,
  };
}
