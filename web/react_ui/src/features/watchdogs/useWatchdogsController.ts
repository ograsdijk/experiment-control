import { notifications } from "@mantine/notifications";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  clearWatchdogLatch,
  fetchWatchdogStatus,
  setWatchdogEnabled,
} from "../../api";
import {
  isProcessRpcStateAvailable,
  supportsProcessCapability,
} from "../runtime/helpers";
import type {
  CapabilityMember,
  ProcessStatus,
  WatchdogStatus,
} from "../../types";

type UseWatchdogsControllerArgs = {
  safetyOpen: boolean;
  processes: ProcessStatus[];
  capabilitiesByProcess: Record<string, CapabilityMember[]>;
  refreshProcesses: () => Promise<ProcessStatus[]>;
  ensureProcessCapabilitiesLoaded: (processId: string) => Promise<CapabilityMember[]>;
};

type RefreshWatchdogProcessOptions = {
  showLoading?: boolean;
};

type WatchdogButtonSummary = {
  status: "idle" | "active" | "error";
  color: string;
  activeLatchCount: number;
  activeAlarmCount: number;
  unknownRuleCount: number;
  pendingRuleCount: number;
  label: string;
  tooltip: string;
};

export function useWatchdogsController({
  safetyOpen,
  processes,
  capabilitiesByProcess,
  refreshProcesses,
  ensureProcessCapabilitiesLoaded,
}: UseWatchdogsControllerArgs) {
  const [watchdogStatusByProcessId, setWatchdogStatusByProcessId] = useState<
    Record<string, WatchdogStatus[]>
  >({});
  const [watchdogLoadingByProcessId, setWatchdogLoadingByProcessId] =
    useState<Record<string, boolean>>({});
  const [watchdogErrorByProcessId, setWatchdogErrorByProcessId] = useState<
    Record<string, string>
  >({});
  const [watchdogBusyByKey, setWatchdogBusyByKey] = useState<
    Record<string, boolean>
  >({});

  const processesRef = useRef<ProcessStatus[]>(processes);
  const capabilitiesByProcessRef = useRef(capabilitiesByProcess);
  const refreshProcessesRef = useRef(refreshProcesses);
  const ensureProcessCapabilitiesLoadedRef = useRef(
    ensureProcessCapabilitiesLoaded
  );
  const watchdogBusyByKeyRef = useRef(watchdogBusyByKey);

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
    watchdogBusyByKeyRef.current = watchdogBusyByKey;
  }, [watchdogBusyByKey]);

  const setWatchdogLoading = useCallback((processId: string, loading: boolean) => {
    setWatchdogLoadingByProcessId((prev) => {
      if (prev[processId] === loading) {
        return prev;
      }
      return { ...prev, [processId]: loading };
    });
  }, []);

  const refreshWatchdogProcessStatus = useCallback(
    async (
      processId: string,
      processHint?: ProcessStatus,
      opts?: RefreshWatchdogProcessOptions
    ) => {
      const showLoading = opts?.showLoading === true;
      const process =
        processHint ?? processesRef.current.find((item) => item.process_id === processId);
      if (!process || !isProcessRpcStateAvailable(process)) {
        setWatchdogStatusByProcessId((prev) => {
          if (!(processId in prev)) {
            return prev;
          }
          const next = { ...prev };
          delete next[processId];
          return next;
        });
        setWatchdogErrorByProcessId((prev) => {
          if (!(processId in prev)) {
            return prev;
          }
          const next = { ...prev };
          delete next[processId];
          return next;
        });
        if (showLoading) {
          setWatchdogLoading(processId, false);
        }
        return;
      }

      if (showLoading) {
        setWatchdogLoading(processId, true);
      }
      try {
        const caps = await ensureProcessCapabilitiesLoadedRef.current(processId);
        if (!supportsProcessCapability(caps, "watchdog.status")) {
          setWatchdogStatusByProcessId((prev) => {
            if (!(processId in prev)) {
              return prev;
            }
            const next = { ...prev };
            delete next[processId];
            return next;
          });
          setWatchdogErrorByProcessId((prev) => {
            if (!(processId in prev)) {
              return prev;
            }
            const next = { ...prev };
            delete next[processId];
            return next;
          });
          return;
        }
        const watchdogs = await fetchWatchdogStatus(processId);
        setWatchdogStatusByProcessId((prev) => ({
          ...prev,
          [processId]: watchdogs,
        }));
        setWatchdogErrorByProcessId((prev) => {
          if (!(processId in prev)) {
            return prev;
          }
          const next = { ...prev };
          delete next[processId];
          return next;
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setWatchdogErrorByProcessId((prev) => ({ ...prev, [processId]: message }));
      } finally {
        if (showLoading) {
          setWatchdogLoading(processId, false);
        }
      }
    },
    [setWatchdogLoading]
  );

  const refreshWatchdogsModalData = useCallback(async () => {
    const nextProcesses = await refreshProcessesRef.current();
    const discovered: string[] = [];
    for (const process of nextProcesses) {
      if (!isProcessRpcStateAvailable(process)) {
        continue;
      }
      const caps = await ensureProcessCapabilitiesLoadedRef.current(process.process_id);
      if (supportsProcessCapability(caps, "watchdog.status")) {
        discovered.push(process.process_id);
      }
    }
    const discoveredSet = new Set(discovered);
    setWatchdogStatusByProcessId((prev) => {
      const next: Record<string, WatchdogStatus[]> = {};
      for (const [key, value] of Object.entries(prev)) {
        if (discoveredSet.has(key)) {
          next[key] = value;
        }
      }
      return next;
    });
    setWatchdogErrorByProcessId((prev) => {
      const next: Record<string, string> = {};
      for (const [key, value] of Object.entries(prev)) {
        if (discoveredSet.has(key)) {
          next[key] = value;
        }
      }
      return next;
    });
    setWatchdogLoadingByProcessId((prev) => {
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
        refreshWatchdogProcessStatus(processId, processById.get(processId))
      )
    );
  }, [refreshWatchdogProcessStatus]);

  const toggleWatchdog = useCallback(
    async (processId: string, watchdogId: string, enabled: boolean) => {
      const key = `${processId}:${watchdogId}:toggle`;
      if (watchdogBusyByKeyRef.current[key]) {
        return;
      }
      setWatchdogBusyByKey((prev) => ({ ...prev, [key]: true }));
      try {
        const resp = await setWatchdogEnabled(processId, watchdogId, enabled);
        if (!resp.ok) {
          notifications.show({
            color: "red",
            title: "Watchdog update failed",
            message: resp.error?.message ?? resp.error?.code ?? "Unknown error",
          });
          return;
        }
        notifications.show({
          color: "teal",
          title: enabled ? "Watchdog enabled" : "Watchdog disabled",
          message: `${processId}:${watchdogId}`,
        });
        await refreshWatchdogProcessStatus(processId);
      } finally {
        setWatchdogBusyByKey((prev) => ({ ...prev, [key]: false }));
      }
    },
    [refreshWatchdogProcessStatus]
  );

  const clearWatchdogRuleLatch = useCallback(
    async (processId: string, watchdogId: string, ruleName: string) => {
      const key = `${processId}:${watchdogId}:${ruleName}:clear`;
      if (watchdogBusyByKeyRef.current[key]) {
        return;
      }
      setWatchdogBusyByKey((prev) => ({ ...prev, [key]: true }));
      try {
        const resp = await clearWatchdogLatch(processId, watchdogId, ruleName);
        if (!resp.ok) {
          notifications.show({
            color: "red",
            title: "Clear latch failed",
            message: resp.error?.message ?? resp.error?.code ?? "Unknown error",
          });
          return;
        }
        notifications.show({
          color: "teal",
          title: "Latch cleared",
          message: `${processId}:${watchdogId}:${ruleName}`,
        });
        await refreshWatchdogProcessStatus(processId);
      } finally {
        setWatchdogBusyByKey((prev) => ({ ...prev, [key]: false }));
      }
    },
    [refreshWatchdogProcessStatus]
  );

  useEffect(() => {
    if (!safetyOpen) {
      return;
    }
    let alive = true;
    const load = async () => {
      if (!alive) {
        return;
      }
      await refreshWatchdogsModalData();
    };
    void load();
    const interval = setInterval(() => {
      void load();
    }, 5000);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [safetyOpen, refreshWatchdogsModalData]);

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
        if (supportsProcessCapability(effectiveCaps, "watchdog.status")) {
          await refreshWatchdogProcessStatus(processId, process);
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
  }, [refreshWatchdogProcessStatus]);

  const watchdogsPanelProcesses = useMemo(() => {
    const byId = new Map<string, ProcessStatus>();
    for (const process of processes) {
      const caps = capabilitiesByProcess[process.process_id] ?? [];
      if (supportsProcessCapability(caps, "watchdog.status")) {
        byId.set(process.process_id, process);
      }
    }
    return [...byId.values()].sort((a, b) => a.process_id.localeCompare(b.process_id));
  }, [processes, capabilitiesByProcess]);

  const watchdogButtonSummary = useMemo<WatchdogButtonSummary>(() => {
    const processMap = new Map(processes.map((process) => [process.process_id, process]));
    const trackedProcessIds = new Set<string>([
      ...Object.keys(watchdogStatusByProcessId),
      ...watchdogsPanelProcesses.map((process) => process.process_id),
    ]);

    let activeLatchCount = 0;
    let activeAlarmCount = 0;
    let unknownRuleCount = 0;
    let pendingRuleCount = 0;
    let hasError = false;
    let hasTrackedWatchdogs = false;
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
      const errorMessage = watchdogErrorByProcessId[processId];
      if (errorMessage) {
        hasError = true;
        errorSources.push(`${processId}: ${errorMessage}`);
      }
      const watchdogs = watchdogStatusByProcessId[processId] ?? [];
      for (const watchdog of watchdogs) {
        hasTrackedWatchdogs = true;
        if (!processRpcActive || !watchdog.enabled) {
          continue;
        }
        for (const rule of watchdog.rules ?? []) {
          if (rule.latched) {
            activeLatchCount += 1;
          } else if (rule.alarm) {
            activeAlarmCount += 1;
          } else if (rule.unknown) {
            unknownRuleCount += 1;
          } else if (rule.last_evaluated_mono == null) {
            pendingRuleCount += 1;
          }
        }
      }
    }

    const activeRuleCount =
      activeLatchCount + activeAlarmCount + unknownRuleCount + pendingRuleCount;
    const status = hasError ? "error" : activeRuleCount > 0 ? "active" : "idle";
    const color = status === "error"
      ? "red"
      : status === "active"
        ? "orange"
        : hasTrackedWatchdogs
          ? "teal"
          : "gray";
    const labelSuffix = activeRuleCount > 0 ? ` (${activeRuleCount})` : "";
    const tooltip = hasError
      ? `Watchdog issue: ${errorSources[0] ?? "unknown"}`
      : activeRuleCount > 0
      ? [
          activeLatchCount > 0
            ? `${activeLatchCount} latched rule${activeLatchCount === 1 ? "" : "s"}`
            : null,
          activeAlarmCount > 0
            ? `${activeAlarmCount} alarm rule${activeAlarmCount === 1 ? "" : "s"}`
            : null,
          unknownRuleCount > 0
            ? `${unknownRuleCount} unknown rule${unknownRuleCount === 1 ? "" : "s"}`
            : null,
          pendingRuleCount > 0
            ? `${pendingRuleCount} pending rule${pendingRuleCount === 1 ? "" : "s"}`
            : null,
        ]
          .filter((part): part is string => Boolean(part))
          .join(" | ")
      : hasTrackedWatchdogs
        ? "All watchdog rules clear"
        : "No watchdog status available";
    return {
      status,
      color,
      activeLatchCount,
      activeAlarmCount,
      unknownRuleCount,
      pendingRuleCount,
      label: `Watchdogs${labelSuffix}`,
      tooltip,
    };
  }, [processes, watchdogStatusByProcessId, watchdogsPanelProcesses, watchdogErrorByProcessId]);

  return {
    watchdogStatusByProcessId,
    watchdogLoadingByProcessId,
    watchdogErrorByProcessId,
    watchdogBusyByKey,
    watchdogsPanelProcesses,
    watchdogButtonSummary,
    refreshWatchdogProcessStatus,
    refreshWatchdogsModalData,
    toggleWatchdog,
    clearWatchdogRuleLatch,
  };
}
