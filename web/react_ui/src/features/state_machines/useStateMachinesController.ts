import { notifications } from "@mantine/notifications";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchStateMachineGraph,
  fetchStateMachineStatus,
  type ApiResponse,
} from "../../api";
import { isProcessRpcStateAvailable } from "../runtime/helpers";
import type {
  CapabilityMember,
  ProcessStatus,
  StateMachineGraph,
  StateMachineStatus,
} from "../../types";

type CallOptions = {
  requestId?: string;
  sourceKind?: string;
  sourceId?: string;
};

type UseStateMachinesControllerArgs = {
  processes: ProcessStatus[];
  capabilitiesByProcess: Record<string, CapabilityMember[]>;
  refreshProcesses: () => Promise<ProcessStatus[]>;
  ensureProcessCapabilitiesLoaded: (processId: string) => Promise<CapabilityMember[]>;
  callProcessFn: (
    processId: string,
    action: string,
    params: Record<string, unknown>,
    options?: CallOptions
  ) => Promise<ApiResponse<unknown>>;
};

type RefreshStateMachineProcessOptions = {
  showLoading?: boolean;
};

export type StateMachineBinding = {
  namespace: string;
  statusAction: string;
  graphAction: string | null;
  actionMembers: CapabilityMember[];
};

export type StateMachineProcessRow = {
  process: ProcessStatus;
  binding: StateMachineBinding;
  status: StateMachineStatus | null;
  statusLoading: boolean;
  statusError: string | null;
  statusAgeS: number | null;
  stale: boolean;
  busy: boolean;
  busyAction: string | null;
  hasActiveOperation: boolean;
  graph: StateMachineGraph | null;
  graphLoading: boolean;
  graphError: string | null;
};

export type StateMachinesSummary = {
  total: number;
  active: number;
  error: number;
  stale: number;
};

const STATUS_STALE_THRESHOLD_S = 3.0;

function namespaceSortWeight(namespace: string): number {
  const value = namespace.trim().toLowerCase();
  if (value === "state_machine") {
    return 0;
  }
  if (value.endsWith("state_machine")) {
    return 1;
  }
  return 2;
}

function detectStateMachineBinding(
  capabilities: CapabilityMember[]
): StateMachineBinding | null {
  const memberByName = new Map<string, CapabilityMember>();
  const statusPrefixes = new Set<string>();
  for (const member of capabilities) {
    const name = String(member.name ?? "").trim();
    if (!name) {
      continue;
    }
    memberByName.set(name, member);
    if (name.endsWith(".status")) {
      statusPrefixes.add(name.slice(0, -".status".length));
    }
  }
  const namespaces = [...statusPrefixes];
  if (namespaces.length === 0) {
    return null;
  }
  namespaces.sort((a, b) => {
    const aw = namespaceSortWeight(a);
    const bw = namespaceSortWeight(b);
    if (aw !== bw) {
      return aw - bw;
    }
    return a.localeCompare(b);
  });
  const namespace = namespaces[0];
  const statusAction = `${namespace}.status`;
  const graphAction = memberByName.has(`${namespace}.graph`)
    ? `${namespace}.graph`
    : null;
  if (!graphAction) {
    return null;
  }
  const actionMembers = capabilities
    .filter((member) => {
      const name = String(member.name ?? "").trim();
      return (
        name.startsWith(`${namespace}.`) &&
        name !== statusAction &&
        name !== `${namespace}.stop` &&
        name !== `${namespace}.graph` &&
        name !== `${namespace}.history.tail`
      );
    })
    .sort((a, b) => String(a.name ?? "").localeCompare(String(b.name ?? "")));
  return {
    namespace,
    statusAction,
    graphAction,
    actionMembers,
  };
}

function getStatusDetailValue(
  status: StateMachineStatus | null,
  path: readonly string[]
): unknown {
  if (!status || !status.status_detail || typeof status.status_detail !== "object") {
    return null;
  }
  let current: unknown = status.status_detail;
  for (const key of path) {
    if (!current || typeof current !== "object" || Array.isArray(current)) {
      return null;
    }
    current = (current as Record<string, unknown>)[key];
  }
  return current;
}

function hasActiveOperation(status: StateMachineStatus | null): boolean {
  if (!status) {
    return false;
  }
  const operationActive =
    getStatusDetailValue(status, ["operation", "active"]) ??
    getStatusDetailValue(status, ["active_operation"]);
  if (typeof operationActive === "boolean") {
    return operationActive;
  }
  if (typeof operationActive === "string") {
    if (operationActive.trim().length > 0) {
      return true;
    }
  } else if (operationActive != null) {
    return true;
  }
  const state = String(status.state ?? "").trim().toUpperCase();
  return (
    state === "STARTING" ||
    state === "BOTH_OPERATING" ||
    state === "OPERATING" ||
    state === "STOPPING" ||
    state === "RUNNING" ||
    state === "ACTIVE"
  );
}

function statusHasError(
  process: ProcessStatus,
  status: StateMachineStatus | null,
  statusError: string | null
): boolean {
  if (statusError) {
    return true;
  }
  const processState = String(process.state ?? "").toUpperCase();
  if (processState === "FAILED" || processState === "CRASHLOOP") {
    return true;
  }
  const state = String(status?.state ?? "").toUpperCase();
  if (state === "ERROR") {
    return true;
  }
  return Boolean(String(status?.last_error ?? "").trim());
}

function isDangerousAction(actionName: string): boolean {
  const text = actionName.toLowerCase();
  return text.includes("shutdown") || text.includes("abort");
}

export function useStateMachinesController({
  processes,
  capabilitiesByProcess,
  refreshProcesses,
  ensureProcessCapabilitiesLoaded,
  callProcessFn,
}: UseStateMachinesControllerArgs) {
  const [stateMachinesOpen, setStateMachinesOpen] = useState(false);
  const [selectedProcessId, setSelectedProcessId] = useState<string | null>(null);

  const [bindingsByProcessId, setBindingsByProcessId] = useState<
    Record<string, StateMachineBinding>
  >({});
  const [statusByProcessId, setStatusByProcessId] = useState<
    Record<string, StateMachineStatus | null>
  >({});
  const [statusFetchedAtByProcessId, setStatusFetchedAtByProcessId] = useState<
    Record<string, number>
  >({});
  const [statusLoadingByProcessId, setStatusLoadingByProcessId] = useState<
    Record<string, boolean>
  >({});
  const [statusErrorByProcessId, setStatusErrorByProcessId] = useState<
    Record<string, string>
  >({});
  const [busyByProcessId, setBusyByProcessId] = useState<Record<string, boolean>>(
    {}
  );
  const [busyActionByProcessId, setBusyActionByProcessId] = useState<
    Record<string, string | null>
  >({});
  const [graphByProcessId, setGraphByProcessId] = useState<
    Record<string, StateMachineGraph | null>
  >({});
  const [graphLoadingByProcessId, setGraphLoadingByProcessId] = useState<
    Record<string, boolean>
  >({});
  const [graphErrorByProcessId, setGraphErrorByProcessId] = useState<
    Record<string, string>
  >({});

  const processesRef = useRef(processes);
  const capabilitiesByProcessRef = useRef(capabilitiesByProcess);
  const refreshProcessesRef = useRef(refreshProcesses);
  const ensureProcessCapabilitiesLoadedRef = useRef(ensureProcessCapabilitiesLoaded);
  const bindingsByProcessIdRef = useRef(bindingsByProcessId);
  const selectedProcessIdRef = useRef<string | null>(selectedProcessId);
  const busyByProcessIdRef = useRef(busyByProcessId);

  useEffect(() => {
    processesRef.current = processes;
  }, [processes]);
  useEffect(() => {
    capabilitiesByProcessRef.current = capabilitiesByProcess;
  }, [capabilitiesByProcess]);
  useEffect(() => {
    refreshProcessesRef.current = refreshProcesses;
  }, [refreshProcesses]);
  useEffect(() => {
    ensureProcessCapabilitiesLoadedRef.current = ensureProcessCapabilitiesLoaded;
  }, [ensureProcessCapabilitiesLoaded]);
  useEffect(() => {
    bindingsByProcessIdRef.current = bindingsByProcessId;
  }, [bindingsByProcessId]);
  useEffect(() => {
    selectedProcessIdRef.current = selectedProcessId;
  }, [selectedProcessId]);
  useEffect(() => {
    busyByProcessIdRef.current = busyByProcessId;
  }, [busyByProcessId]);

  const setStatusLoading = useCallback((processId: string, loading: boolean) => {
    setStatusLoadingByProcessId((prev) => {
      if (prev[processId] === loading) {
        return prev;
      }
      return { ...prev, [processId]: loading };
    });
  }, []);

  const setGraphLoading = useCallback((processId: string, loading: boolean) => {
    setGraphLoadingByProcessId((prev) => {
      if (prev[processId] === loading) {
        return prev;
      }
      return { ...prev, [processId]: loading };
    });
  }, []);

  const refreshStateMachineGraph = useCallback(
    async (processId: string, opts?: RefreshStateMachineProcessOptions) => {
      const showLoading = opts?.showLoading === true;
      const binding = bindingsByProcessIdRef.current[processId];
      if (!binding || !binding.graphAction) {
        setGraphByProcessId((prev) => ({ ...prev, [processId]: null }));
        setGraphErrorByProcessId((prev) => {
          if (!(processId in prev)) {
            return prev;
          }
          const next = { ...prev };
          delete next[processId];
          return next;
        });
        return;
      }
      if (showLoading) {
        setGraphLoading(processId, true);
      }
      try {
        const graph = await fetchStateMachineGraph(processId, binding.graphAction);
        if (graph === null) {
          setGraphByProcessId((prev) => ({ ...prev, [processId]: null }));
          setGraphErrorByProcessId((prev) => ({
            ...prev,
            [processId]: "State-machine graph unavailable",
          }));
        } else {
          setGraphByProcessId((prev) => ({ ...prev, [processId]: graph }));
          setGraphErrorByProcessId((prev) => {
            if (!(processId in prev)) {
              return prev;
            }
            const next = { ...prev };
            delete next[processId];
            return next;
          });
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setGraphErrorByProcessId((prev) => ({ ...prev, [processId]: message }));
      } finally {
        if (showLoading) {
          setGraphLoading(processId, false);
        }
      }
    },
    [setGraphLoading]
  );

  const refreshStateMachineProcess = useCallback(
    async (
      processId: string,
      processHint?: ProcessStatus,
      opts?: RefreshStateMachineProcessOptions
    ) => {
      const showLoading = opts?.showLoading === true;
      const process =
        processHint ?? processesRef.current.find((item) => item.process_id === processId);
      const binding = bindingsByProcessIdRef.current[processId];
      if (!process || !binding || !isProcessRpcStateAvailable(process)) {
        return;
      }
      if (showLoading) {
        setStatusLoading(processId, true);
      }
      try {
        const status = await fetchStateMachineStatus(processId, binding.statusAction);
        if (status === null) {
          setStatusErrorByProcessId((prev) => ({
            ...prev,
            [processId]: "State-machine status unavailable",
          }));
        } else {
          setStatusByProcessId((prev) => ({ ...prev, [processId]: status }));
          setStatusFetchedAtByProcessId((prev) => ({
            ...prev,
            [processId]: Date.now() / 1000,
          }));
          setStatusErrorByProcessId((prev) => {
            if (!(processId in prev)) {
              return prev;
            }
            const next = { ...prev };
            delete next[processId];
            return next;
          });
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setStatusErrorByProcessId((prev) => ({ ...prev, [processId]: message }));
      } finally {
        if (showLoading) {
          setStatusLoading(processId, false);
        }
      }
    },
    [setStatusLoading]
  );

  const refreshStateMachinesModalData = useCallback(async () => {
    const nextProcesses = await refreshProcessesRef.current();
    const discovered: Record<string, StateMachineBinding> = {};
    for (const process of nextProcesses) {
      if (!isProcessRpcStateAvailable(process)) {
        continue;
      }
      const processId = process.process_id;
      const existingCaps = capabilitiesByProcessRef.current[processId] ?? [];
      const caps =
        existingCaps.length > 0
          ? existingCaps
          : await ensureProcessCapabilitiesLoadedRef.current(processId);
      const binding = detectStateMachineBinding(caps);
      if (binding) {
        discovered[processId] = binding;
      }
    }
    setBindingsByProcessId(discovered);
    bindingsByProcessIdRef.current = discovered;

    const discoveredIds = new Set(Object.keys(discovered));
    const processById = new Map(nextProcesses.map((process) => [process.process_id, process]));

    setStatusByProcessId((prev) => {
      const next: Record<string, StateMachineStatus | null> = {};
      for (const [processId, status] of Object.entries(prev)) {
        if (discoveredIds.has(processId)) {
          next[processId] = status;
        }
      }
      return next;
    });
    setStatusFetchedAtByProcessId((prev) => {
      const next: Record<string, number> = {};
      for (const [processId, fetchedAt] of Object.entries(prev)) {
        if (discoveredIds.has(processId)) {
          next[processId] = fetchedAt;
        }
      }
      return next;
    });
    setStatusLoadingByProcessId((prev) => {
      const next: Record<string, boolean> = {};
      for (const [processId, loading] of Object.entries(prev)) {
        if (discoveredIds.has(processId)) {
          next[processId] = loading;
        }
      }
      return next;
    });
    setStatusErrorByProcessId((prev) => {
      const next: Record<string, string> = {};
      for (const [processId, error] of Object.entries(prev)) {
        if (discoveredIds.has(processId)) {
          next[processId] = error;
        }
      }
      return next;
    });
    setBusyByProcessId((prev) => {
      const next: Record<string, boolean> = {};
      for (const [processId, busy] of Object.entries(prev)) {
        if (discoveredIds.has(processId)) {
          next[processId] = busy;
        }
      }
      return next;
    });
    setBusyActionByProcessId((prev) => {
      const next: Record<string, string | null> = {};
      for (const [processId, action] of Object.entries(prev)) {
        if (discoveredIds.has(processId)) {
          next[processId] = action ?? null;
        }
      }
      return next;
    });
    setGraphByProcessId((prev) => {
      const next: Record<string, StateMachineGraph | null> = {};
      for (const [processId, graph] of Object.entries(prev)) {
        if (discoveredIds.has(processId)) {
          next[processId] = graph;
        }
      }
      return next;
    });
    setGraphLoadingByProcessId((prev) => {
      const next: Record<string, boolean> = {};
      for (const [processId, loading] of Object.entries(prev)) {
        if (discoveredIds.has(processId)) {
          next[processId] = loading;
        }
      }
      return next;
    });
    setGraphErrorByProcessId((prev) => {
      const next: Record<string, string> = {};
      for (const [processId, error] of Object.entries(prev)) {
        if (discoveredIds.has(processId)) {
          next[processId] = error;
        }
      }
      return next;
    });

    const discoveredIdsOrdered = [...Object.keys(discovered)].sort((a, b) =>
      a.localeCompare(b)
    );
    await Promise.all(
      discoveredIdsOrdered.map((processId) =>
        refreshStateMachineProcess(processId, processById.get(processId))
      )
    );

    const selected = selectedProcessIdRef.current;
    if (!selected || !discoveredIds.has(selected)) {
      const fallback = discoveredIdsOrdered[0] ?? null;
      setSelectedProcessId(fallback);
      selectedProcessIdRef.current = fallback;
    }

    const graphProcessId =
      selectedProcessIdRef.current && discoveredIds.has(selectedProcessIdRef.current)
        ? selectedProcessIdRef.current
        : discoveredIdsOrdered[0] ?? null;
    if (graphProcessId) {
      await refreshStateMachineGraph(graphProcessId);
    }
  }, [refreshStateMachineProcess, refreshStateMachineGraph]);

  const executeStateMachineAction = useCallback(
    async (processId: string, action: string, params: Record<string, unknown>) => {
      const binding = bindingsByProcessIdRef.current[processId];
      if (!binding) {
        return;
      }
      if (busyByProcessIdRef.current[processId]) {
        return;
      }
      const actionName = String(action ?? "").trim();
      if (!actionName) {
        return;
      }
      if (isDangerousAction(actionName)) {
        const confirmed = window.confirm(
          `Confirm ${actionName} for ${processId}?`
        );
        if (!confirmed) {
          return;
        }
      }
      setBusyByProcessId((prev) => ({ ...prev, [processId]: true }));
      setBusyActionByProcessId((prev) => ({ ...prev, [processId]: actionName }));
      try {
        const resp = await callProcessFn(processId, actionName, params, {
          sourceKind: "webui",
          sourceId: "state_machines",
        });
        if (!resp.ok) {
          notifications.show({
            color: "red",
            title: "Action failed",
            message: resp.error?.message ?? resp.error?.code ?? "Unknown error",
          });
          return;
        }
        notifications.show({
          color: "teal",
          title: "Action sent",
          message: `${processId}.${actionName}`,
        });
      } finally {
        await refreshStateMachineProcess(processId);
        await refreshStateMachineGraph(processId);
        setBusyByProcessId((prev) => ({ ...prev, [processId]: false }));
        setBusyActionByProcessId((prev) => ({ ...prev, [processId]: null }));
      }
    },
    [callProcessFn, refreshStateMachineGraph, refreshStateMachineProcess]
  );

  useEffect(() => {
    if (!stateMachinesOpen) {
      return;
    }
    let alive = true;
    const load = async () => {
      if (!alive) {
        return;
      }
      await refreshStateMachinesModalData();
    };
    void load();
    const interval = window.setInterval(() => {
      void load();
    }, 1000);
    return () => {
      alive = false;
      window.clearInterval(interval);
    };
  }, [stateMachinesOpen, refreshStateMachinesModalData]);

  useEffect(() => {
    if (!stateMachinesOpen || !selectedProcessId) {
      return;
    }
    void refreshStateMachineGraph(selectedProcessId);
  }, [stateMachinesOpen, selectedProcessId, refreshStateMachineGraph]);

  const stateMachineRows = useMemo<StateMachineProcessRow[]>(() => {
    const processById = new Map(processes.map((process) => [process.process_id, process]));
    const nowWallS = Date.now() / 1000;
    return Object.entries(bindingsByProcessId)
      .map(([processId, binding]): StateMachineProcessRow | null => {
        const process = processById.get(processId);
        if (!process) {
          return null;
        }
        const status = statusByProcessId[processId] ?? null;
        const fetchedAt = statusFetchedAtByProcessId[processId];
        const statusAgeFromPayload = Number(status?.status_age_s ?? Number.NaN);
        const statusAgeS = Number.isFinite(statusAgeFromPayload)
          ? statusAgeFromPayload
          : Number.isFinite(fetchedAt)
            ? Math.max(0, nowWallS - fetchedAt)
            : null;
        const stale = !isProcessRpcStateAvailable(process)
          ? true
          : statusAgeS == null || statusAgeS > STATUS_STALE_THRESHOLD_S;
        return {
          process,
          binding,
          status,
          statusLoading: Boolean(statusLoadingByProcessId[processId]),
          statusError: statusErrorByProcessId[processId] ?? null,
          statusAgeS,
          stale,
          busy: Boolean(busyByProcessId[processId]),
          busyAction: busyActionByProcessId[processId] ?? null,
          hasActiveOperation: hasActiveOperation(status),
          graph: graphByProcessId[processId] ?? null,
          graphLoading: Boolean(graphLoadingByProcessId[processId]),
          graphError: graphErrorByProcessId[processId] ?? null,
        };
      })
      .filter((row): row is StateMachineProcessRow => row !== null)
      .sort((a, b) => a.process.process_id.localeCompare(b.process.process_id));
  }, [
    bindingsByProcessId,
    processes,
    statusByProcessId,
    statusFetchedAtByProcessId,
    statusLoadingByProcessId,
    statusErrorByProcessId,
    busyByProcessId,
    busyActionByProcessId,
    graphByProcessId,
    graphLoadingByProcessId,
    graphErrorByProcessId,
  ]);

  const stateMachineSummary = useMemo<StateMachinesSummary>(() => {
    let active = 0;
    let error = 0;
    let stale = 0;
    for (const row of stateMachineRows) {
      if (row.hasActiveOperation) {
        active += 1;
      }
      if (statusHasError(row.process, row.status, row.statusError)) {
        error += 1;
      }
      if (row.stale) {
        stale += 1;
      }
    }
    return {
      total: stateMachineRows.length,
      active,
      error,
      stale,
    };
  }, [stateMachineRows]);

  const stateMachineButtonSummary = useMemo(() => {
    const suffix = stateMachineSummary.total > 0 ? ` (${stateMachineSummary.total})` : "";
    const color =
      stateMachineSummary.error > 0
        ? "red"
        : stateMachineSummary.active > 0
          ? "teal"
          : stateMachineSummary.stale > 0
            ? "yellow"
            : "gray";
    const tooltipParts = [
      `${stateMachineSummary.total} total`,
      `${stateMachineSummary.active} active`,
      `${stateMachineSummary.error} error`,
      `${stateMachineSummary.stale} stale`,
    ];
    return {
      color,
      label: `State Machines${suffix}`,
      tooltip: tooltipParts.join(" | "),
    };
  }, [stateMachineSummary]);

  return {
    stateMachinesOpen,
    setStateMachinesOpen,
    selectedProcessId,
    setSelectedProcessId,
    stateMachineRows,
    stateMachineSummary,
    stateMachineButtonSummary,
    refreshStateMachineProcess,
    refreshStateMachineGraph,
    refreshStateMachinesModalData,
    executeStateMachineAction,
  };
}
