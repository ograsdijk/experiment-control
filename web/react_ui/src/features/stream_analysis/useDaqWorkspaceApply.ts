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
  normalizeDagNode,
  normalizeDagOutput,
} from "../stream/dag";
import type {
  StreamAnalysisWorkspaceConfig,
  StreamCatalogEntry,
  StreamDagNodeConfig,
  StreamDagOutputConfig,
} from "../stream/types";
import {
  defaultOutputForKind,
  defaultStreamWorkspaceName,
  workspaceOutputOptionsByKind,
  workspaceStreamFromGraphNodes,
} from "../stream/workspace";
import { useTelemetry } from "../telemetry/TelemetryContext";
import { useStreamAnalysis } from "./StreamAnalysisContext";

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

export interface DaqWorkspaceApplyArgs {
  streamCatalogByKey: Map<string, StreamCatalogEntry>;
  buildStreamAnalysisWorkspacePayload: (
    workspace: StreamAnalysisWorkspaceConfig
  ) => Record<string, unknown> | null;
  syncStreamAnalysisWorkspace: (
    workspaceId: string,
    source: string
  ) => Promise<void>;
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
  const { setPanels, setPlotTick } = usePanels();
  const {
    streamFramesRef,
    streamTraceOverlayRef,
    streamParamsLatestRef,
    streamBin2dRef,
    streamBinStatsRef,
    streamBinStatsOverlayRef,
    streamBinStatsFitOverlayRef,
  } = useTelemetry();

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
    const nodeIds = cleanedNodes.map((node) => node.id);
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
        if (isStreamTracePanel(panel)) {
          if (panel.sourceMode !== "dag") {
            return panel;
          }
          const nextOutputId =
            panel.outputId && traceOutputIds.has(panel.outputId)
              ? panel.outputId
              : defaultOutputForKind(updated, "trace");
          streamFramesRef.set(panel.id, []);
          streamTraceOverlayRef.set(panel.id, new Map());
          const overlayOutputIds = (panel.overlayOutputIds ?? []).filter(
            (id) => id !== nextOutputId && traceOutputIds.has(id)
          );
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
          clearPanelBuffers(panel.id);
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
          streamParamsLatestRef.set(panel.id, {});
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
          streamBin2dRef.delete(panel.id);
          return {
            ...panel,
            outputId: nextOutputId,
          };
        }
        const nextOutputId =
          panel.outputId && histOutputIds.has(panel.outputId)
            ? panel.outputId
            : defaultOutputForKind(updated, "hist_agg");
        streamBinStatsRef.delete(panel.id);
        streamBinStatsOverlayRef.set(panel.id, new Map());
        streamBinStatsFitOverlayRef.set(panel.id, new Map());
        const nextOverlayOutputIds = (panel.overlayOutputIds ?? []).filter(
          (id) => traceOutputIds.has(id)
        );
        const nextFitOverlayOutputIds = (
          panel.fitOverlayOutputIds ?? []
        ).filter((id) => fitOutputIds.has(id));
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
    setPlotTick((tick) => tick + 1);
    void syncStreamAnalysisWorkspace(workspaceId, "stream-workspace-apply");
  };

  return { applyDaqWorkspace };
}
