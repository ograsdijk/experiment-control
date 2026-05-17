import {
  isStreamBin2dPanel,
  isStreamBinStatsPanel,
  isStreamParamsPanel,
  isStreamScalarPanel,
  isStreamTracePanel,
} from "../stream/panel_helpers";
import type {
  PlotStreamBin2dPanelState,
  PlotStreamBinStatsPanelState,
  PlotStreamParamsPanelState,
  PlotStreamScalarPanelState,
  StreamTraceSourceMode,
} from "../stream/types";
import {
  defaultOutputForKind,
  workspaceOutputKind,
  workspaceOutputOptionsByKind,
} from "../stream/workspace";
import { useStreamAnalysis } from "../stream_analysis/StreamAnalysisContext";
import { useTelemetry } from "../telemetry/TelemetryContext";
import { usePanels } from "./PanelsContext";

/**
 * Workspace / output handlers for stream panels.
 *
 * App.tsx historically defined ~9 of these inline. They mutate
 * panel.workspaceId / panel.outputId / panel.overlayOutputIds /
 * panel.fitOverlayOutputIds and as a side effect clear the relevant
 * per-stream overlay refs so the panel rebuilds its display from the
 * new workspace's data.
 *
 * **Handlers**:
 *
 * - Stream-trace (raw vs dag source mode + workspace + output):
 *   `setStreamTracePanelSourceMode`, `setStreamTracePanelWorkspace`,
 *   `setStreamTracePanelOutput`, `setStreamTracePanelOverlayOutputs`.
 * - Stream-analysis (scalar / params / bin-stats / bin2d) panel
 *   workspace + output switches:
 *   `setStreamAnalysisPanelWorkspace`, `setStreamAnalysisPanelOutput`.
 * - Stream-params output list: `setStreamParamsPanelOutputs`.
 * - Stream-binstats overlay/fit-overlay output lists:
 *   `setStreamBinStatsOverlayOutputs`,
 *   `setStreamBinStatsFitOverlayOutputs`.
 *
 * `clearPanelBuffers` is taken as an arg because `setStreamAnalysisPanelWorkspace`
 * and `setStreamAnalysisPanelOutput` call it when switching a scalar
 * panel to clear its accumulated time-series buffer.
 */

export interface StreamWorkspaceHandlersArgs {
  clearPanelBuffers: (panelId: string) => void;
}

export function useStreamWorkspaceHandlers(args: StreamWorkspaceHandlersArgs) {
  const { clearPanelBuffers } = args;
  const { panels, setPanels, setPlotTick } = usePanels();
  const { streamWorkspacesRef } = useStreamAnalysis();
  const {
    streamFramesRef,
    streamTraceOverlayRef,
    streamBinStatsOverlayRef,
    streamBinStatsFitOverlayRef,
    streamParamsLatestRef,
    streamBinStatsRef,
    streamBin2dRef,
  } = useTelemetry();

  const setStreamTracePanelSourceMode = (
    panelId: string,
    sourceMode: StreamTraceSourceMode
  ) => {
    setPanels((prev) =>
      prev.map((panel) => {
        if (panel.id !== panelId || !isStreamTracePanel(panel)) {
          return panel;
        }
        if (panel.sourceMode === sourceMode) {
          return panel;
        }
        if (sourceMode === "raw") {
          return {
            ...panel,
            sourceMode: "raw",
            overlayOutputIds: [],
          };
        }
        const workspaceId =
          panel.workspaceId && streamWorkspacesRef.current[panel.workspaceId]
            ? panel.workspaceId
            : Object.keys(streamWorkspacesRef.current).sort()[0] ?? panel.workspaceId;
        const workspace = workspaceId
          ? streamWorkspacesRef.current[workspaceId] ?? null
          : null;
        const outputId =
          panel.outputId &&
          workspace &&
          workspaceOutputKind(workspace, panel.outputId) === "trace"
            ? panel.outputId
            : defaultOutputForKind(workspace, "trace");
        return {
          ...panel,
          sourceMode: "dag",
          workspaceId,
          outputId,
          overlayOutputIds: [],
          stream: workspace?.stream ?? panel.stream,
          channelIndex: workspace?.channelIndex ?? panel.channelIndex,
        };
      })
    );
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamTracePanelWorkspace = (
    panelId: string,
    workspaceId: string | null
  ) => {
    const nextWorkspaceId = String(workspaceId ?? "").trim();
    const workspace = streamWorkspacesRef.current[nextWorkspaceId] ?? null;
    if (!nextWorkspaceId || !workspace) {
      return;
    }
    setPanels((prev) =>
      prev.map((panel) => {
        if (
          panel.id !== panelId ||
          !isStreamTracePanel(panel) ||
          panel.sourceMode !== "dag"
        ) {
          return panel;
        }
        const outputId = defaultOutputForKind(workspace, "trace");
        return {
          ...panel,
          workspaceId: nextWorkspaceId,
          outputId,
          overlayOutputIds: [],
          stream: workspace.stream,
          channelIndex: workspace.channelIndex,
        };
      })
    );
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamTracePanelOutput = (
    panelId: string,
    outputId: string | null
  ) => {
    const nextOutputId = String(outputId ?? "").trim() || null;
    setPanels((prev) =>
      prev.map((panel) => {
        if (
          panel.id !== panelId ||
          !isStreamTracePanel(panel) ||
          panel.sourceMode !== "dag"
        ) {
          return panel;
        }
        return {
          ...panel,
          outputId: nextOutputId,
          overlayOutputIds: (panel.overlayOutputIds ?? []).filter(
            (id) => id !== nextOutputId
          ),
        };
      })
    );
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamTracePanelOverlayOutputs = (
    panelId: string,
    outputIds: string[]
  ) => {
    const nextSet = new Set(
      outputIds
        .map((value) => String(value ?? "").trim())
        .filter((value) => value.length > 0)
    );
    setPanels((prev) =>
      prev.map((panel) => {
        if (
          panel.id !== panelId ||
          !isStreamTracePanel(panel) ||
          panel.sourceMode !== "dag"
        ) {
          return panel;
        }
        const primary = String(panel.outputId ?? "").trim();
        if (primary) {
          nextSet.delete(primary);
        }
        return {
          ...panel,
          overlayOutputIds: [...nextSet],
        };
      })
    );
    streamTraceOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamAnalysisPanelWorkspace = (
    panelId: string,
    workspaceId: string | null
  ) => {
    const nextWorkspaceId = String(workspaceId ?? "").trim();
    const nextWorkspace = streamWorkspacesRef.current[nextWorkspaceId];
    if (!nextWorkspaceId || !nextWorkspace) {
      return;
    }
    const panel = panels.find((entry) => entry.id === panelId);
    if (
      !panel ||
      (!isStreamScalarPanel(panel) &&
        !isStreamParamsPanel(panel) &&
        !isStreamBinStatsPanel(panel) &&
        !isStreamBin2dPanel(panel))
    ) {
      return;
    }
    if (panel.workspaceId === nextWorkspaceId) {
      return;
    }
    const nextOutputId = isStreamScalarPanel(panel)
      ? defaultOutputForKind(nextWorkspace, "scalar")
      : isStreamParamsPanel(panel)
      ? null
      : isStreamBinStatsPanel(panel)
      ? defaultOutputForKind(nextWorkspace, "hist_agg")
      : defaultOutputForKind(nextWorkspace, "hist2d");
    const updated = isStreamScalarPanel(panel)
      ? ({
          ...panel,
          workspaceId: nextWorkspaceId,
          outputId: nextOutputId,
          stream: nextWorkspace.stream,
          channelIndex: nextWorkspace.channelIndex,
          analysis: nextWorkspace.analysis,
        } as PlotStreamScalarPanelState)
      : isStreamParamsPanel(panel)
      ? ({
          ...panel,
          workspaceId: nextWorkspaceId,
          outputIds: (() => {
            const paramsOutputs = workspaceOutputOptionsByKind(
              nextWorkspace,
              "params_map"
            ).map((item) => item.value);
            if (paramsOutputs.length > 0) {
              return paramsOutputs;
            }
            const firstScalar = defaultOutputForKind(nextWorkspace, "scalar");
            return firstScalar ? [firstScalar] : [];
          })(),
        } as PlotStreamParamsPanelState)
      : isStreamBinStatsPanel(panel)
      ? ({
          ...panel,
          workspaceId: nextWorkspaceId,
          outputId: nextOutputId,
          overlayOutputIds: [],
          fitOverlayOutputIds: [],
          stream: nextWorkspace.stream,
          channelIndex: nextWorkspace.channelIndex,
          analysis: nextWorkspace.analysis,
          binStats: nextWorkspace.binStats,
        } as PlotStreamBinStatsPanelState)
      : ({
          ...panel,
          workspaceId: nextWorkspaceId,
          outputId: nextOutputId,
        } as PlotStreamBin2dPanelState);
    setPanels((prev) =>
      prev.map((entry) => (entry.id === panelId ? updated : entry))
    );
    if (isStreamScalarPanel(panel)) {
      clearPanelBuffers(panelId);
    } else if (isStreamParamsPanel(panel)) {
      streamParamsLatestRef.set(panelId, {});
      setPlotTick((tick) => tick + 1);
    } else if (isStreamBinStatsPanel(panel)) {
      streamBinStatsRef.delete(panelId);
      streamBinStatsOverlayRef.set(panelId, new Map());
      streamBinStatsFitOverlayRef.set(panelId, new Map());
      setPlotTick((tick) => tick + 1);
    } else {
      streamBin2dRef.delete(panelId);
      setPlotTick((tick) => tick + 1);
    }
  };

  const setStreamAnalysisPanelOutput = (
    panelId: string,
    outputId: string | null
  ) => {
    const nextOutputId = String(outputId ?? "").trim() || null;
    const panel = panels.find((entry) => entry.id === panelId);
    if (
      !panel ||
      (!isStreamScalarPanel(panel) &&
        !isStreamBinStatsPanel(panel) &&
        !isStreamBin2dPanel(panel))
    ) {
      return;
    }
    setPanels((prev) =>
      prev.map((entry) => {
        if (entry.id !== panelId) {
          return entry;
        }
        if (isStreamScalarPanel(entry)) {
          return { ...entry, outputId: nextOutputId };
        }
        if (isStreamBinStatsPanel(entry)) {
          return { ...entry, outputId: nextOutputId };
        }
        return { ...entry, outputId: nextOutputId };
      })
    );
    if (isStreamScalarPanel(panel)) {
      clearPanelBuffers(panelId);
    } else if (isStreamBinStatsPanel(panel)) {
      streamBinStatsRef.delete(panelId);
      streamBinStatsOverlayRef.set(panelId, new Map());
      streamBinStatsFitOverlayRef.set(panelId, new Map());
      setPlotTick((tick) => tick + 1);
    } else {
      streamBin2dRef.delete(panelId);
      setPlotTick((tick) => tick + 1);
    }
  };

  const setStreamParamsPanelOutputs = (
    panelId: string,
    outputIds: string[]
  ) => {
    const next = outputIds
      .map((value) => String(value ?? "").trim())
      .filter((value) => value.length > 0);
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamParamsPanel(panel)
          ? { ...panel, outputIds: next }
          : panel
      )
    );
    streamParamsLatestRef.set(panelId, {});
    setPlotTick((tick) => tick + 1);
  };

  const setStreamBinStatsOverlayOutputs = (
    panelId: string,
    outputIds: string[]
  ) => {
    const next = outputIds
      .map((value) => String(value ?? "").trim())
      .filter((value) => value.length > 0);
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamBinStatsPanel(panel)
          ? { ...panel, overlayOutputIds: next }
          : panel
      )
    );
    streamBinStatsOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamBinStatsFitOverlayOutputs = (
    panelId: string,
    outputIds: string[]
  ) => {
    const next = outputIds
      .map((value) => String(value ?? "").trim())
      .filter((value) => value.length > 0);
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamBinStatsPanel(panel)
          ? { ...panel, fitOverlayOutputIds: next }
          : panel
      )
    );
    streamBinStatsFitOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  return {
    setStreamTracePanelSourceMode,
    setStreamTracePanelWorkspace,
    setStreamTracePanelOutput,
    setStreamTracePanelOverlayOutputs,
    setStreamAnalysisPanelWorkspace,
    setStreamAnalysisPanelOutput,
    setStreamParamsPanelOutputs,
    setStreamBinStatsOverlayOutputs,
    setStreamBinStatsFitOverlayOutputs,
  };
}
