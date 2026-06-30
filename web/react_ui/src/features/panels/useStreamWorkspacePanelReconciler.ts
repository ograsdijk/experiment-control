import { useEffect } from "react";

import { sameStringArray } from "../common/compare";
import {
  isStreamBin2dPanel,
  isStreamBinStatsPanel,
  isStreamParamsPanel,
  isStreamScalarPanel,
  isStreamTracePanel,
} from "../stream/panel_helpers";
import { streamTargetKey } from "../stream/utils";
import {
  defaultOutputForKind,
  workspaceOutputKind,
  workspaceOutputOptionsByKind,
} from "../stream/workspace";
import { useStreamAnalysis } from "../stream_analysis/StreamAnalysisContext";
import { usePanels } from "./PanelsContext";

/**
 * Keeps per-panel `outputId` / `overlayOutputIds` / `stream` /
 * `channelIndex` valid as `streamWorkspaces` changes.
 *
 * For each panel kind:
 *
 * - **stream trace (dag source)**: re-resolve outputId against the
 *   workspace's trace outputs, drop overlay ids that no longer
 *   point at a trace, sync stream + channelIndex from the workspace.
 * - **stream scalar**: backfill outputId from the workspace's
 *   default scalar output if missing.
 * - **stream params**: drop selected outputIds that no longer
 *   resolve to a scalar or params_map output.
 * - **stream bin stats**: re-resolve outputId against hist_agg
 *   outputs, prune overlay + fit-overlay outputs against trace /
 *   fit_1d output sets.
 * - **stream bin 2d**: backfill outputId from the default hist2d
 *   output if missing.
 *
 * This consolidates a 120-line useEffect that used to live in
 * App.tsx.
 */

export function useStreamWorkspacePanelReconciler() {
  const { streamWorkspaces } = useStreamAnalysis();
  const { setPanels } = usePanels();

  useEffect(() => {
    setPanels((prev) => {
      let changed = false;
      const next = prev.map((panel) => {
        if (isStreamTracePanel(panel) && panel.sourceMode === "dag") {
          const workspace = streamWorkspaces[panel.workspaceId] ?? null;
          if (!workspace) {
            return panel;
          }
          const validTraceOutputIds = new Set(
            workspace.publishOutputs
              .filter(
                (entry) =>
                  workspaceOutputKind(workspace, entry.outputId) === "trace"
              )
              .map((entry) => entry.outputId)
          );
          const outputId =
            panel.outputId && workspaceOutputKind(workspace, panel.outputId) === "trace"
              ? panel.outputId
              : defaultOutputForKind(workspace, "trace");
          const overlayOutputIds = (panel.overlayOutputIds ?? []).filter(
            (id) => id !== outputId && validTraceOutputIds.has(id)
          );
          const currentStreamKey = panel.stream
            ? streamTargetKey(panel.stream.deviceId, panel.stream.stream)
            : "";
          const workspaceStreamKey = workspace?.stream
            ? streamTargetKey(
                workspace.stream.deviceId,
                workspace.stream.stream
              )
            : "";
          const streamChanged = currentStreamKey !== workspaceStreamKey;
          const channelChanged =
            panel.channelIndex !== (workspace?.channelIndex ?? panel.channelIndex);
          const outputChanged = panel.outputId !== outputId;
          const overlayChanged = !sameStringArray(
            panel.overlayOutputIds ?? [],
            overlayOutputIds
          );
          if (
            !streamChanged &&
            !channelChanged &&
            !outputChanged &&
            !overlayChanged
          ) {
            return panel;
          }
          changed = true;
          return {
            ...panel,
            outputId,
            overlayOutputIds,
            stream: workspace?.stream ?? panel.stream,
            channelIndex: workspace?.channelIndex ?? panel.channelIndex,
          };
        }
        if (isStreamScalarPanel(panel)) {
          if (panel.outputId) {
            return panel;
          }
          const workspace = streamWorkspaces[panel.workspaceId] ?? null;
          const outputId = defaultOutputForKind(workspace, "scalar");
          if (!outputId) {
            return panel;
          }
          changed = true;
          return { ...panel, outputId };
        }
        if (isStreamParamsPanel(panel)) {
          const workspace = streamWorkspaces[panel.workspaceId] ?? null;
          if (!workspace) {
            return panel;
          }
          const validOutputIds = new Set(
            workspaceOutputOptionsByKind(workspace, "scalar").map(
              (item) => item.value
            )
          );
          for (const item of workspaceOutputOptionsByKind(
            workspace,
            "params_map"
          )) {
            validOutputIds.add(item.value);
          }
          const outputIds = (panel.outputIds ?? []).filter((id) =>
            validOutputIds.has(id)
          );
          if (sameStringArray(panel.outputIds ?? [], outputIds)) {
            return panel;
          }
          changed = true;
          return { ...panel, outputIds };
        }
        if (isStreamBinStatsPanel(panel)) {
          const workspace = streamWorkspaces[panel.workspaceId] ?? null;
          if (!workspace) {
            return panel;
          }
          const outputId =
            panel.outputId &&
            workspaceOutputKind(workspace, panel.outputId) === "hist_agg"
              ? panel.outputId
              : defaultOutputForKind(workspace, "hist_agg");
          const validTraceOutputIds = new Set(
            workspaceOutputOptionsByKind(workspace, "trace").map(
              (item) => item.value
            )
          );
          const validFitOutputIds = new Set(
            workspaceOutputOptionsByKind(workspace, "fit_1d").map(
              (item) => item.value
            )
          );
          const overlayOutputIds = (panel.overlayOutputIds ?? []).filter((id) =>
            validTraceOutputIds.has(id)
          );
          const fitOverlayOutputIds = (panel.fitOverlayOutputIds ?? []).filter(
            (id) => validFitOutputIds.has(id)
          );
          const outputChanged = panel.outputId !== outputId;
          const overlayChanged = !sameStringArray(
            panel.overlayOutputIds ?? [],
            overlayOutputIds
          );
          const fitOverlayChanged = !sameStringArray(
            panel.fitOverlayOutputIds ?? [],
            fitOverlayOutputIds
          );
          if (!outputChanged && !overlayChanged && !fitOverlayChanged) {
            return panel;
          }
          changed = true;
          return { ...panel, outputId, overlayOutputIds, fitOverlayOutputIds };
        }
        if (isStreamBin2dPanel(panel)) {
          if (panel.outputId) {
            return panel;
          }
          const workspace = streamWorkspaces[panel.workspaceId] ?? null;
          const outputId = defaultOutputForKind(workspace, "hist2d");
          if (!outputId) {
            return panel;
          }
          changed = true;
          return { ...panel, outputId };
        }
        return panel;
      });
      return changed ? next : prev;
    });
  }, [streamWorkspaces]);
}
