import { notifications } from "@mantine/notifications";

import {
  reloadStreamWorkspaceStore,
  resetStreamWorkspace,
  saveStreamWorkspaceStore,
} from "../../api";
import { normalizeWorkspaceStoreStatus } from "../stream/workspace";
import { useStreamAnalysis } from "./StreamAnalysisContext";

/**
 * Workspace-store CRUD + node-aggregate reset.
 *
 * Three async actions that touch the runtime workspace store (the
 * on-disk YAML the stream_analysis process loads from) plus one
 * per-node aggregate reset. All four share the
 * `workspaceStoreBusyAction` / `daqResetNodeBusyId` lockouts so the
 * UI can disable buttons while a save/reload/reset is in flight.
 *
 * **Handlers**:
 *
 * - `resetDaqNodeAggregate(nodeId)` — clears one node's accumulated
 *   aggregate state in the live workspace, then clears any
 *   bin-panel UI state associated with that node.
 * - `saveDaqWorkspaceStore()` — persists the current runtime
 *   workspaces to disk; refreshes the workspace-store status from
 *   the response (or with a follow-up status fetch).
 * - `reloadDaqWorkspaceStore()` — re-reads the on-disk YAML into the
 *   runtime, then triggers a `loadStreamAnalysisWorkspaces` to
 *   re-hydrate the UI from the new runtime state.
 *
 * **Args** (App.tsx-local handlers):
 *
 * - `clearWorkspaceBinPanels` — comes from `useStreamPanelHandlers`;
 *   `resetDaqNodeAggregate` calls it after the node aggregate is
 *   cleared.
 * - `refreshWorkspaceStoreStatus` — App-local; called as a fallback
 *   after save/reload so the displayed store status is fresh.
 * - `loadStreamAnalysisWorkspaces` — App-local; `reloadDaqWorkspaceStore`
 *   calls it to re-hydrate the workspace list after the reload.
 */

export interface WorkspaceStoreActionsArgs {
  clearWorkspaceBinPanels: (
    workspaceId: string,
    nodeId: string
  ) => void;
  refreshWorkspaceStoreStatus: (
    source: string,
    options?: { notifyOnError?: boolean }
  ) => Promise<unknown>;
  loadStreamAnalysisWorkspaces: (
    source: string,
    options?: { notifyOnError?: boolean }
  ) => Promise<unknown>;
}

export function useWorkspaceStoreActions(args: WorkspaceStoreActionsArgs) {
  const {
    clearWorkspaceBinPanels,
    refreshWorkspaceStoreStatus,
    loadStreamAnalysisWorkspaces,
  } = args;
  const {
    streamAnalysisReadyRef,
    daqWorkspaceId,
    daqResetNodeBusyId,
    setDaqResetNodeBusyId,
    workspaceStoreBusyAction,
    setWorkspaceStoreBusyAction,
    workspaceStoreStatus,
    setWorkspaceStoreStatus,
  } = useStreamAnalysis();

  const resetDaqNodeAggregate = async (nodeId: string) => {
    const workspaceId = String(daqWorkspaceId ?? "").trim();
    const normalizedNodeId = String(nodeId ?? "").trim();
    if (!workspaceId || !normalizedNodeId || daqResetNodeBusyId !== null) {
      return;
    }
    if (!streamAnalysisReadyRef.current) {
      notifications.show({
        color: "yellow",
        title: "Stream analysis unavailable",
        message: "Start the stream_analysis process first.",
      });
      return;
    }
    setDaqResetNodeBusyId(normalizedNodeId);
    try {
      const resp = await resetStreamWorkspace(workspaceId, normalizedNodeId);
      if (!resp.ok) {
        notifications.show({
          color: "red",
          title: "Node reset failed",
          message:
            resp.error?.message ?? resp.error?.code ?? "workspace.reset failed",
        });
        return;
      }
      clearWorkspaceBinPanels(workspaceId, normalizedNodeId);
      notifications.show({
        color: "teal",
        title: "Node aggregate cleared",
        message: `${workspaceId}.${normalizedNodeId}`,
      });
    } finally {
      setDaqResetNodeBusyId(null);
    }
  };

  const saveDaqWorkspaceStore = async () => {
    if (!streamAnalysisReadyRef.current || workspaceStoreBusyAction !== null) {
      return;
    }
    setWorkspaceStoreBusyAction("save");
    try {
      const resp = await saveStreamWorkspaceStore();
      if (!resp.ok) {
        notifications.show({
          color: "red",
          title: "Workspace save failed",
          message:
            resp.error?.message ??
            resp.error?.code ??
            "workspace_store.save failed",
        });
        await refreshWorkspaceStoreStatus("workspace-save", {
          notifyOnError: false,
        });
        return;
      }
      const resultObj =
        resp.result && typeof resp.result === "object"
          ? (resp.result as Record<string, unknown>)
          : {};
      const statusRaw =
        resultObj.status && typeof resultObj.status === "object"
          ? resultObj.status
          : null;
      if (statusRaw) {
        setWorkspaceStoreStatus(normalizeWorkspaceStoreStatus(statusRaw));
      } else {
        await refreshWorkspaceStoreStatus("workspace-save", {
          notifyOnError: false,
        });
      }
      notifications.show({
        color: "teal",
        title: "Workspace file saved",
        message:
          (statusRaw && typeof (statusRaw as { path?: unknown }).path === "string"
            ? String((statusRaw as { path?: unknown }).path)
            : workspaceStoreStatus.path) ?? "workspace store updated",
      });
    } finally {
      setWorkspaceStoreBusyAction(null);
    }
  };

  const reloadDaqWorkspaceStore = async () => {
    if (!streamAnalysisReadyRef.current || workspaceStoreBusyAction !== null) {
      return;
    }
    setWorkspaceStoreBusyAction("reload");
    try {
      const resp = await reloadStreamWorkspaceStore();
      if (!resp.ok) {
        notifications.show({
          color: "red",
          title: "Workspace reload failed",
          message:
            resp.error?.message ??
            resp.error?.code ??
            "workspace_store.reload failed",
        });
        await refreshWorkspaceStoreStatus("workspace-reload", {
          notifyOnError: false,
        });
        return;
      }
      const resultObj =
        resp.result && typeof resp.result === "object"
          ? (resp.result as Record<string, unknown>)
          : {};
      const statusRaw =
        resultObj.status && typeof resultObj.status === "object"
          ? resultObj.status
          : null;
      if (statusRaw) {
        setWorkspaceStoreStatus(normalizeWorkspaceStoreStatus(statusRaw));
      } else {
        await refreshWorkspaceStoreStatus("workspace-reload", {
          notifyOnError: false,
        });
      }
      await loadStreamAnalysisWorkspaces("workspace-reload", {
        notifyOnError: false,
      });
      notifications.show({
        color: "teal",
        title: "Workspace file reloaded",
        message: "Runtime DAG workspaces refreshed from disk.",
      });
    } finally {
      setWorkspaceStoreBusyAction(null);
    }
  };

  return {
    resetDaqNodeAggregate,
    saveDaqWorkspaceStore,
    reloadDaqWorkspaceStore,
  };
}
