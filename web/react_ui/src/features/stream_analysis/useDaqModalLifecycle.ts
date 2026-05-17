import { notifications } from "@mantine/notifications";

import { cloneDagNodes, cloneDagOutputs } from "../stream/dag";
import { defaultStreamAnalysisWorkspaceConfig } from "../stream/workspace";
import { useStreamAnalysis } from "./StreamAnalysisContext";

/**
 * DAQ workspace modal lifecycle — open / close / create / load / focus.
 *
 * These handlers manage the DAQ workspace editor modal's open state
 * plus the draft state hydration when the user switches between
 * workspaces inside the modal. The actual node/output editing lives
 * in `useDaqDraftEditors`; the validate + commit flow lives in
 * `applyDaqWorkspace` (still in App.tsx for now).
 *
 * **Handlers**:
 *
 * - `loadDaqWorkspaceDraft(workspaceId)` — populates the draft state
 *   from a workspace already in `streamWorkspaces`, used both on
 *   modal open and when the user picks a different workspace from
 *   the dropdown.
 * - `createStreamWorkspace()` — mints a fresh workspace with a
 *   numbered id + `Workspace N` name, registers it in
 *   `streamWorkspaces`, and loads its draft into the editor.
 * - `openDaqModal(workspaceId?)` — refreshes the workspace list from
 *   the runtime, picks a workspace (preferred → current → first
 *   known), hydrates the draft, and opens the modal. Creates a
 *   workspace if none exist.
 * - `closeDaqModal()` — closes the modal and clears any pending
 *   focus-highlight timeout.
 * - `focusDaqNodeCard(nodeId)` — scrolls the named node card into
 *   view + briefly highlights it (auto-clears after 1.6 s).
 *
 * **Args** (App.tsx-local state):
 *
 * - `loadStreamAnalysisWorkspaces` — the async workspace-list
 *   refresh routine that still lives in App.tsx. `openDaqModal`
 *   triggers a refresh before deciding which workspace to load so
 *   the modal opens with the latest server-side workspaces.
 */

export interface DaqModalLifecycleArgs {
  loadStreamAnalysisWorkspaces: (
    source: string,
    options?: { notifyOnError?: boolean }
  ) => Promise<unknown>;
}

export function useDaqModalLifecycle(args: DaqModalLifecycleArgs) {
  const { loadStreamAnalysisWorkspaces } = args;
  const {
    streamWorkspacesRef,
    streamWorkspaceIdRef,
    setStreamWorkspaces,
    streamAnalysisReadyRef,
    daqWorkspaceId,
    setDaqWorkspaceId,
    setDaqDraftName,
    setDaqDraftNodes,
    setDaqDraftOutputs,
    setDaqDraftEnabled,
    setDaqFocusedNodeId,
    daqNodeCardRefs,
    daqNodeFocusTimeoutRef,
    setDaqOpen,
  } = useStreamAnalysis();

  const focusDaqNodeCard = (nodeId: string) => {
    const normalizedNodeId = String(nodeId ?? "").trim();
    if (!normalizedNodeId) {
      return;
    }
    const card = daqNodeCardRefs.current.get(normalizedNodeId);
    if (card) {
      card.scrollIntoView({
        behavior: "smooth",
        block: "center",
        inline: "nearest",
      });
    }
    setDaqFocusedNodeId(normalizedNodeId);
    if (daqNodeFocusTimeoutRef.current !== null) {
      window.clearTimeout(daqNodeFocusTimeoutRef.current);
      daqNodeFocusTimeoutRef.current = null;
    }
    daqNodeFocusTimeoutRef.current = window.setTimeout(() => {
      setDaqFocusedNodeId((current) =>
        current === normalizedNodeId ? null : current
      );
      daqNodeFocusTimeoutRef.current = null;
    }, 1600);
  };

  const loadDaqWorkspaceDraft = (workspaceId: string | null) => {
    const id = String(workspaceId ?? "").trim();
    if (!id) {
      return;
    }
    const workspace = streamWorkspacesRef.current[id];
    if (!workspace) {
      return;
    }
    setDaqWorkspaceId(id);
    setDaqDraftName(workspace.name);
    setDaqDraftNodes(cloneDagNodes(workspace.graphNodes));
    setDaqDraftOutputs(cloneDagOutputs(workspace.publishOutputs));
    setDaqDraftEnabled(workspace.enabled !== false);
    setDaqFocusedNodeId(null);
    daqNodeCardRefs.current.clear();
  };

  const createStreamWorkspace = () => {
    const nextId = Math.max(1, Math.trunc(streamWorkspaceIdRef.current));
    const workspaceId = `workspace-${nextId}`;
    streamWorkspaceIdRef.current = nextId + 1;
    const workspace = defaultStreamAnalysisWorkspaceConfig(workspaceId);
    workspace.name = `Workspace ${nextId}`;
    setStreamWorkspaces((prev) => ({ ...prev, [workspaceId]: workspace }));
    streamWorkspacesRef.current = {
      ...streamWorkspacesRef.current,
      [workspaceId]: workspace,
    };
    loadDaqWorkspaceDraft(workspaceId);
  };

  const openDaqModal = async (workspaceId?: string | null) => {
    if (!streamAnalysisReadyRef.current) {
      notifications.show({
        color: "yellow",
        title: "stream_analysis not running",
        message: "Start the stream_analysis process first.",
      });
      return;
    }
    if (streamAnalysisReadyRef.current) {
      await loadStreamAnalysisWorkspaces("daq-modal-open", {
        notifyOnError: false,
      });
    }
    const preferred = String(workspaceId ?? "").trim();
    const knownIds = Object.keys(streamWorkspacesRef.current).sort();
    if (knownIds.length === 0) {
      createStreamWorkspace();
      setDaqOpen(true);
      return;
    }
    const nextId =
      (preferred && streamWorkspacesRef.current[preferred] ? preferred : null) ??
      daqWorkspaceId ??
      knownIds[0];
    loadDaqWorkspaceDraft(nextId);
    setDaqOpen(true);
  };

  const closeDaqModal = () => {
    setDaqOpen(false);
    setDaqFocusedNodeId(null);
    if (daqNodeFocusTimeoutRef.current !== null) {
      window.clearTimeout(daqNodeFocusTimeoutRef.current);
      daqNodeFocusTimeoutRef.current = null;
    }
  };

  return {
    focusDaqNodeCard,
    loadDaqWorkspaceDraft,
    createStreamWorkspace,
    openDaqModal,
    closeDaqModal,
  };
}
