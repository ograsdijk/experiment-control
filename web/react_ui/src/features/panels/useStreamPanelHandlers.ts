import type { MutableRefObject } from "react";
import { notifications } from "@mantine/notifications";

import { resetStreamWorkspace } from "../../api";
import {
  isStreamBin2dPanel,
  isStreamBinStatsPanel,
  isStreamRawPanel,
  isStreamTracePanel,
  isStreamWaterfallPanel,
} from "../stream/panel_helpers";
import type { StreamCatalogEntry } from "../../types";
import type {
  StreamTarget,
  StreamTraceAverageMode,
  StreamTraceDecimator,
} from "../stream/types";
import {
  DEFAULT_STREAM_OVERLAY_COUNT,
  DEFAULT_WATERFALL_ROWS,
  inferChannelCountFromShape,
  normalizeShape,
  normalizeTraceAverageMode,
  normalizeTraceDecimator,
  normalizeTraceMaxFps,
  normalizeTraceMaxPoints,
  normalizeTraceRollingWindow,
} from "../stream/utils";
import { useStreamAnalysis } from "../stream_analysis/StreamAnalysisContext";
import { useTelemetry } from "../telemetry/TelemetryContext";
import { usePanels } from "./PanelsContext";
import { usePlotTick } from "./PlotTickContext";

/**
 * Per-panel stream-trace config setters + the buffer-clear utilities.
 *
 * App.tsx historically defined ~13 of these inline:
 *
 * - **Target picker / channel / overlay count** for raw stream panels:
 *   `setStreamPanelTarget`, `setStreamPanelTargetFromKey`,
 *   `setStreamPanelOverlayCount`, `setStreamPanelChannelIndex`.
 * - **Trace decimator + rate config** for stream panels:
 *   `setStreamPanelTraceDecimator`, `setStreamPanelTraceMaxPoints`,
 *   `setStreamPanelTraceMaxFps`, `setStreamPanelRollingWindow`,
 *   `setStreamPanelAverageMode`.
 * - **Buffer-clear helpers**: `clearPanelBuffers`,
 *   `clearStreamPanelFrames`, `clearStreamBinStatsPanel`,
 *   `clearStreamBin2dPanel`, `clearWorkspaceBinPanels`.
 *
 * **Notable**: `setStreamPanelTarget` was accidentally removed in the
 * round-16 Python-driven block-deletion (it lived inside the deleted
 * range with the other Y-axis helpers); this hook restores it. The
 * vite build was lenient on the unresolved reference, but a strict TS
 * check (`tsc --noEmit`) caught the missing definition; the runtime
 * stream-target picker would have crashed once a user touched it.
 *
 * Args:
 *
 * - `streamCatalogByKey` — derived in App.tsx from the
 *   `streamCatalog` state; used by `setStreamPanelTargetFromKey` to
 *   resolve units/shape from the cached metadata.
 * - `streamAnalysisReadyRef` — App.tsx ref tracking whether the
 *   stream_analysis process has registered RPC; consulted by
 *   `clearStreamBinStatsPanel` / `clearStreamBin2dPanel` before
 *   calling the workspace reset RPC.
 */

export interface StreamPanelHandlersArgs {
  streamCatalogByKey: Map<string, StreamCatalogEntry>;
  streamAnalysisReadyRef: MutableRefObject<boolean>;
}

export function useStreamPanelHandlers(args: StreamPanelHandlersArgs) {
  const { streamCatalogByKey, streamAnalysisReadyRef } = args;
  const { panels, setPanels, panelsRef } = usePanels();
  const { setPlotTick } = usePlotTick();
  const { streamWorkspacesRef } = useStreamAnalysis();
  const {
    buffersRef,
    streamFramesRef,
    streamTraceOverlayRef,
    streamExtraChannelRef,
    streamBinStatsOverlayRef,
    streamBinStatsFitOverlayRef,
    streamBinStatsRef,
    streamBin2dRef,
  } = useTelemetry();

  // ---- buffer-clear helpers --------------------------------------

  const clearPanelBuffers = (panelId: string) => {
    const panelBuffers = buffersRef.get(panelId);
    if (!panelBuffers) {
      return;
    }
    for (const buffer of panelBuffers.values()) {
      buffer.clear();
    }
    setPlotTick((tick) => tick + 1);
  };

  const clearStreamPanelFrames = (panelId: string) => {
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    streamExtraChannelRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const clearWorkspaceBinPanels = (
    workspaceId: string,
    nodeId?: string | null
  ) => {
    const workspace = streamWorkspacesRef.current[workspaceId] ?? null;
    const allowedOutputIds =
      workspace && nodeId
        ? new Set(
            workspace.publishOutputs
              .filter((output) => output.nodeId === nodeId)
              .map((output) => output.outputId)
          )
        : null;
    for (const panel of panelsRef.current) {
      if (!isStreamBinStatsPanel(panel) && !isStreamBin2dPanel(panel)) {
        continue;
      }
      if (panel.workspaceId !== workspaceId) {
        continue;
      }
      if (allowedOutputIds && !allowedOutputIds.has(panel.outputId ?? "")) {
        continue;
      }
      if (isStreamBinStatsPanel(panel)) {
        streamBinStatsRef.delete(panel.id);
        streamBinStatsFitOverlayRef.set(panel.id, new Map());
      } else {
        streamBin2dRef.delete(panel.id);
      }
    }
    setPlotTick((tick) => tick + 1);
  };

  const clearStreamBinStatsPanel = async (panelId: string) => {
    const panel = panels.find((entry) => entry.id === panelId);
    if (!panel || !isStreamBinStatsPanel(panel)) {
      return;
    }
    const workspace = streamWorkspacesRef.current[panel.workspaceId] ?? null;
    const outputId = String(panel.outputId ?? "").trim();
    const output = workspace?.publishOutputs.find(
      (entry) => entry.outputId === outputId
    );
    const nodeId = output?.nodeId ?? null;
    const node = nodeId
      ? workspace?.graphNodes.find((entry) => entry.nodeId === nodeId) ?? null
      : null;
    if (
      streamAnalysisReadyRef.current &&
      workspace &&
      node &&
      node.op === "aggregate.bin_stats"
    ) {
      const resp = await resetStreamWorkspace(workspace.workspaceId, node.nodeId);
      if (!resp.ok) {
        notifications.show({
          color: "red",
          title: "Clear binned data failed",
          message:
            resp.error?.message ?? resp.error?.code ?? "workspace.reset failed",
        });
      } else {
        clearWorkspaceBinPanels(workspace.workspaceId, node.nodeId);
        return;
      }
    }
    streamBinStatsRef.delete(panelId);
    streamBinStatsOverlayRef.set(panelId, new Map());
    streamBinStatsFitOverlayRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const clearStreamBin2dPanel = async (panelId: string) => {
    const panel = panels.find((entry) => entry.id === panelId);
    if (!panel || !isStreamBin2dPanel(panel)) {
      return;
    }
    const workspace = streamWorkspacesRef.current[panel.workspaceId] ?? null;
    const outputId = String(panel.outputId ?? "").trim();
    const output = workspace?.publishOutputs.find(
      (entry) => entry.outputId === outputId
    );
    const nodeId = output?.nodeId ?? null;
    const node = nodeId
      ? workspace?.graphNodes.find((entry) => entry.nodeId === nodeId) ?? null
      : null;
    if (
      streamAnalysisReadyRef.current &&
      workspace &&
      node &&
      node.op === "aggregate.bin2d_stats"
    ) {
      const resp = await resetStreamWorkspace(workspace.workspaceId, node.nodeId);
      if (!resp.ok) {
        notifications.show({
          color: "red",
          title: "Clear binned data failed",
          message:
            resp.error?.message ?? resp.error?.code ?? "workspace.reset failed",
        });
      } else {
        clearWorkspaceBinPanels(workspace.workspaceId, node.nodeId);
        return;
      }
    }
    streamBin2dRef.delete(panelId);
    setPlotTick((tick) => tick + 1);
  };

  // ---- stream-trace config setters -------------------------------

  const setStreamPanelTarget = (
    panelId: string,
    target: StreamTarget | null
  ) => {
    const targetChannelCount = inferChannelCountFromShape(target?.shape);
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId &&
        isStreamTracePanel(panel) &&
        panel.sourceMode === "raw"
          ? {
              ...panel,
              stream: target,
              channelIndex:
                targetChannelCount <= 1
                  ? 0
                  : Math.max(
                      0,
                      Math.min(panel.channelIndex, targetChannelCount - 1)
                    ),
              // Drop extra channels that no longer exist on the new
              // stream (and reset entirely for single-channel streams).
              ...(isStreamRawPanel(panel)
                ? {
                    extraChannelIndices:
                      targetChannelCount <= 1
                        ? []
                        : (panel.extraChannelIndices ?? []).filter(
                            (value) =>
                              Math.trunc(value) < targetChannelCount &&
                              Math.trunc(value) !== panel.channelIndex
                          ),
                  }
                : {}),
            }
          : panel
      )
    );
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    streamExtraChannelRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamPanelTargetFromKey = (
    panelId: string,
    targetKey: string | null
  ) => {
    if (!targetKey) {
      setStreamPanelTarget(panelId, null);
      return;
    }
    const splitAt = targetKey.indexOf("|");
    if (splitAt <= 0 || splitAt >= targetKey.length - 1) {
      setStreamPanelTarget(panelId, null);
      return;
    }
    const deviceId = targetKey.slice(0, splitAt);
    const stream = targetKey.slice(splitAt + 1);
    const meta = streamCatalogByKey.get(targetKey);
    setStreamPanelTarget(panelId, {
      deviceId,
      stream,
      units: typeof meta?.units === "string" ? meta.units : undefined,
      shape: normalizeShape(meta?.shape),
    });
  };

  const setStreamPanelOverlayCount = (panelId: string, value: number) => {
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamTracePanel(panel)
          ? {
              ...panel,
              overlayCount: Number.isFinite(value)
                ? Math.max(
                    1,
                    Math.min(
                      isStreamWaterfallPanel(panel) ? 600 : 80,
                      Math.trunc(value)
                    )
                  )
                : isStreamWaterfallPanel(panel)
                ? DEFAULT_WATERFALL_ROWS
                : DEFAULT_STREAM_OVERLAY_COUNT,
            }
          : panel
      )
    );
  };

  const setStreamPanelChannelIndex = (panelId: string, value: number) => {
    const nextChannel = Number.isFinite(value)
      ? Math.max(0, Math.trunc(value))
      : 0;
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId &&
        isStreamTracePanel(panel) &&
        panel.sourceMode === "raw"
          ? { ...panel, channelIndex: nextChannel }
          : panel
      )
    );
    // The channel is part of the subscription key, so changing it swaps
    // the WS/snapshot data. Clear the stale (old-channel) buffers and
    // force an immediate redraw instead of waiting for the next frame.
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    streamExtraChannelRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  // Multi-channel selection for raw stream panels: the first selected
  // channel becomes the primary `channelIndex`, the rest become
  // `extraChannelIndices` (each drawn as its own latest-frame line).
  const setStreamPanelChannels = (panelId: string, channels: number[]) => {
    const clean = [
      ...new Set(
        channels
          .filter((value) => Number.isFinite(value))
          .map((value) => Math.max(0, Math.trunc(value)))
      ),
    ];
    const primary = clean.length > 0 ? clean[0] : 0;
    const extras = clean.slice(1);
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId &&
        isStreamRawPanel(panel) &&
        panel.sourceMode === "raw"
          ? { ...panel, channelIndex: primary, extraChannelIndices: extras }
          : panel
      )
    );
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    streamExtraChannelRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamPanelTraceDecimator = (
    panelId: string,
    decimator: StreamTraceDecimator
  ) => {
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamTracePanel(panel)
          ? { ...panel, traceDecimator: normalizeTraceDecimator(decimator) }
          : panel
      )
    );
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    streamExtraChannelRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamPanelTraceMaxPoints = (panelId: string, value: number) => {
    const nextPoints = normalizeTraceMaxPoints(value);
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamTracePanel(panel)
          ? { ...panel, traceMaxPoints: nextPoints }
          : panel
      )
    );
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    streamExtraChannelRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamPanelTraceMaxFps = (panelId: string, value: number) => {
    const nextFps = normalizeTraceMaxFps(value);
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamTracePanel(panel)
          ? { ...panel, traceMaxFps: nextFps }
          : panel
      )
    );
  };

  const setStreamPanelRollingWindow = (panelId: string, value: number) => {
    const nextWindow = normalizeTraceRollingWindow(value);
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamTracePanel(panel)
          ? { ...panel, rollingWindow: nextWindow }
          : panel
      )
    );
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    streamExtraChannelRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  const setStreamPanelAverageMode = (
    panelId: string,
    mode: StreamTraceAverageMode
  ) => {
    const nextMode = normalizeTraceAverageMode(mode);
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId && isStreamTracePanel(panel)
          ? { ...panel, averageMode: nextMode }
          : panel
      )
    );
    streamFramesRef.set(panelId, []);
    streamTraceOverlayRef.set(panelId, new Map());
    streamExtraChannelRef.set(panelId, new Map());
    setPlotTick((tick) => tick + 1);
  };

  return {
    clearPanelBuffers,
    clearStreamPanelFrames,
    clearStreamBinStatsPanel,
    clearStreamBin2dPanel,
    clearWorkspaceBinPanels,
    setStreamPanelTarget,
    setStreamPanelTargetFromKey,
    setStreamPanelOverlayCount,
    setStreamPanelChannelIndex,
    setStreamPanelChannels,
    setStreamPanelTraceDecimator,
    setStreamPanelTraceMaxPoints,
    setStreamPanelTraceMaxFps,
    setStreamPanelRollingWindow,
    setStreamPanelAverageMode,
  };
}
