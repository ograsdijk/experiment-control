import { notifications } from "@mantine/notifications";

import { validateStreamWorkspace } from "../../api";
import {
  isStreamBin2dPanel,
  isStreamBinStatsPanel,
  isStreamParamsPanel,
  isStreamScalarPanel,
  isStreamTracePanel,
} from "../stream/panel_helpers";
import { usePanels } from "../panels/PanelsContext";
import {
  cloneDagNodes,
  cloneDagOutputs,
  nodeKindFromOp,
  normalizeDagNode,
  normalizeDagOutput,
} from "../stream/dag";
import type {
  StreamAnalysisWorkspaceConfig,
  StreamDagNodeConfig,
  StreamDagOutputConfig,
} from "../stream/types";
import type { StreamCatalogEntry } from "../../types";
import type { StreamWorkspaceSyncResult } from "./useWorkspaceListManagement";
import {
  defaultOutputForKind,
  defaultStreamWorkspaceName,
  workspaceNodeMap,
  workspaceOutputOptionsByKind,
  workspaceStreamFromGraphNodes,
} from "../stream/workspace";
import { useTelemetry } from "../telemetry/TelemetryContext";
import { useStreamAnalysis } from "./StreamAnalysisContext";
import { markPanelsDirty } from "../panels/PanelInvalidationStore";

/**
 * `applyDaqWorkspace` — validate the DAQ workspace draft, commit it
 * to the React-side workspace registry, cascade the new outputs into
 * every panel bound to this workspace, and push the result to the
 * stream_analysis runtime.
 *
 * This is the heaviest single handler in the DAQ flow. It's split
 * into roughly four phases:
 *
 * 1. **Validate**: graph must have ≥1 node, unique node ids, exactly
 *    one `source.stream` node, unique output ids. On failure show a
 *    notification and bail.
 * 2. **Commit locally**: serialise the cleaned draft into a
 *    workspace config, run the stream_analysis-side validation RPC,
 *    then write the workspace into `streamWorkspaces`.
 * 3. **Cascade into panels**: every panel of every stream-bound kind
 *    that lives in this workspace gets its `outputId` (and
 *    overlay/fit-overlay lists) reconciled against the new output
 *    set — outputs that no longer exist drop back to the kind's
 *    default. Bin / 2D / trace caches are cleared so the panel
 *    starts cleanly on the new wiring.
 * 4. **Push to runtime**: fire-and-forget `syncStreamAnalysisWorkspace`
 *    so the stream_analysis service learns about the update.
 *
 * **Args** (App.tsx-local handlers / memos):
 *
 * - `streamCatalogByKey` — App-local memo over the stream catalog;
 *   passed into `workspaceStreamFromGraphNodes` to look up
 *   `source.stream` nodes against the live catalog.
 * - `buildStreamAnalysisWorkspacePayload` — comes from
 *   `useWorkspaceListManagement`; used to build the validation
 *   payload sent to the runtime before committing.
 * - `syncStreamAnalysisWorkspace` — comes from
 *   `useWorkspaceListManagement`; called after commit to push the
 *   workspace to the runtime.
 * - `clearPanelBuffers` — comes from `useStreamPanelHandlers`;
 *   called for scalar panels in the cascade to drop their
 *   accumulated history when the workspace's stream/channel change.
 */

/** Where a published output comes from, and what shape it carries. */
export type OutputBinding = { nodeId: string; kind: string };

export function workspaceOutputBindings(
  workspace: StreamAnalysisWorkspaceConfig
): Map<string, OutputBinding> {
  const nodeById = workspaceNodeMap(workspace);
  const bindings = new Map<string, OutputBinding>();
  for (const output of workspace.publishOutputs) {
    const node = nodeById.get(output.nodeId);
    const kind = node ? nodeKindFromOp(node.op) : null;
    if (!kind) continue;
    bindings.set(output.outputId, { nodeId: output.nodeId, kind });
  }
  return bindings;
}

/**
 * One cache a panel keeps, and the outputs that fill it.
 *
 * Slots are per *cache*, not per panel, because a panel reads several
 * unrelated outputs into separate stores: a bin-stats card holds its
 * histogram, its trace overlays and its fit overlays independently.
 * Resetting the fit must drop the fit overlay and leave the histogram —
 * a panel-wide decision blanks the whole card whenever any one output it
 * reads is touched, which is the behaviour this replaced.
 *
 * `alwaysReset` opts a slot out of the mechanism. Only the primary trace
 * frame list needs it: `applyToPanels` *appends* there
 * (`currentFrames.push`), so a frame emitted between the runtime
 * committing the new graph and us deciding what to keep would interleave
 * with pre-apply frames inside the overlay-N window. Every other store
 * replaces its payload wholesale, so a late arrival simply wins.
 */
export type PanelCacheSlot = {
  slot:
    | "traceFrames"
    | "traceOverlay"
    | "scalar"
    | "params"
    | "bin2d"
    | "binStats"
    | "binStatsOverlay"
    | "binStatsFitOverlay";
  outputIds: string[];
  alwaysReset?: boolean;
};

export type PanelCachePlan = PanelCacheSlot[];

/**
 * Which of a slot's outputs no longer describe what it holds.
 *
 * `stateResetNodeIds` is the answer from the runtime to "which
 * accumulators did this apply throw away" - see
 * `StreamWorkspaceSyncResult`. `null` means we never got an answer, so
 * everything is stale, which is what the UI assumed unconditionally
 * before this existed.
 *
 * The per-output granularity matters for the overlay stores, which are
 * keyed by output id: one dead overlay does not cost the others.
 */
export function staleCacheOutputIds(
  slot: PanelCacheSlot,
  stateResetNodeIds: string[] | null,
  previousBindings: Map<string, OutputBinding>,
  nextBindings: Map<string, OutputBinding>
): string[] {
  if (stateResetNodeIds === null || slot.alwaysReset) return [...slot.outputIds];
  const resetNodes = new Set(stateResetNodeIds);
  return slot.outputIds.filter((outputId) => {
    const next = nextBindings.get(outputId);
    if (!next) return true;
    const previous = previousBindings.get(outputId);
    // Repointed at another node, or the node changed shape: whatever the
    // panel holds was measured against a different quantity.
    if (!previous) return true;
    if (previous.nodeId !== next.nodeId) return true;
    if (previous.kind !== next.kind) return true;
    return resetNodes.has(next.nodeId);
  });
}

export interface DaqWorkspaceApplyArgs {
  streamCatalogByKey: Map<string, StreamCatalogEntry>;
  buildStreamAnalysisWorkspacePayload: (
    workspace: StreamAnalysisWorkspaceConfig
  ) => Record<string, unknown> | null;
  syncStreamAnalysisWorkspace: (
    workspaceId: string,
    source: string
  ) => Promise<StreamWorkspaceSyncResult>;
  clearPanelBuffers: (panelId: string) => void;
}

export function useDaqWorkspaceApply(args: DaqWorkspaceApplyArgs) {
  const {
    streamCatalogByKey,
    buildStreamAnalysisWorkspacePayload,
    syncStreamAnalysisWorkspace,
    clearPanelBuffers,
  } = args;
  const {
    streamAnalysisReadyRef,
    streamWorkspacesRef,
    setStreamWorkspaces,
    daqWorkspaceId,
    daqDraftName,
    daqDraftNodes,
    daqDraftOutputs,
    daqDraftEnabled,
  } = useStreamAnalysis();
  const { setPanels } = usePanels();
  const {
    streamFramesRef,
    streamTraceOverlayRef,
    streamParamsLatestRef,
    streamBin2dRef,
    streamBinStatsRef,
    streamBinStatsOverlayRef,
    streamBinStatsFitOverlayRef,
  } = useTelemetry();

  const clearPanelCacheSlot = (
    panelId: string,
    slot: PanelCacheSlot["slot"],
    staleOutputIds: string[]
  ) => {
    switch (slot) {
      case "traceFrames":
        streamFramesRef.set(panelId, []);
        return;
      case "traceOverlay": {
        const overlays = streamTraceOverlayRef.get(panelId);
        for (const outputId of staleOutputIds) overlays?.delete(outputId);
        return;
      }
      case "scalar":
        clearPanelBuffers(panelId);
        return;
      case "params": {
        // Keyed by output id like the overlay maps, so drop only the
        // entries whose source actually changed.
        const latest = streamParamsLatestRef.get(panelId);
        if (latest) {
          for (const outputId of staleOutputIds) delete latest[outputId];
        }
        return;
      }
      case "bin2d":
        streamBin2dRef.delete(panelId);
        return;
      case "binStats":
        streamBinStatsRef.delete(panelId);
        return;
      case "binStatsOverlay": {
        const overlays = streamBinStatsOverlayRef.get(panelId);
        for (const outputId of staleOutputIds) overlays?.delete(outputId);
        return;
      }
      case "binStatsFitOverlay": {
        const overlays = streamBinStatsFitOverlayRef.get(panelId);
        for (const outputId of staleOutputIds) overlays?.delete(outputId);
        return;
      }
    }
  };

  const applyDaqWorkspace = async () => {
    const workspaceId = String(daqWorkspaceId ?? "").trim();
    if (!workspaceId) {
      return;
    }
    const current = streamWorkspacesRef.current[workspaceId];
    if (!current) {
      return;
    }

    const name = daqDraftName.trim() || defaultStreamWorkspaceName(workspaceId);
    const cleanedNodes = daqDraftNodes
      .map((node) => normalizeDagNode(node))
      .filter((node): node is StreamDagNodeConfig => node !== null);
    if (cleanedNodes.length <= 0) {
      notifications.show({
        color: "red",
        title: "Invalid graph",
        message: "At least one node is required.",
      });
      return;
    }
    const nodeIds = cleanedNodes.map((node) => node.nodeId);
    const uniqueNodeIds = new Set(nodeIds);
    if (uniqueNodeIds.size !== nodeIds.length) {
      notifications.show({
        color: "red",
        title: "Invalid graph",
        message: "Node IDs must be unique and non-empty.",
      });
      return;
    }
    const sourceStreamCount = cleanedNodes.filter(
      (node) => node.op === "source.stream"
    ).length;
    if (sourceStreamCount !== 1) {
      notifications.show({
        color: "red",
        title: "Invalid graph",
        message: "Graph must include exactly one source.stream node.",
      });
      return;
    }

    const cleanedOutputs = daqDraftOutputs
      .map((output) => normalizeDagOutput(output))
      .filter((output): output is StreamDagOutputConfig => output !== null)
      .filter((output) => uniqueNodeIds.has(output.nodeId));
    const outputIds = cleanedOutputs.map((output) => output.outputId);
    const uniqueOutputIds = new Set(outputIds);
    if (uniqueOutputIds.size !== outputIds.length) {
      notifications.show({
        color: "red",
        title: "Invalid outputs",
        message: "Output IDs must be unique and non-empty.",
      });
      return;
    }

    const derivedSource = workspaceStreamFromGraphNodes(
      cleanedNodes,
      streamCatalogByKey
    );
    const updated: StreamAnalysisWorkspaceConfig = {
      ...current,
      workspaceId,
      name,
      stream: derivedSource.stream,
      channelIndex: derivedSource.channelIndex,
      graphNodes: cloneDagNodes(cleanedNodes),
      publishOutputs: cloneDagOutputs(cleanedOutputs),
      enabled: daqDraftEnabled !== false,
    };
    const validatePayload = buildStreamAnalysisWorkspacePayload(updated);
    if (streamAnalysisReadyRef.current && validatePayload) {
      const validation = await validateStreamWorkspace(
        workspaceId,
        validatePayload
      );
      if (!validation.ok) {
        notifications.show({
          color: "red",
          title: "Invalid DAG workspace",
          message:
            validation.error?.message ??
            validation.error?.code ??
            "workspace validation failed",
        });
        return;
      }
    }
    setStreamWorkspaces((prev) => ({ ...prev, [workspaceId]: updated }));
    streamWorkspacesRef.current = {
      ...streamWorkspacesRef.current,
      [workspaceId]: updated,
    };
    const scalarOutputIds = new Set(
      workspaceOutputOptionsByKind(updated, "scalar").map((item) => item.value)
    );
    const paramsMapOutputIds = new Set(
      workspaceOutputOptionsByKind(updated, "params_map").map(
        (item) => item.value
      )
    );
    const traceOutputIds = new Set(
      workspaceOutputOptionsByKind(updated, "trace").map((item) => item.value)
    );
    const histOutputIds = new Set(
      workspaceOutputOptionsByKind(updated, "hist_agg").map((item) => item.value)
    );
    const fitOutputIds = new Set(
      workspaceOutputOptionsByKind(updated, "fit_1d").map((item) => item.value)
    );
    const hist2dOutputIds = new Set(
      workspaceOutputOptionsByKind(updated, "hist2d").map((item) => item.value)
    );
    const previousBindings = workspaceOutputBindings(current);
    const nextBindings = workspaceOutputBindings(updated);
    const dirtyPanelIds = new Set<string>();
    // Which panels *might* have to drop their caches. That decision needs
    // the answer from the runtime, which only arrives after the sync
    // below, so the clearing deliberately does not happen in this updater
    // - a state updater must not have side effects anyway.
    const cachePlans = new Map<string, PanelCachePlan>();
    setPanels((prev) =>
      prev.map((panel) => {
        if (
          !isStreamTracePanel(panel) &&
          !isStreamScalarPanel(panel) &&
          !isStreamParamsPanel(panel) &&
          !isStreamBinStatsPanel(panel) &&
          !isStreamBin2dPanel(panel)
        ) {
          return panel;
        }
        if (panel.workspaceId !== workspaceId) {
          return panel;
        }
        dirtyPanelIds.add(panel.id);
        if (isStreamTracePanel(panel)) {
          if (panel.sourceMode !== "dag") {
            return panel;
          }
          const nextOutputId =
            panel.outputId && traceOutputIds.has(panel.outputId)
              ? panel.outputId
              : defaultOutputForKind(updated, "trace");
          const overlayOutputIds = (panel.overlayOutputIds ?? []).filter(
            (id) => id !== nextOutputId && traceOutputIds.has(id)
          );
          cachePlans.set(panel.id, [
            {
              slot: "traceFrames",
              outputIds: [nextOutputId ?? ""],
              alwaysReset: true,
            },
            { slot: "traceOverlay", outputIds: overlayOutputIds },
          ]);
          return {
            ...panel,
            outputId: nextOutputId,
            overlayOutputIds,
            stream: updated.stream,
            channelIndex: updated.channelIndex,
          };
        }
        if (isStreamScalarPanel(panel)) {
          const nextOutputId =
            panel.outputId && scalarOutputIds.has(panel.outputId)
              ? panel.outputId
              : defaultOutputForKind(updated, "scalar");
          cachePlans.set(panel.id, [
            { slot: "scalar", outputIds: [nextOutputId ?? ""] },
          ]);
          return {
            ...panel,
            outputId: nextOutputId,
            stream: updated.stream,
            channelIndex: updated.channelIndex,
            analysis: updated.analysis,
          };
        }
        if (isStreamParamsPanel(panel)) {
          const nextOutputIds = (panel.outputIds ?? []).filter(
            (id) => scalarOutputIds.has(id) || paramsMapOutputIds.has(id)
          );
          cachePlans.set(panel.id, [
            { slot: "params", outputIds: nextOutputIds },
          ]);
          return {
            ...panel,
            outputIds: nextOutputIds,
          };
        }
        if (isStreamBin2dPanel(panel)) {
          const nextOutputId =
            panel.outputId && hist2dOutputIds.has(panel.outputId)
              ? panel.outputId
              : defaultOutputForKind(updated, "hist2d");
          cachePlans.set(panel.id, [
            { slot: "bin2d", outputIds: [nextOutputId ?? ""] },
          ]);
          return {
            ...panel,
            outputId: nextOutputId,
          };
        }
        const nextOutputId =
          panel.outputId && histOutputIds.has(panel.outputId)
            ? panel.outputId
            : defaultOutputForKind(updated, "hist_agg");
        const nextOverlayOutputIds = (panel.overlayOutputIds ?? []).filter(
          (id) => traceOutputIds.has(id)
        );
        const nextFitOverlayOutputIds = (
          panel.fitOverlayOutputIds ?? []
        ).filter((id) => fitOutputIds.has(id));
        // Three independent stores. Changing the fit must not cost the
        // histogram, and vice versa.
        cachePlans.set(panel.id, [
          { slot: "binStats", outputIds: [nextOutputId ?? ""] },
          { slot: "binStatsOverlay", outputIds: nextOverlayOutputIds },
          { slot: "binStatsFitOverlay", outputIds: nextFitOverlayOutputIds },
        ]);
        return {
          ...panel,
          outputId: nextOutputId,
          overlayOutputIds: nextOverlayOutputIds,
          fitOverlayOutputIds: nextFitOverlayOutputIds,
          stream: updated.stream,
          channelIndex: updated.channelIndex,
          analysis: updated.analysis,
          binStats: updated.binStats,
        };
      })
    );
    markPanelsDirty(dirtyPanelIds);

    const sync = await syncStreamAnalysisWorkspace(
      workspaceId,
      "stream-workspace-apply"
    );
    for (const [panelId, plan] of cachePlans) {
      for (const slot of plan) {
        const stale = staleCacheOutputIds(
          slot,
          sync.stateResetNodeIds,
          previousBindings,
          nextBindings
        );
        if (stale.length === 0) continue;
        clearPanelCacheSlot(panelId, slot.slot, stale);
      }
    }
    markPanelsDirty(dirtyPanelIds);
  };

  return { applyDaqWorkspace };
}
