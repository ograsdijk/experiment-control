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
import type {
  SequencerAdaptiveStudyStatus,
  SequencerDiagnostic,
  SequencerStatus,
  SequencerYamlEditorHandle,
} from "./types";
import {
  computeSequencerDiagnosticJumpPlan,
  focusSequencerDiagnosticOffset,
} from "./diagnostics_jump";
import {
  formatDurationCompact,
  normalizeSequencerDiagnostics,
  normalizeSequencerProgress,
  sameSequencerStatus,
} from "./utils";
import {
  buildLocalConditionDiagnostics,
  mergeDiagnostics,
} from "./validation";

type SequencerAction = "start" | "pause" | "resume" | "stop";
type AdaptiveStartMode = "reset" | "resume" | "warm_start";
type SequencerRunMode = "once" | "repeat" | "continuous";
export type SequencerLoadSource = "editor" | "library";
type SequencerOverrideValueType = "number" | "bool" | "string" | "json" | "null";
type SequencerOverrideRow = {
  id: string;
  name: string;
  valueType: SequencerOverrideValueType;
  valueText: string;
};
type SequencerLibraryEntry = {
  id: string;
  label: string | null;
  description: string | null;
  path: string | null;
  source: string | null;
  vars: string[];
};

export function buildSequencerLoadRequest(
  loadSource: SequencerLoadSource,
  selectedSequenceId: string | null,
  yamlText: string
) {
  const trimmedSequenceId = selectedSequenceId?.trim() ?? null;
  const useLibraryLoad = loadSource === "library" && Boolean(trimmedSequenceId);
  return {
    action: useLibraryLoad ? "sequencer.library.load" : "sequencer.load",
    params: useLibraryLoad
      ? { sequence_id: trimmedSequenceId }
      : { text: yamlText },
    source: useLibraryLoad ? "sequencer-library-load" : "sequencer-load",
  };
}

export function buildSequencerStartParams(
  loadSource: SequencerLoadSource,
  sequencerLibraryConfigured: boolean,
  selectedSequenceId: string | null,
  adaptiveParams: Record<string, { mode: AdaptiveStartMode }> | undefined,
  runMode: SequencerRunMode,
  repeatCount: number,
  varsOverride: Record<string, unknown>
) {
  const startParams: Record<string, unknown> = {};
  if (adaptiveParams) {
    startParams.adaptive = adaptiveParams;
  }
  if (
    loadSource === "library" &&
    sequencerLibraryConfigured &&
    selectedSequenceId?.trim()
  ) {
    startParams.sequence_id = selectedSequenceId.trim();
  }
  if (runMode === "repeat") {
    startParams.repeat_count = Math.max(1, Math.trunc(Number(repeatCount) || 1));
  } else if (runMode === "continuous") {
    startParams.continuous = true;
  }
  if (Object.keys(varsOverride).length > 0) {
    startParams.vars_override = varsOverride;
  }
  return startParams;
}

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
  const [sequencerAdaptiveModes, setSequencerAdaptiveModes] = useState<
    Record<string, AdaptiveStartMode>
  >({});
  const [sequencerAdaptiveClearBusy, setSequencerAdaptiveClearBusy] = useState<
    string | null
  >(null);
  const [sequencerRunMode, setSequencerRunMode] =
    useState<SequencerRunMode>("once");
  const [sequencerRepeatCount, setSequencerRepeatCount] = useState(1);
  const [sequencerLibraryConfigured, setSequencerLibraryConfigured] =
    useState(false);
  const [sequencerLibraryEntries, setSequencerLibraryEntries] = useState<
    SequencerLibraryEntry[]
  >([]);
  const [sequencerLibraryLoading, setSequencerLibraryLoading] = useState(false);
  const [sequencerLibraryError, setSequencerLibraryError] = useState<string | null>(
    null
  );
  const [sequencerLoadSource, setSequencerLoadSource] =
    useState<SequencerLoadSource>("editor");
  const [sequencerSelectedSequenceId, setSequencerSelectedSequenceId] = useState<
    string | null
  >(null);
  const [sequencerRuntimeVarNamesByProcessId, setSequencerRuntimeVarNamesByProcessId] =
    useState<Record<string, string[]>>({});
  const [sequencerOverrideRows, setSequencerOverrideRows] = useState<
    SequencerOverrideRow[]
  >([]);
  const sequencerOverrideIdRef = useRef(1);
  const sequencerEditorRef = useRef<SequencerYamlEditorHandle | null>(null);
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
              runId: current?.runId ?? null,
              state: current?.state ?? null,
              currentStep: current?.currentStep ?? null,
              loopMode: current?.loopMode ?? null,
              loopsCompleted: current?.loopsCompleted ?? null,
              loopsTarget: current?.loopsTarget ?? null,
              error: message ?? code ?? "sequencer.status failed",
              loaded: current?.loaded ?? null,
              activeSequenceId: current?.activeSequenceId ?? null,
              contextColumns: current?.contextColumns ?? null,
              loadedSource: current?.loadedSource ?? null,
              autoloadError: current?.autoloadError ?? null,
              progress: current?.progress ?? null,
              loadedAdaptiveIds: current?.loadedAdaptiveIds ?? [],
              adaptiveStudies: current?.adaptiveStudies ?? {},
            };
            if (sameSequencerStatus(current, nextStatus)) {
              return prev;
            }
            return { ...prev, [processId]: nextStatus };
          });
          return;
        }
        const result = resp.result as {
          run_id?: unknown;
          state?: unknown;
          current_step?: unknown;
          loop_mode?: unknown;
          loops_completed?: unknown;
          loops_target?: unknown;
          vars?: unknown;
          error?: unknown;
          loaded?: unknown;
          active_sequence_id?: unknown;
          context_columns?: unknown;
          loaded_source?: unknown;
          autoload_error?: unknown;
          progress?: unknown;
          loaded_adaptive_ids?: unknown;
          adaptive_studies?: unknown;
        };
        const normalizeInt = (value: unknown): number | null => {
          if (typeof value !== "number" || !Number.isFinite(value)) {
            return null;
          }
          return Math.max(0, Math.trunc(value));
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
        const loopMode =
          typeof result.loop_mode === "string" && result.loop_mode.trim().length > 0
            ? result.loop_mode
            : progress?.loopMode ?? null;
        const loopsCompleted =
          normalizeInt(result.loops_completed) ?? progress?.loopsCompleted ?? null;
        const loopsTarget =
          normalizeInt(result.loops_target) ?? progress?.loopsTarget ?? null;
        const runId = normalizeInt(result.run_id) ?? progress?.runId ?? null;
        const runtimeVarNames =
          result.vars && typeof result.vars === "object"
            ? Object.keys(result.vars as Record<string, unknown>)
                .map((value) => value.trim())
                .filter((value) => value.length > 0)
                .sort()
            : [];
        const loadedAdaptiveIds = Array.isArray(result.loaded_adaptive_ids)
          ? result.loaded_adaptive_ids
              .filter(
                (item): item is string =>
                  typeof item === "string" && item.trim().length > 0
              )
              .map((item) => item.trim())
          : [];
        const adaptiveStudiesRaw = result.adaptive_studies;
        const adaptiveStudies: Record<string, SequencerAdaptiveStudyStatus> =
          adaptiveStudiesRaw && typeof adaptiveStudiesRaw === "object"
            ? Object.fromEntries(
                Object.entries(
                  adaptiveStudiesRaw as Record<string, unknown>
                ).flatMap(([key, value]) => {
                  if (typeof key !== "string" || !key.trim()) {
                    return [];
                  }
                  if (!value || typeof value !== "object") {
                    return [];
                  }
                  const rawStudy = value as Record<string, unknown>;
                  const trialCountRaw = rawStudy.trial_count;
                  const trialCount =
                    typeof trialCountRaw === "number" &&
                    Number.isFinite(trialCountRaw)
                      ? Math.max(0, Math.trunc(trialCountRaw))
                      : 0;
                  return [
                    [
                      key,
                      {
                        controllerKind:
                          typeof rawStudy.controller_kind === "string" &&
                          rawStudy.controller_kind.trim()
                            ? rawStudy.controller_kind
                            : null,
                        trialCount,
                        lastMode:
                          typeof rawStudy.last_mode === "string" &&
                          rawStudy.last_mode.trim()
                            ? rawStudy.last_mode
                            : null,
                      } satisfies SequencerAdaptiveStudyStatus,
                    ],
                  ];
                })
              )
            : {};
        setSequencerStatusByProcessId((prev) => {
          const current = prev[processId];
          const nextStatus: SequencerStatus = {
            runId,
            state: typeof result.state === "string" ? result.state : null,
            currentStep:
              typeof result.current_step === "string" ? result.current_step : null,
            loopMode,
            loopsCompleted,
            loopsTarget,
            error: typeof result.error === "string" ? result.error : null,
            loaded:
              typeof result.loaded === "boolean"
                ? result.loaded
                : current?.loaded ?? null,
            activeSequenceId:
              typeof result.active_sequence_id === "string" &&
              result.active_sequence_id.trim().length > 0
                ? result.active_sequence_id
                : null,
            contextColumns,
            loadedSource,
            autoloadError,
            progress,
            loadedAdaptiveIds,
            adaptiveStudies,
          };
          if (sameSequencerStatus(current, nextStatus)) {
            return prev;
          }
          return { ...prev, [processId]: nextStatus };
        });
        setSequencerRuntimeVarNamesByProcessId((prev) => {
          const current = prev[processId] ?? [];
          if (JSON.stringify(current) === JSON.stringify(runtimeVarNames)) {
            return prev;
          }
          return { ...prev, [processId]: runtimeVarNames };
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setSequencerStatusByProcessId((prev) => {
          const current = prev[processId];
          const nextStatus: SequencerStatus = {
            runId: current?.runId ?? null,
            state: current?.state ?? null,
            currentStep: current?.currentStep ?? null,
            loopMode: current?.loopMode ?? null,
            loopsCompleted: current?.loopsCompleted ?? null,
            loopsTarget: current?.loopsTarget ?? null,
            error: message,
            loaded: current?.loaded ?? null,
            activeSequenceId: current?.activeSequenceId ?? null,
            contextColumns: current?.contextColumns ?? null,
            loadedSource: current?.loadedSource ?? null,
            autoloadError: current?.autoloadError ?? null,
            progress: current?.progress ?? null,
            loadedAdaptiveIds: current?.loadedAdaptiveIds ?? [],
            adaptiveStudies: current?.adaptiveStudies ?? {},
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

  const refreshSequencerLibrary = useCallback(
    async (processId: string, opts?: { silent?: boolean }) => {
      const silent = opts?.silent === true;
      if (sequencerLibraryLoading) {
        return;
      }
      setSequencerLibraryLoading(true);
      try {
        const resp = await callProcessFn(processId, "sequencer.library.list", {});
        if (!resp.ok || !resp.result || typeof resp.result !== "object") {
          const message = resp.error?.message ?? resp.error?.code ?? "Unknown error";
          setSequencerLibraryError(message);
          if (!silent) {
            notifications.show({
              color: "red",
              title: "Failed to fetch sequence library",
              message,
            });
          }
          return;
        }
        const result = resp.result as {
          configured?: unknown;
          entries?: unknown;
          last_error?: unknown;
          active_sequence_id?: unknown;
        };
        const entriesRaw = Array.isArray(result.entries) ? result.entries : [];
        const entries: SequencerLibraryEntry[] = entriesRaw
          .map((item) => {
            if (!item || typeof item !== "object") {
              return null;
            }
            const obj = item as Record<string, unknown>;
            const id =
              typeof obj.id === "string" && obj.id.trim().length > 0
                ? obj.id.trim()
                : "";
            if (!id) {
              return null;
            }
            const varsRaw = Array.isArray(obj.vars) ? obj.vars : [];
            const vars = varsRaw
              .filter((value): value is string => typeof value === "string")
              .map((value) => value.trim())
              .filter((value) => value.length > 0);
            return {
              id,
              label:
                typeof obj.label === "string" && obj.label.trim().length > 0
                  ? obj.label
                  : null,
              description:
                typeof obj.description === "string" && obj.description.trim().length > 0
                  ? obj.description
                  : null,
              path:
                typeof obj.path === "string" && obj.path.trim().length > 0
                  ? obj.path
                  : null,
              source:
                typeof obj.source === "string" && obj.source.trim().length > 0
                  ? obj.source
                  : null,
              vars,
            } satisfies SequencerLibraryEntry;
          })
          .filter((item): item is SequencerLibraryEntry => item !== null);
        setSequencerLibraryConfigured(result.configured === true);
        setSequencerLibraryEntries(entries);
        const activeSequenceId =
          typeof result.active_sequence_id === "string" &&
          result.active_sequence_id.trim().length > 0
            ? result.active_sequence_id
            : null;
        setSequencerSelectedSequenceId((prev) => {
          if (activeSequenceId) {
            return activeSequenceId;
          }
          if (prev && entries.some((entry) => entry.id === prev)) {
            return prev;
          }
          return entries[0]?.id ?? null;
        });
        const lastError =
          typeof result.last_error === "string" && result.last_error.trim().length > 0
            ? result.last_error
            : null;
        setSequencerLibraryError(lastError);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setSequencerLibraryError(message);
        if (!silent) {
          notifications.show({
            color: "red",
            title: "Failed to fetch sequence library",
            message,
          });
        }
      } finally {
        setSequencerLibraryLoading(false);
      }
    },
    [callProcessFn, sequencerLibraryLoading]
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
          active_sequence_id?: unknown;
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
            activeSequenceId:
              typeof result.active_sequence_id === "string" &&
              result.active_sequence_id.trim().length > 0
                ? result.active_sequence_id
                : null,
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
    await refreshSequencerLibrary(sequencerProcess.process_id, { silent: true });
  }, [
    fetchSequencerLoadedYaml,
    refreshSequencerLibrary,
    refreshSequencerStatus,
    sequencerProcess,
  ]);

  const reloadSequencerLibrary = useCallback(async () => {
    if (!sequencerProcess) {
      return;
    }
    setSequencerLibraryLoading(true);
    try {
      const resp = await callProcessFn(sequencerProcess.process_id, "sequencer.library.reload", {});
      if (!resp.ok || !resp.result || typeof resp.result !== "object") {
        const message = resp.error?.message ?? resp.error?.code ?? "Unknown error";
        setSequencerLibraryError(message);
        notifications.show({
          color: "red",
          title: "Failed to reload sequence library",
          message,
        });
        return;
      }
      const result = resp.result as {
        configured?: unknown;
        entries?: unknown;
        last_error?: unknown;
        active_sequence_id?: unknown;
      };
      const entriesRaw = Array.isArray(result.entries) ? result.entries : [];
      const entries: SequencerLibraryEntry[] = entriesRaw
        .map((item) => {
          if (!item || typeof item !== "object") {
            return null;
          }
          const obj = item as Record<string, unknown>;
          const id =
            typeof obj.id === "string" && obj.id.trim().length > 0
              ? obj.id.trim()
              : "";
          if (!id) {
            return null;
          }
          const varsRaw = Array.isArray(obj.vars) ? obj.vars : [];
          const vars = varsRaw
            .filter((value): value is string => typeof value === "string")
            .map((value) => value.trim())
            .filter((value) => value.length > 0);
          return {
            id,
            label:
              typeof obj.label === "string" && obj.label.trim().length > 0
                ? obj.label
                : null,
            description:
              typeof obj.description === "string" && obj.description.trim().length > 0
                ? obj.description
                : null,
            path:
              typeof obj.path === "string" && obj.path.trim().length > 0
                ? obj.path
                : null,
            source:
              typeof obj.source === "string" && obj.source.trim().length > 0
                ? obj.source
                : null,
            vars,
          } satisfies SequencerLibraryEntry;
        })
        .filter((item): item is SequencerLibraryEntry => item !== null);
      setSequencerLibraryConfigured(result.configured === true);
      setSequencerLibraryEntries(entries);
      const activeSequenceId =
        typeof result.active_sequence_id === "string" &&
        result.active_sequence_id.trim().length > 0
          ? result.active_sequence_id
          : null;
      setSequencerSelectedSequenceId((prev) => {
        if (activeSequenceId) {
          return activeSequenceId;
        }
        if (prev && entries.some((entry) => entry.id === prev)) {
          return prev;
        }
        return entries[0]?.id ?? null;
      });
      const lastError =
        typeof result.last_error === "string" && result.last_error.trim().length > 0
          ? result.last_error
          : null;
      setSequencerLibraryError(lastError);
      notifications.show({
        color: "teal",
        title: "Sequence library reloaded",
        message:
          entries.length > 0
            ? `${entries.length} entr${entries.length === 1 ? "y" : "ies"} available`
            : "No sequence entries found",
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setSequencerLibraryError(message);
      notifications.show({
        color: "red",
        title: "Failed to reload sequence library",
        message,
      });
    } finally {
      setSequencerLibraryLoading(false);
    }
  }, [callProcessFn, sequencerProcess]);

  const setAdaptiveMode = useCallback(
    (studyId: string, mode: AdaptiveStartMode) => {
      const normalizedId = studyId.trim();
      if (!normalizedId) {
        return;
      }
      setSequencerAdaptiveModes((prev) => {
        if (prev[normalizedId] === mode) {
          return prev;
        }
        return { ...prev, [normalizedId]: mode };
      });
    },
    []
  );

  const clearAdaptiveStudy = useCallback(
    async (studyId: string) => {
      if (!sequencerProcess || sequencerAdaptiveClearBusy) {
        return;
      }
      const normalizedId = studyId.trim();
      if (!normalizedId) {
        return;
      }
      setSequencerAdaptiveClearBusy(normalizedId);
      setSequencerModalError(null);
      try {
        const resp = await sendProcessCommand(
          sequencerProcess.process_id,
          "sequencer.adaptive.clear",
          { study_id: normalizedId },
          "sequencer-adaptive-clear"
        );
        if (!resp.ok) {
          const message = resp.error?.message ?? resp.error?.code ?? "Unknown error";
          setSequencerModalError(message);
          notifications.show({
            color: "red",
            title: "Failed to clear adaptive study",
            message,
          });
          return;
        }
        notifications.show({
          color: "teal",
          title: "Adaptive study cleared",
          message: normalizedId,
        });
        setSequencerAdaptiveModes((prev) => ({ ...prev, [normalizedId]: "reset" }));
        await refreshSequencerStatus(sequencerProcess.process_id);
      } finally {
        setSequencerAdaptiveClearBusy(null);
      }
    },
    [
      refreshSequencerStatus,
      sendProcessCommand,
      sequencerAdaptiveClearBusy,
      sequencerProcess,
    ]
  );

  const selectedLibraryEntryForOverrides = useMemo(() => {
    if (!sequencerSelectedSequenceId) {
      return null;
    }
    return (
      sequencerLibraryEntries.find(
        (entry) => entry.id === sequencerSelectedSequenceId
      ) ?? null
    );
  }, [sequencerLibraryEntries, sequencerSelectedSequenceId]);

  const sequencerRuntimeVarNames = useMemo(() => {
    if (!sequencerProcess) {
      return [] as string[];
    }
    return (
      sequencerRuntimeVarNamesByProcessId[sequencerProcess.process_id] ?? []
    );
  }, [sequencerProcess, sequencerRuntimeVarNamesByProcessId]);

  const sequencerOverrideVarOptions = useMemo(() => {
    const base =
      selectedLibraryEntryForOverrides?.vars &&
      selectedLibraryEntryForOverrides.vars.length > 0
        ? selectedLibraryEntryForOverrides.vars
        : sequencerRuntimeVarNames;
    return Array.from(
      new Set(base.map((value) => value.trim()).filter((value) => value.length > 0))
    ).sort();
  }, [selectedLibraryEntryForOverrides, sequencerRuntimeVarNames]);

  const addSequencerOverrideRow = useCallback(() => {
    setSequencerOverrideRows((prev) => {
      const used = new Set(prev.map((row) => row.name.trim()).filter(Boolean));
      const suggested =
        sequencerOverrideVarOptions.find((name) => !used.has(name)) ??
        sequencerOverrideVarOptions[0] ??
        "";
      return [
        ...prev,
        {
          id: `ovr-${sequencerOverrideIdRef.current++}`,
          name: suggested,
          valueType: "number",
          valueText: "",
        },
      ];
    });
  }, [sequencerOverrideVarOptions]);

  const removeSequencerOverrideRow = useCallback((rowId: string) => {
    setSequencerOverrideRows((prev) => prev.filter((row) => row.id !== rowId));
  }, []);

  const clearSequencerOverrides = useCallback(() => {
    setSequencerOverrideRows([]);
  }, []);

  const updateSequencerOverrideRow = useCallback(
    (
      rowId: string,
      patch: Partial<Pick<SequencerOverrideRow, "name" | "valueType" | "valueText">>
    ) => {
      setSequencerOverrideRows((prev) =>
        prev.map((row) => {
          if (row.id !== rowId) {
            return row;
          }
          const next = { ...row, ...patch };
          if (patch.valueType === "bool" && !["true", "false"].includes(next.valueText)) {
            next.valueText = "true";
          }
          if (patch.valueType === "null") {
            next.valueText = "";
          }
          return next;
        })
      );
    },
    []
  );

  const sequencerOverrideEvaluation = useMemo(() => {
    const errorsById: Record<string, string | null> = {};
    const payload: Record<string, unknown> = {};
    const normalizedNames = sequencerOverrideRows
      .map((row) => row.name.trim())
      .filter((name) => name.length > 0);
    const nameCounts: Record<string, number> = {};
    for (const name of normalizedNames) {
      nameCounts[name] = (nameCounts[name] ?? 0) + 1;
    }
    const known = new Set(sequencerOverrideVarOptions);
    let hasError = false;

    for (const row of sequencerOverrideRows) {
      const name = row.name.trim();
      if (!name) {
        errorsById[row.id] = "Variable name is required";
        hasError = true;
        continue;
      }
      if ((nameCounts[name] ?? 0) > 1) {
        errorsById[row.id] = "Duplicate variable name";
        hasError = true;
        continue;
      }
      if (known.size > 0 && !known.has(name)) {
        errorsById[row.id] = "Unknown variable for this sequence";
        hasError = true;
        continue;
      }

      try {
        if (row.valueType === "number") {
          const parsed = Number(row.valueText);
          if (!Number.isFinite(parsed)) {
            throw new Error("Value must be a finite number");
          }
          payload[name] = parsed;
          errorsById[row.id] = null;
          continue;
        }
        if (row.valueType === "bool") {
          const text = row.valueText.trim().toLowerCase();
          if (text !== "true" && text !== "false") {
            throw new Error("Value must be true or false");
          }
          payload[name] = text === "true";
          errorsById[row.id] = null;
          continue;
        }
        if (row.valueType === "string") {
          payload[name] = row.valueText;
          errorsById[row.id] = null;
          continue;
        }
        if (row.valueType === "json") {
          payload[name] = JSON.parse(row.valueText || "null");
          errorsById[row.id] = null;
          continue;
        }
        if (row.valueType === "null") {
          payload[name] = null;
          errorsById[row.id] = null;
          continue;
        }
        throw new Error("Unsupported value type");
      } catch (error) {
        errorsById[row.id] =
          error instanceof Error ? error.message : "Invalid value";
        hasError = true;
      }
    }

    const preview = JSON.stringify(payload, null, 2);
    return {
      errorsById,
      payload,
      hasError,
      isValid: !hasError,
      preview,
    };
  }, [sequencerOverrideRows, sequencerOverrideVarOptions]);

  const runSequencerAction = useCallback(
    async (action: SequencerAction) => {
      if (!sequencerProcess || sequencerActionBusy) {
        return;
      }
      const processId = sequencerProcess.process_id;
      setSequencerActionBusy(true);
      setSequencerModalError(null);
      try {
        if (action === "start" && !sequencerOverrideEvaluation.isValid) {
          const message = "Fix invalid run overrides before starting.";
          setSequencerModalError(message);
          notifications.show({
            color: "red",
            title: "Invalid run overrides",
            message,
          });
          return;
        }
        const loadedAdaptiveIds =
          sequencerStatusByProcessIdRef.current[processId]?.loadedAdaptiveIds ?? [];
        const adaptiveParams =
          action === "start" && loadedAdaptiveIds.length > 0
            ? Object.fromEntries(
                loadedAdaptiveIds.map((studyId) => [
                  studyId,
                  { mode: sequencerAdaptiveModes[studyId] ?? "reset" },
                ])
              )
            : undefined;
        const startParams =
          action === "start"
            ? buildSequencerStartParams(
                sequencerLoadSource,
                sequencerLibraryConfigured,
                sequencerSelectedSequenceId,
                adaptiveParams,
                sequencerRunMode,
                sequencerRepeatCount,
                sequencerOverrideEvaluation.payload
              )
            : {};
        const resp = await sendProcessCommand(
          processId,
          `sequencer.${action}`,
          action === "start" ? startParams : {},
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
        if (action === "start") {
          await refreshSequencerLibrary(processId, { silent: true });
        }
        await refreshProcesses();
      } finally {
        setSequencerActionBusy(false);
      }
    },
    [
      refreshProcesses,
      refreshSequencerLibrary,
      refreshSequencerStatus,
      sendProcessCommand,
      sequencerLibraryConfigured,
      sequencerOverrideEvaluation,
      sequencerRepeatCount,
      sequencerRunMode,
      sequencerSelectedSequenceId,
      sequencerAdaptiveModes,
      sequencerActionBusy,
      sequencerProcess,
      sequencerLoadSource,
    ]
  );

  const jumpToSequencerDiagnostic = useCallback(
    (line: number | null, column: number | null) => {
      const jumpPlan = computeSequencerDiagnosticJumpPlan(
        sequencerYamlText,
        sequencerYamlViewMode,
        line,
        column
      );
      if (!jumpPlan) {
        return;
      }
      const focusEditorAtLine = (attempt = 0) => {
        const applied = focusSequencerDiagnosticOffset(
          sequencerEditorRef.current,
          jumpPlan.offset
        );
        if (!applied) {
          if (attempt < 5) {
            window.setTimeout(() => focusEditorAtLine(attempt + 1), 0);
          }
        }
      };
      if (jumpPlan.requiresEditMode) {
        setSequencerYamlViewMode("edit");
        window.setTimeout(focusEditorAtLine, 0);
        return;
      }
      focusEditorAtLine();
    },
    [sequencerYamlText, sequencerYamlViewMode]
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
        setSequencerLoadSource("editor");
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
        "sequencer.preflight",
        {
          text: sequencerYamlText,
        },
        "sequencer-preflight"
      );
      if (!resp.ok) {
        const message = resp.error?.message ?? resp.error?.code ?? "Unknown error";
        setSequencerModalError(message);
        setSequencerDiagnostics([]);
        notifications.show({
          color: "red",
          title: "Validate + preflight failed",
          message,
        });
        return;
      }
      const result =
        resp.result && typeof resp.result === "object"
          ? (resp.result as {
              valid?: unknown;
              diagnostics?: unknown;
              summary?: unknown;
            })
          : null;
      const processDiagnostics = normalizeSequencerDiagnostics(result?.diagnostics);
      const localDiagnostics = buildLocalConditionDiagnostics(sequencerYamlText);
      const diagnostics = mergeDiagnostics(processDiagnostics, localDiagnostics);
      setSequencerDiagnostics(processDiagnostics);
      const errorCount = diagnostics.filter(
        (item) => item.severity === "error"
      ).length;
      const warningCount = diagnostics.filter(
        (item) => item.severity === "warning"
      ).length;
      const valid =
        result?.valid === true &&
        !diagnostics.some((item) => item.severity === "error");
      notifications.show({
        color: valid ? "teal" : "yellow",
        title: valid ? "Sequence checks passed" : "Sequence has issues",
        message: valid
          ? "No validation or preflight errors."
          : `${diagnostics.length} diagnostic${diagnostics.length === 1 ? "" : "s"} (${errorCount} errors, ${warningCount} warnings)`,
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
      const loadRequest = buildSequencerLoadRequest(
        sequencerLoadSource,
        sequencerSelectedSequenceId,
        sequencerYamlText
      );
      const resp = await sendProcessCommand(
        sequencerProcess.process_id,
        loadRequest.action,
        loadRequest.params,
        loadRequest.source
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
      setSequencerLoadSource(
        loadRequest.action === "sequencer.library.load" ? "library" : "editor"
      );
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
    sequencerSelectedSequenceId,
    sequencerLoadSource,
    sequencerYamlText,
  ]);

  const onSequencerYamlTextChange = useCallback((value: string) => {
    setSequencerYamlText(value);
    setSequencerModalError(null);
    setSequencerLoadSource("editor");
  }, []);

  const setSequencerSelectedSequenceIdForUi = useCallback(
    (sequenceId: string | null) => {
      setSequencerSelectedSequenceId(sequenceId);
      setSequencerLoadSource(sequenceId?.trim() ? "library" : "editor");
    },
    []
  );

  const sequencerLocalDiagnostics = useMemo(
    () => buildLocalConditionDiagnostics(sequencerYamlText),
    [sequencerYamlText]
  );
  const sequencerCombinedDiagnostics = useMemo(
    () => mergeDiagnostics(sequencerDiagnostics, sequencerLocalDiagnostics),
    [sequencerDiagnostics, sequencerLocalDiagnostics]
  );

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
    (sequencerPrimaryAction === "start" &&
      (sequencerStatus?.loaded === false || !sequencerOverrideEvaluation.isValid));

  useEffect(() => {
    const loadedAdaptiveIds = sequencerStatus?.loadedAdaptiveIds ?? [];
    setSequencerAdaptiveModes((prev) => {
      const next: Record<string, AdaptiveStartMode> = {};
      for (const studyId of loadedAdaptiveIds) {
        next[studyId] = prev[studyId] ?? "reset";
      }
      const sameKeys =
        Object.keys(prev).length === Object.keys(next).length &&
        Object.keys(next).every((key) => prev[key] === next[key]);
      if (sameKeys) {
        return prev;
      }
      return next;
    });
  }, [sequencerStatus?.loadedAdaptiveIds]);

  useEffect(() => {
    const activeId =
      typeof sequencerStatus?.activeSequenceId === "string" &&
      sequencerStatus.activeSequenceId.trim().length > 0
        ? sequencerStatus.activeSequenceId
        : null;
    if (activeId) {
      setSequencerSelectedSequenceId(activeId);
      return;
    }
    setSequencerSelectedSequenceId((prev) => {
      if (prev && sequencerLibraryEntries.some((entry) => entry.id === prev)) {
        return prev;
      }
      return sequencerLibraryEntries[0]?.id ?? null;
    });
  }, [sequencerLibraryEntries, sequencerStatus?.activeSequenceId]);

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
    sequencerDiagnostics: sequencerCombinedDiagnostics,
    sequencerModalError,
    sequencerAdaptiveModes,
    sequencerAdaptiveClearBusy,
    sequencerRunMode,
    setSequencerRunMode,
    sequencerRepeatCount,
    setSequencerRepeatCount,
    sequencerLibraryConfigured,
    sequencerLibraryEntries,
    sequencerLibraryLoading,
    sequencerLibraryError,
    sequencerSelectedSequenceId,
    setSequencerSelectedSequenceId: setSequencerSelectedSequenceIdForUi,
    reloadSequencerLibrary,
    sequencerOverrideRows,
    sequencerOverrideVarOptions,
    sequencerOverrideErrors: sequencerOverrideEvaluation.errorsById,
    sequencerOverridePreview: sequencerOverrideEvaluation.preview,
    sequencerOverridesValid: sequencerOverrideEvaluation.isValid,
    addSequencerOverrideRow,
    removeSequencerOverrideRow,
    updateSequencerOverrideRow,
    clearSequencerOverrides,
    sequencerEditorRef,
    sequencerFileInputRef,
    onSequencerYamlTextChange,
    refreshSequencerStatus,
    fetchSequencerLoadedYaml,
    openSequencerModal,
    runSequencerAction,
    setAdaptiveMode,
    clearAdaptiveStudy,
    jumpToSequencerDiagnostic,
    handleSequencerFileInput,
    validateSequencerYaml,
    loadSequencerYaml,
  };
}
