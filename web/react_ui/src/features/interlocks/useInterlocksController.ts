import { notifications } from "@mantine/notifications";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchCommandInterceptorRoutes,
  fetchFollowerRules,
  fetchInterlockStatus,
  setFollowerRuleEnabled,
  setInterlockRuleEnabled,
} from "../../api";
import {
  followerRuleNamespace,
  interlockRuleKey,
  isProcessRpcStateAvailable,
  supportsProcessCapability,
} from "../runtime/helpers";
import type {
  CapabilityMember,
  CommandInterceptorRoute,
  FollowerRuleStatus,
  InterlockInterceptorStatus,
  ProcessStatus,
} from "../../types";

type UseInterlocksControllerArgs = {
  processes: ProcessStatus[];
  capabilitiesByProcess: Record<string, CapabilityMember[]>;
  refreshProcesses: () => Promise<ProcessStatus[]>;
  ensureProcessCapabilitiesLoaded: (processId: string) => Promise<CapabilityMember[]>;
};

type RefreshInterlockProcessOptions = {
  showLoading?: boolean;
};

type InterlockButtonSummary = {
  status: "idle" | "active" | "error";
  color: string;
  activeRuleCount: number;
  label: string;
  tooltip: string;
};

export function useInterlocksController({
  processes,
  capabilitiesByProcess,
  refreshProcesses,
  ensureProcessCapabilitiesLoaded,
}: UseInterlocksControllerArgs) {
  const [interlocksOpen, setInterlocksOpen] = useState(false);
  const [followerRulesByProcessId, setFollowerRulesByProcessId] = useState<
    Record<string, FollowerRuleStatus[]>
  >({});
  const [interlockStatusByProcessId, setInterlockStatusByProcessId] = useState<
    Record<string, InterlockInterceptorStatus[]>
  >({});
  const [interlocksLoadingByProcessId, setInterlocksLoadingByProcessId] =
    useState<Record<string, boolean>>({});
  const [interlocksErrorByProcessId, setInterlocksErrorByProcessId] = useState<
    Record<string, string>
  >({});
  const [interlockRuleBusyByKey, setInterlockRuleBusyByKey] = useState<
    Record<string, boolean>
  >({});
  const [commandInterceptorRoutes, setCommandInterceptorRoutes] = useState<
    CommandInterceptorRoute[]
  >([]);
  const [commandInterceptorRoutesLoading, setCommandInterceptorRoutesLoading] =
    useState(false);
  const [commandInterceptorRoutesError, setCommandInterceptorRoutesError] =
    useState<string | null>(null);

  const processesRef = useRef<ProcessStatus[]>(processes);
  const capabilitiesByProcessRef = useRef(capabilitiesByProcess);
  const refreshProcessesRef = useRef(refreshProcesses);
  const ensureProcessCapabilitiesLoadedRef = useRef(
    ensureProcessCapabilitiesLoaded
  );
  const interlockRuleBusyByKeyRef = useRef(interlockRuleBusyByKey);

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
    interlockRuleBusyByKeyRef.current = interlockRuleBusyByKey;
  }, [interlockRuleBusyByKey]);

  const setInterlocksLoading = useCallback((processId: string, loading: boolean) => {
    setInterlocksLoadingByProcessId((prev) => {
      if (prev[processId] === loading) {
        return prev;
      }
      return { ...prev, [processId]: loading };
    });
  }, []);

  const refreshInterlockProcessStatus = useCallback(
    async (
      processId: string,
      processHint?: ProcessStatus,
      opts?: RefreshInterlockProcessOptions
    ) => {
      const showLoading = opts?.showLoading === true;
      const process =
        processHint ?? processesRef.current.find((item) => item.process_id === processId);
      if (!process || !isProcessRpcStateAvailable(process)) {
        setFollowerRulesByProcessId((prev) => {
          if (!(processId in prev)) {
            return prev;
          }
          const next = { ...prev };
          delete next[processId];
          return next;
        });
        setInterlockStatusByProcessId((prev) => {
          if (!(processId in prev)) {
            return prev;
          }
          const next = { ...prev };
          delete next[processId];
          return next;
        });
        setInterlocksErrorByProcessId((prev) => {
          if (!(processId in prev)) {
            return prev;
          }
          const next = { ...prev };
          delete next[processId];
          return next;
        });
        if (showLoading) {
          setInterlocksLoading(processId, false);
        }
        return;
      }

      if (showLoading) {
        setInterlocksLoading(processId, true);
      }
      try {
        const caps = await ensureProcessCapabilitiesLoadedRef.current(processId);
        const followerNamespace = followerRuleNamespace(caps);
        const hasInterlockStatus = supportsProcessCapability(caps, "interlock.status");

        if (followerNamespace !== null) {
          const rules = await fetchFollowerRules(processId, followerNamespace);
          setFollowerRulesByProcessId((prev) => ({ ...prev, [processId]: rules }));
        } else {
          setFollowerRulesByProcessId((prev) => {
            if (!(processId in prev)) {
              return prev;
            }
            const next = { ...prev };
            delete next[processId];
            return next;
          });
        }

        if (hasInterlockStatus) {
          const interceptors = await fetchInterlockStatus(processId);
          setInterlockStatusByProcessId((prev) => ({
            ...prev,
            [processId]: interceptors,
          }));
        } else {
          setInterlockStatusByProcessId((prev) => {
            if (!(processId in prev)) {
              return prev;
            }
            const next = { ...prev };
            delete next[processId];
            return next;
          });
        }

        setInterlocksErrorByProcessId((prev) => {
          if (!(processId in prev)) {
            return prev;
          }
          const next = { ...prev };
          delete next[processId];
          return next;
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setInterlocksErrorByProcessId((prev) => ({ ...prev, [processId]: message }));
      } finally {
        if (showLoading) {
          setInterlocksLoading(processId, false);
        }
      }
    },
    [setInterlocksLoading]
  );

  const refreshInterlocksModalData = useCallback(async () => {
    const nextProcesses = await refreshProcessesRef.current();
    const discovered: string[] = [];
    for (const process of nextProcesses) {
      if (!isProcessRpcStateAvailable(process)) {
        continue;
      }
      const caps = await ensureProcessCapabilitiesLoadedRef.current(process.process_id);
      if (
        followerRuleNamespace(caps) !== null ||
        supportsProcessCapability(caps, "interlock.status")
      ) {
        discovered.push(process.process_id);
      }
    }
    const discoveredSet = new Set(discovered);
    setFollowerRulesByProcessId((prev) => {
      const next: Record<string, FollowerRuleStatus[]> = {};
      for (const [key, value] of Object.entries(prev)) {
        if (discoveredSet.has(key)) {
          next[key] = value;
        }
      }
      return next;
    });
    setInterlockStatusByProcessId((prev) => {
      const next: Record<string, InterlockInterceptorStatus[]> = {};
      for (const [key, value] of Object.entries(prev)) {
        if (discoveredSet.has(key)) {
          next[key] = value;
        }
      }
      return next;
    });
    setInterlocksErrorByProcessId((prev) => {
      const next: Record<string, string> = {};
      for (const [key, value] of Object.entries(prev)) {
        if (discoveredSet.has(key)) {
          next[key] = value;
        }
      }
      return next;
    });
    setInterlocksLoadingByProcessId((prev) => {
      const next: Record<string, boolean> = {};
      for (const [key, value] of Object.entries(prev)) {
        if (discoveredSet.has(key)) {
          next[key] = value;
        }
      }
      return next;
    });

    const processById = new Map(
      nextProcesses.map((process) => [process.process_id, process])
    );
    await Promise.all(
      discovered.map((processId) =>
        refreshInterlockProcessStatus(processId, processById.get(processId))
      )
    );
    setCommandInterceptorRoutesLoading(true);
    try {
      const routes = await fetchCommandInterceptorRoutes();
      setCommandInterceptorRoutes(routes);
      setCommandInterceptorRoutesError(null);
    } catch (error) {
      setCommandInterceptorRoutesError(
        error instanceof Error ? error.message : String(error)
      );
    } finally {
      setCommandInterceptorRoutesLoading(false);
    }
  }, [refreshInterlockProcessStatus]);

  const toggleFollowerRule = useCallback(
    async (processId: string, ruleId: string, enabled: boolean) => {
      const key = interlockRuleKey(processId, "follower", ruleId);
      if (interlockRuleBusyByKeyRef.current[key]) {
        return;
      }
      setInterlockRuleBusyByKey((prev) => ({ ...prev, [key]: true }));
      try {
        const caps =
          capabilitiesByProcessRef.current[processId] ??
          (await ensureProcessCapabilitiesLoadedRef.current(processId));
        const namespace = followerRuleNamespace(caps) ?? "follower";
        const resp = await setFollowerRuleEnabled(
          processId,
          ruleId,
          enabled,
          namespace
        );
        if (!resp.ok) {
          notifications.show({
            color: "red",
            title: "Rule update failed",
            message: resp.error?.message ?? resp.error?.code ?? "Unknown error",
          });
          return;
        }
        notifications.show({
          color: "teal",
          title: enabled ? "Rule enabled" : "Rule disabled",
          message: `${processId}:${ruleId}`,
        });
        await refreshInterlockProcessStatus(processId);
      } finally {
        setInterlockRuleBusyByKey((prev) => ({ ...prev, [key]: false }));
      }
    },
    [refreshInterlockProcessStatus]
  );

  const toggleInterlockRule = useCallback(
    async (
      processId: string,
      interceptorId: string,
      ruleId: string,
      enabled: boolean
    ) => {
      const key = interlockRuleKey(processId, interceptorId, ruleId);
      if (interlockRuleBusyByKeyRef.current[key]) {
        return;
      }
      setInterlockRuleBusyByKey((prev) => ({ ...prev, [key]: true }));
      try {
        const resp = await setInterlockRuleEnabled(
          processId,
          interceptorId,
          ruleId,
          enabled
        );
        if (!resp.ok) {
          notifications.show({
            color: "red",
            title: "Rule update failed",
            message: resp.error?.message ?? resp.error?.code ?? "Unknown error",
          });
          return;
        }
        notifications.show({
          color: "teal",
          title: enabled ? "Rule enabled" : "Rule disabled",
          message: `${processId}:${interceptorId}:${ruleId}`,
        });
        await refreshInterlockProcessStatus(processId);
      } finally {
        setInterlockRuleBusyByKey((prev) => ({ ...prev, [key]: false }));
      }
    },
    [refreshInterlockProcessStatus]
  );

  useEffect(() => {
    if (!interlocksOpen) {
      return;
    }
    let alive = true;
    const load = async () => {
      if (!alive) {
        return;
      }
      await refreshInterlocksModalData();
    };
    void load();
    const interval = setInterval(() => {
      void load();
    }, 5000);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [interlocksOpen, refreshInterlocksModalData]);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      if (!alive) {
        return;
      }
      const nextProcesses = await refreshProcessesRef.current();
      if (!alive) {
        return;
      }
      for (const process of nextProcesses) {
        if (!isProcessRpcStateAvailable(process)) {
          continue;
        }
        const processId = process.process_id;
        const caps = capabilitiesByProcessRef.current[processId] ?? [];
        let effectiveCaps = caps;
        if (effectiveCaps.length === 0) {
          effectiveCaps = await ensureProcessCapabilitiesLoadedRef.current(processId);
          if (!alive) {
            return;
          }
        }
        if (
          followerRuleNamespace(effectiveCaps) !== null ||
          supportsProcessCapability(effectiveCaps, "interlock.status")
        ) {
          await refreshInterlockProcessStatus(processId, process);
        }
      }
    };
    void load();
    const interval = setInterval(() => {
      void load();
    }, 10000);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [refreshInterlockProcessStatus]);

  const interlocksPanelProcesses = useMemo(() => {
    const byId = new Map<string, ProcessStatus>();
    for (const process of processes) {
      const caps = capabilitiesByProcess[process.process_id] ?? [];
      if (
        followerRuleNamespace(caps) !== null ||
        supportsProcessCapability(caps, "interlock.status")
      ) {
        byId.set(process.process_id, process);
      }
    }
    return [...byId.values()].sort((a, b) => a.process_id.localeCompare(b.process_id));
  }, [processes, capabilitiesByProcess]);

  const interlockButtonSummary = useMemo<InterlockButtonSummary>(() => {
    const processMap = new Map(processes.map((process) => [process.process_id, process]));
    const trackedProcessIds = new Set<string>([
      ...Object.keys(followerRulesByProcessId),
      ...Object.keys(interlockStatusByProcessId),
      ...interlocksPanelProcesses.map((process) => process.process_id),
    ]);

    let activeRuleCount = 0;
    let hasError = false;
    const errorSources: string[] = [];

    for (const processId of trackedProcessIds) {
      const process = processMap.get(processId);
      const processState = String(process?.state ?? "").toUpperCase();
      if (processState === "FAILED" || processState === "CRASHLOOP") {
        hasError = true;
        errorSources.push(`${processId}: process ${processState}`);
      }
      const processRpcActive =
        process !== undefined ? isProcessRpcStateAvailable(process) : false;
      const errorMessage = interlocksErrorByProcessId[processId];
      if (errorMessage) {
        hasError = true;
        errorSources.push(`${processId}: ${errorMessage}`);
      }

      const followerRules = followerRulesByProcessId[processId] ?? [];
      for (const rule of followerRules) {
        if (processRpcActive && rule.enabled) {
          activeRuleCount += 1;
        }
      }

      const interceptors = interlockStatusByProcessId[processId] ?? [];
      for (const interceptor of interceptors) {
        if (!interceptor.enabled) {
          continue;
        }
        const rules = Array.isArray(interceptor.rules) ? interceptor.rules : [];
        for (const rule of rules) {
          if (processRpcActive && rule.enabled) {
            activeRuleCount += 1;
          }
        }
      }
    }

    const status =
      hasError ? "error" : activeRuleCount > 0 ? "active" : "idle";
    const color = status === "error" ? "red" : status === "active" ? "teal" : "gray";
    const labelSuffix = activeRuleCount > 0 ? ` (${activeRuleCount})` : "";
    const tooltip = hasError
      ? `Interlock issue: ${errorSources[0] ?? "unknown"}`
      : activeRuleCount > 0
      ? `${activeRuleCount} active rule${activeRuleCount === 1 ? "" : "s"}`
      : "No active interlock rules";

    return {
      status,
      color,
      activeRuleCount,
      label: `Interlocks${labelSuffix}`,
      tooltip,
    };
  }, [
    processes,
    interlocksPanelProcesses,
    followerRulesByProcessId,
    interlockStatusByProcessId,
    interlocksErrorByProcessId,
  ]);

  return {
    interlocksOpen,
    setInterlocksOpen,
    followerRulesByProcessId,
    interlockStatusByProcessId,
    interlocksLoadingByProcessId,
    interlocksErrorByProcessId,
    interlockRuleBusyByKey,
    commandInterceptorRoutes,
    commandInterceptorRoutesLoading,
    commandInterceptorRoutesError,
    interlocksPanelProcesses,
    interlockButtonSummary,
    refreshInterlockProcessStatus,
    refreshInterlocksModalData,
    toggleFollowerRule,
    toggleInterlockRule,
  };
}
