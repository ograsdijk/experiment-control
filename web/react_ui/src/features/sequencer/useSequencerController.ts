import { notifications } from "@mantine/notifications";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from "react";
import type { ApiResponse } from "../../api";
import type { ProcessStatus } from "../../types";
import type { SequencerDiagnostic, SequencerStatus } from "./types";
import {
  formatDurationCompact,
  normalizeSequencerDiagnostics,
  normalizeSequencerProgress,
  sameSequencerStatus,
} from "./utils";

type SequencerAction = "start" | "pause" | "resume" | "stop";

type UseSequencerControllerArgs = {
  sequencerProcess: ProcessStatus | null;
  callProcessFn: (
    processId: string,
    action: string,
    params: Record<string, unknown>
  ) => Promise<ApiResponse<unknown>>;
  sendProcessCommand: (
    processId: string,
    action: string,
    params: Record<string, unknown>,
    source: string
  ) => Promise<ApiResponse<unknown>>;
  refreshProcesses: () => Promise<ProcessStatus[]>;
};

export function useSequencerController({
  sequencerProcess,
  callProcessFn,
  sendProcessCommand,
  refreshProcesses,
}: UseSequencerControllerArgs) {
  const [sequencerOpen, setSequencerOpen] = useState(false);
  const [sequencerStatusByProcessId, setSequencerStatusByProcessId] = useState<
    Record<string, SequencerStatus>
  >({});
  const [sequencerStatusLoadingByProcessId, setSequencerStatusLoadingByProcessId] =
    useState<Record<string, boolean>>({});
  const [sequencerActionBusy, setSequencerActionBusy] = useState(false);
  const [sequencerValidateBusy, setSequencerValidateBusy] = useState(false);
  const [sequencerLoadBusy, setSequencerLoadBusy] = useState(false);
  const [sequencerLoadedYamlBusy, setSequencerLoadedYamlBusy] = useState(false);
  const [sequencerYamlText, setSequencerYamlText] = useState("");
  const [sequencerYamlViewMode, setSequencerYamlViewMode] = useState<
    "edit" | "preview"
  >("preview");
  const [sequencerDiagnostics, setSequencerDiagnostics] = useState<
    SequencerDiagnostic[]
  >([]);
  const [sequencerModalError, setSequencerModalError] = useState<string | null>(
    null
  );
  const sequencerEditorRef = useRef<HTMLTextAreaElement | null>(null);
  const sequencerFileInputRef = useRef<HTMLInputElement | null>(null);
  const sequencerStatusByProcessIdRef = useRef<Record<string, SequencerStatus>>(
    {}
  );

  useEffect(() => {
    sequencerStatusByProcessIdRef.current = sequencerStatusByProcessId;
  }, [sequencerStatusByProcessId]);

  const setSequencerStatusLoading = useCallback(
    (processId: string, loading: boolean) => {
      setSequencerStatusLoadingByProcessId((prev) => {
        if (prev[processId] === loading) {
          return prev;
        }
        return { ...prev, [processId]: loading };
      });
    },
    []
  );

  const refreshSequencerStatus = useCallback(
    async (processId: string) => {
      const hasExistingStatus = Boolean(
        sequencerStatusByProcessIdRef.current[processId]
      );
      if (!hasExistingStatus) {
        setSequencerStatusLoading(processId, true);
      }
      try {
        const resp = await callProcessFn(processId, "sequencer.status", {});
        if (!resp.ok || !resp.result || typeof resp.result !== "object") {
          const code = resp.error?.code ?? null;
          const message = resp.error?.message ?? null;
          setSequencerStatusByProcessId((prev) => {
            const current = prev[processId];
            const nextStatus: SequencerStatus = {
              state: current?.state ?? null,
              currentStep: current?.currentStep ?? null,
              error: message ?? code ?? "sequencer.status failed",
              loaded: current?.loaded ?? null,
              contextColumns: current?.contextColumns ?? null,
              loadedSource: current?.loadedSource ?? null,
              autoloadError: current?.autoloadError ?? null,
              progress: current?.progress ?? null,
            };
            if (sameSequencerStatus(current, nextStatus)) {
              return prev;
            }
            return { ...prev, [processId]: nextStatus };
          });
          return;
        }
        const result = resp.result as {
          state?: unknown;
          current_step?: unknown;
          error?: unknown;
          loaded?: unknown;
          context_columns?: unknown;
          loaded_source?: unknown;
          autoload_error?: unknown;
          progress?: unknown;
        };
        const contextColumns =
          result.context_columns && typeof result.context_columns === "object"
            ? Object.fromEntries(
                Object.entries(result.context_columns as Record<string, unknown>)
                  .filter(
                    ([key, value]) => typeof key === "string" && typeof value === "string"
                  )
                  .map(([key, value]) => [key, String(value)])
              )
            : null;
        const loadedSource =
          typeof result.loaded_source === "string" && result.loaded_source.trim()
            ? result.loaded_source
            : null;
        const autoloadError =
          typeof result.autoload_error === "string" && result.autoload_error.trim()
            ? result.autoload_error
            : null;
        const progress = normalizeSequencerProgress(result.progress);
        setSequencerStatusByProcessId((prev) => {
          const current = prev[processId];
          const nextStatus: SequencerStatus = {
            state: typeof result.state === "string" ? result.state : null,
            currentStep:
              typeof result.current_step === "string" ? result.current_step : null,
            error: typeof result.error === "string" ? result.error : null,
            loaded:
              typeof result.loaded === "boolean"
                ? result.loaded
                : current?.loaded ?? null,
            contextColumns,
            loadedSource,
            autoloadError,
            progress,
          };
          if (sameSequencerStatus(current, nextStatus)) {
            return prev;
          }
          return { ...prev, [processId]: nextStatus };
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setSequencerStatusByProcessId((prev) => {
          const current = prev[processId];
          const nextStatus: SequencerStatus = {
            state: current?.state ?? null,
            currentStep: current?.currentStep ?? null,
            error: message,
            loaded: current?.loaded ?? null,
            contextColumns: current?.contextColumns ?? null,
            loadedSource: current?.loadedSource ?? null,
            autoloadError: current?.autoloadError ?? null,
            progress: current?.progress ?? null,
          };
          if (sameSequencerStatus(current, nextStatus)) {
            return prev;
          }
          return { ...prev, [processId]: nextStatus };
        });
      } finally {
        if (!hasExistingStatus) {
          setSequencerStatusLoading(processId, false);
        }
      }
    },
    [callProcessFn, setSequencerStatusLoading]
  );

  const fetchSequencerLoadedYaml = useCallback(
    async (
      processId: string,
      opts?: { applyToEditor?: boolean; silent?: boolean }
    ) => {
      const applyToEditor = opts?.applyToEditor === true;
      const silent = opts?.silent === true;
      if (sequencerLoadedYamlBusy) {
        return;
      }
      setSequencerLoadedYamlBusy(true);
      try {
        const resp = await callProcessFn(processId, "sequencer.loaded_yaml", {});
        if (!resp.ok || !resp.result || typeof resp.result !== "object") {
          const message = resp.error?.message ?? resp.error?.code ?? "Unknown error";
          setSequencerModalError(message);
          if (!silent) {
            notifications.show({
              color: "red",
              title: "Failed to fetch loaded sequence",
              message,
            });
          }
          return;
        }
        const result = resp.result as {
          loaded?: unknown;
          source?: unknown;
          text?: unknown;
        };
        const loaded = result.loaded === true;
        const source =
          typeof result.source === "string" && result.source.trim()
            ? result.source
            : null;
        const text = typeof result.text === "string" ? result.text : null;
        setSequencerStatusByProcessId((prev) => {
          const current = prev[processId];
          if (!current) {
            return prev;
          }
          const next: SequencerStatus = {
            ...current,
            loadedSource: source,
          };
          if (sameSequencerStatus(current, next)) {
            return prev;
          }
          return { ...prev, [processId]: next };
        });
        if (applyToEditor && loaded && text !== null) {
          setSequencerYamlText(text.replace(/\r\n/g, "\n"));
        }
        if (!loaded) {
          setSequencerModalError("No sequence is currently loaded in the sequencer.");
        } else if (text === null) {
          setSequencerModalError(
            "Loaded sequence text is unavailable from sequencer.loaded_yaml."
          );
        } else {
          setSequencerModalError(null);
        }
        if (source && !silent) {
          notifications.show({
            color: "teal",
            title: "Loaded sequence fetched",
            message: source,
          });
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setSequencerModalError(message);
        if (!silent) {
          notifications.show({
            color: "red",
            title: "Failed to fetch loaded sequence",
            message,
          });
        }
      } finally {
        setSequencerLoadedYamlBusy(false);
      }
    },
    [callProcessFn, sequencerLoadedYamlBusy]
  );

  const openSequencerModal = useCallback(async () => {
    if (!sequencerProcess) {
      return;
    }
    setSequencerOpen(true);
    setSequencerYamlViewMode("preview");
    setSequencerModalError(null);
    await refreshSequencerStatus(sequencerProcess.process_id);
    await fetchSequencerLoadedYaml(sequencerProcess.process_id, {
      applyToEditor: true,
      silent: true,
    });
  }, [fetchSequencerLoadedYaml, refreshSequencerStatus, sequencerProcess]);

  const runSequencerAction = useCallback(
    async (action: SequencerAction) => {
      if (!sequencerProcess || sequencerActionBusy) {
        return;
      }
      const processId = sequencerProcess.process_id;
      setSequencerActionBusy(true);
      setSequencerModalError(null);
      try {
        const resp = await sendProcessCommand(
          processId,
          `sequencer.${action}`,
          {},
          "sequencer-action"
        );
        if (!resp.ok) {
          const message = resp.error?.message ?? resp.error?.code ?? "Unknown error";
          setSequencerModalError(message);
          notifications.show({
            color: "red",
            title: `Sequencer ${action} failed`,
            message,
          });
          return;
        }
        notifications.show({
          color: "teal",
          title: `Sequencer ${action} requested`,
          message: processId,
        });
        await refreshSequencerStatus(processId);
        await refreshProcesses();
      } finally {
        setSequencerActionBusy(false);
      }
    },
    [
      refreshProcesses,
      refreshSequencerStatus,
      sendProcessCommand,
      sequencerActionBusy,
      sequencerProcess,
    ]
  );

  const lineColumnToOffset = useCallback(
    (text: string, line: number, column: number | null): number => {
      const lines = text.split("\n");
      const safeLine = Math.max(1, Math.min(line, lines.length || 1));
      let offset = 0;
      for (let idx = 0; idx < safeLine - 1; idx += 1) {
        offset += lines[idx].length + 1;
      }
      const target = lines[safeLine - 1] ?? "";
      const safeColumn = Math.max(1, column ?? 1);
      offset += Math.min(target.length, safeColumn - 1);
      return offset;
    },
    []
  );

  const jumpToSequencerDiagnostic = useCallback(
    (line: number | null, column: number | null) => {
      if (line == null) {
        return;
      }
      const focusEditorAtLine = () => {
        if (!sequencerEditorRef.current) {
          return;
        }
        const offset = lineColumnToOffset(sequencerYamlText, line, column);
        sequencerEditorRef.current.focus();
        sequencerEditorRef.current.setSelectionRange(offset, offset);
      };
      if (sequencerYamlViewMode !== "edit") {
        setSequencerYamlViewMode("edit");
        window.setTimeout(focusEditorAtLine, 0);
        return;
      }
      focusEditorAtLine();
    },
    [lineColumnToOffset, sequencerYamlText, sequencerYamlViewMode]
  );

  const handleSequencerFileInput = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const file = event.currentTarget.files?.[0];
      if (!file) {
        return;
      }
      try {
        const text = (await file.text()).replace(/\r\n/g, "\n");
        setSequencerYamlText(text);
        setSequencerDiagnostics([]);
        setSequencerModalError(null);
        notifications.show({
          color: "teal",
          title: "Sequence loaded in editor",
          message: file.name,
        });
      } catch (error) {
        notifications.show({
          color: "red",
          title: "Failed to read sequence file",
          message: error instanceof Error ? error.message : String(error),
        });
      } finally {
        event.currentTarget.value = "";
      }
    },
    []
  );

  const validateSequencerYaml = useCallback(async () => {
    if (!sequencerProcess || sequencerValidateBusy) {
      return;
    }
    setSequencerValidateBusy(true);
    setSequencerModalError(null);
    try {
      const resp = await sendProcessCommand(
        sequencerProcess.process_id,
        "sequencer.validate",
        {
          text: sequencerYamlText,
        },
        "sequencer-validate"
      );
      if (!resp.ok) {
        const message = resp.error?.message ?? resp.error?.code ?? "Unknown error";
        setSequencerModalError(message);
        setSequencerDiagnostics([]);
        notifications.show({
          color: "red",
          title: "Validation failed",
          message,
        });
        return;
      }
      const result =
        resp.result && typeof resp.result === "object"
          ? (resp.result as { valid?: unknown; diagnostics?: unknown })
          : null;
      const diagnostics = normalizeSequencerDiagnostics(result?.diagnostics);
      setSequencerDiagnostics(diagnostics);
      const valid = result?.valid === true;
      notifications.show({
        color: valid ? "teal" : "yellow",
        title: valid ? "Sequence is valid" : "Sequence has issues",
        message: valid
          ? "No validation errors."
          : `${diagnostics.length} diagnostic${diagnostics.length === 1 ? "" : "s"}`,
      });
    } finally {
      setSequencerValidateBusy(false);
    }
  }, [
    sendProcessCommand,
    sequencerProcess,
    sequencerValidateBusy,
    sequencerYamlText,
  ]);

  const loadSequencerYaml = useCallback(async () => {
    if (!sequencerProcess || sequencerLoadBusy) {
      return;
    }
    setSequencerLoadBusy(true);
    setSequencerModalError(null);
    try {
      const resp = await sendProcessCommand(
        sequencerProcess.process_id,
        "sequencer.load",
        {
          text: sequencerYamlText,
        },
        "sequencer-load"
      );
      if (!resp.ok) {
        const message = resp.error?.message ?? resp.error?.code ?? "Unknown error";
        setSequencerModalError(message);
        const diagnostics = normalizeSequencerDiagnostics(
          (resp.error as { diagnostics?: unknown } | undefined)?.diagnostics
        );
        setSequencerDiagnostics(diagnostics);
        notifications.show({
          color: "red",
          title: "Load failed",
          message,
        });
        return;
      }
      setSequencerDiagnostics([]);
      notifications.show({
        color: "teal",
        title: "Sequence loaded",
        message: sequencerProcess.process_id,
      });
      await refreshSequencerStatus(sequencerProcess.process_id);
      await fetchSequencerLoadedYaml(sequencerProcess.process_id, {
        applyToEditor: true,
        silent: true,
      });
      await refreshProcesses();
    } finally {
      setSequencerLoadBusy(false);
    }
  }, [
    fetchSequencerLoadedYaml,
    refreshProcesses,
    refreshSequencerStatus,
    sendProcessCommand,
    sequencerLoadBusy,
    sequencerProcess,
    sequencerYamlText,
  ]);

  const onSequencerYamlTextChange = useCallback((value: string) => {
    setSequencerYamlText(value);
    setSequencerModalError(null);
  }, []);

  useEffect(() => {
    if (!sequencerProcess) {
      return;
    }
    const processId = sequencerProcess.process_id;
    const state = String(sequencerProcess.state ?? "").toUpperCase();
    if (!["RUNNING", "STARTING", "STOPPING"].includes(state)) {
      return;
    }
    let alive = true;
    const load = async () => {
      if (!alive) {
        return;
      }
      await refreshSequencerStatus(processId);
    };
    void load();
    const interval = setInterval(() => {
      void load();
    }, 1500);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [refreshSequencerStatus, sequencerProcess]);

  useEffect(() => {
    if (!sequencerProcess && sequencerOpen) {
      setSequencerOpen(false);
    }
  }, [sequencerOpen, sequencerProcess]);

  const sequencerStatus = sequencerProcess
    ? sequencerStatusByProcessId[sequencerProcess.process_id]
    : undefined;
  const sequencerStatusLoading = sequencerProcess
    ? Boolean(sequencerStatusLoadingByProcessId[sequencerProcess.process_id])
    : false;
  const sequencerProcessState = String(
    sequencerProcess?.state ?? "UNKNOWN"
  ).toUpperCase();
  const sequencerRuntimeState = String(sequencerStatus?.state ?? "UNKNOWN").toUpperCase();
  const sequencerLoaded = sequencerStatus?.loaded === true;
  const sequencerProgress = sequencerStatus?.progress ?? null;
  const sequencerProgressPercent =
    typeof sequencerProgress?.percent === "number" &&
    Number.isFinite(sequencerProgress.percent)
      ? Math.max(0, Math.min(100, sequencerProgress.percent))
      : null;
  const sequencerCompletedSteps =
    typeof sequencerProgress?.completedSteps === "number" &&
    Number.isFinite(sequencerProgress.completedSteps)
      ? Math.max(0, Math.trunc(sequencerProgress.completedSteps))
      : null;
  const sequencerTotalSteps =
    typeof sequencerProgress?.totalSteps === "number" &&
    Number.isFinite(sequencerProgress.totalSteps)
      ? Math.max(0, Math.trunc(sequencerProgress.totalSteps))
      : null;
  const sequencerChipSuffix = useMemo(() => {
    if (!sequencerProgress) {
      return "";
    }
    const elapsed = formatDurationCompact(sequencerProgress.elapsedS);
    const eta = formatDurationCompact(sequencerProgress.etaS);
    if (sequencerProgressPercent !== null) {
      return ` | ${sequencerProgressPercent.toFixed(1)}% | ${elapsed}${
        eta !== "n/a" ? ` | ETA ${eta}` : ""
      }`;
    }
    if (elapsed !== "n/a") {
      return ` | ${elapsed}`;
    }
    return "";
  }, [sequencerProgress, sequencerProgressPercent]);
  const sequencerChipTooltip = useMemo(() => {
    if (!sequencerProgress) {
      return "Open sequencer controls";
    }
    const elapsed = formatDurationCompact(sequencerProgress.elapsedS);
    const eta = formatDurationCompact(sequencerProgress.etaS);
    const steps =
      sequencerTotalSteps !== null
        ? `${sequencerCompletedSteps ?? 0}/${sequencerTotalSteps}`
        : `${sequencerCompletedSteps ?? 0}`;
    if (sequencerProgressPercent !== null) {
      return `Progress ${sequencerProgressPercent.toFixed(1)}%, steps ${steps}, elapsed ${elapsed}${
        eta !== "n/a" ? `, ETA ${eta}` : ""
      }`;
    }
    return `Steps ${steps}, elapsed ${elapsed}`;
  }, [
    sequencerCompletedSteps,
    sequencerProgress,
    sequencerProgressPercent,
    sequencerTotalSteps,
  ]);
  const sequencerPrimaryAction: "start" | "pause" | "resume" =
    sequencerRuntimeState === "RUNNING" || sequencerRuntimeState === "STOP_REQUESTED"
      ? "pause"
      : sequencerRuntimeState === "PAUSED"
      ? "resume"
      : "start";
  const sequencerPrimaryLabel =
    sequencerPrimaryAction === "pause"
      ? "Pause"
      : sequencerPrimaryAction === "resume"
      ? "Resume"
      : "Start";
  const sequencerPrimaryDisabled =
    sequencerActionBusy ||
    (sequencerPrimaryAction === "start" && sequencerStatus?.loaded === false);

  return {
    sequencerOpen,
    setSequencerOpen,
    sequencerStatus,
    sequencerStatusLoading,
    sequencerProcessState,
    sequencerRuntimeState,
    sequencerLoaded,
    sequencerProgress,
    sequencerProgressPercent,
    sequencerCompletedSteps,
    sequencerTotalSteps,
    sequencerChipSuffix,
    sequencerChipTooltip,
    sequencerPrimaryAction,
    sequencerPrimaryLabel,
    sequencerPrimaryDisabled,
    sequencerActionBusy,
    sequencerValidateBusy,
    sequencerLoadBusy,
    sequencerLoadedYamlBusy,
    sequencerYamlText,
    sequencerYamlViewMode,
    setSequencerYamlViewMode,
    sequencerDiagnostics,
    sequencerModalError,
    sequencerEditorRef,
    sequencerFileInputRef,
    onSequencerYamlTextChange,
    refreshSequencerStatus,
    fetchSequencerLoadedYaml,
    openSequencerModal,
    runSequencerAction,
    jumpToSequencerDiagnostic,
    handleSequencerFileInput,
    validateSequencerYaml,
    loadSequencerYaml,
  };
}
