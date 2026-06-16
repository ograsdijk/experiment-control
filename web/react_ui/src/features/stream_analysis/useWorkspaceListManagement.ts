import { notifications } from "@mantine/notifications";

import {
  deleteStreamWorkspace,
  fetchStreamWorkspace,
  fetchStreamWorkspaceList,
  fetchStreamWorkspaceStoreStatus,
  putStreamWorkspace,
} from "../../api";
import { STREAM_DAG_OPS, coerceDagParamValue } from "../stream/dag";
import type { StreamAnalysisWorkspaceConfig } from "../stream/types";
import {
  nextWorkspaceCounter,
  normalizeStreamWorkspaceRecord,
  normalizeWorkspaceStoreStatus,
  normalizeWorkspaceSummaries,
} from "../stream/workspace";
import { useStreamAnalysis } from "./StreamAnalysisContext";

/**
 * Workspace-list management — refresh / load / delete / sync against
 * the stream_analysis runtime.
 *
 * These four async handlers (plus the `buildStreamAnalysisWorkspacePayload`
 * helper) own the round-trip between the React workspace state and
 * the stream_analysis service's authoritative workspace registry.
 *
 * **Handlers**:
 *
 * - `refreshWorkspaceStoreStatus(source, options?)` — fetches the
 *   workspace-store status (path / dirty flag / etc.) from the
 *   runtime and writes it into context.
 * - `loadStreamAnalysisWorkspaces(source, options?)` — pulls the
 *   workspace list, then fetches each workspace's raw config,
 *   normalises into the context shape, and updates revisions +
 *   workspace-id counter. Falls back to summary data if a per-id
 *   fetch fails.
 * - `deleteStreamAnalysisWorkspace(workspaceId, source)` — removes
 *   a workspace from the runtime (and from the revisions map). On
 *   revision conflicts it re-loads the workspace list so the next
 *   action sees fresh revisions.
 * - `syncStreamAnalysisWorkspace(workspaceId, source)` — uploads
 *   the current React-side workspace to the runtime. If the
 *   workspace was deleted client-side or has no graph it issues a
 *   delete instead. Updates the post-sync state from the response.
 *
 * **Helper**: `buildStreamAnalysisWorkspacePayload(workspace)` —
 * serializes the React-side workspace config into the
 * stream_analysis-RPC shape (op specs filled in via STREAM_DAG_OPS,
 * params coerced to their declared kinds, optional empty params
 * dropped). Returned alongside the handlers so other consumers
 * (e.g. `applyDaqWorkspace` in App.tsx) can build payloads without
 * re-implementing the serialisation.
 */
export function useWorkspaceListManagement() {
  const {
    streamAnalysisReadyRef,
    setWorkspaceStoreStatus,
    streamWorkspacesRef,
    setStreamWorkspaces,
    streamWorkspaceRevisionsRef,
    setStreamWorkspaceRevisions,
    streamWorkspaceIdRef,
  } = useStreamAnalysis();

  const refreshWorkspaceStoreStatus = async (
    source: string,
    options?: { notifyOnError?: boolean }
  ) => {
    if (!streamAnalysisReadyRef.current) {
      setWorkspaceStoreStatus(normalizeWorkspaceStoreStatus(null));
      return;
    }
    const resp = await fetchStreamWorkspaceStoreStatus();
    if (!resp.ok) {
      if (options?.notifyOnError) {
        notifications.show({
          color: "red",
          title: "Workspace store status failed",
          message: `${source}: ${
            resp.error?.message ??
            resp.error?.code ??
            "workspace_store.status failed"
          }`,
        });
      }
      return;
    }
    setWorkspaceStoreStatus(normalizeWorkspaceStoreStatus(resp.result));
  };

  const loadStreamAnalysisWorkspaces = async (
    source: string,
    options?: { notifyOnError?: boolean }
  ): Promise<boolean> => {
    if (!streamAnalysisReadyRef.current) {
      return false;
    }
    const listResp = await fetchStreamWorkspaceList();
    if (!listResp.ok) {
      if (options?.notifyOnError) {
        notifications.show({
          color: "red",
          title: "Workspace load failed",
          message: `${source}: ${
            listResp.error?.message ??
            listResp.error?.code ??
            "workspace.list failed"
          }`,
        });
      }
      return false;
    }
    const listRaw = Array.isArray(listResp.result?.workspaces)
      ? listResp.result.workspaces
      : [];
    const summaries = normalizeWorkspaceSummaries(listRaw);
    const summaryById = new Map<string, Record<string, unknown>>();
    for (const item of listRaw) {
      if (!item || typeof item !== "object") {
        continue;
      }
      const obj = item as Record<string, unknown>;
      const workspaceId = String(obj.workspace_id ?? "").trim();
      if (!workspaceId) {
        continue;
      }
      summaryById.set(workspaceId, obj);
    }
    if (
      summaries.length === 0 &&
      Object.keys(streamWorkspacesRef.current).length > 0
    ) {
      await refreshWorkspaceStoreStatus(source, { notifyOnError: false });
      return true;
    }
    const rawRecord: Record<string, unknown> = {};
    await Promise.all(
      summaries.map(async (summary) => {
        const getResp = await fetchStreamWorkspace(summary.workspaceId);
        if (getResp.ok && getResp.result && typeof getResp.result === "object") {
          const raw = (getResp.result as { raw?: unknown }).raw;
          if (raw && typeof raw === "object") {
            rawRecord[summary.workspaceId] = raw as Record<string, unknown>;
            return;
          }
        }
        const fallback = summaryById.get(summary.workspaceId);
        if (!fallback) {
          return;
        }
        const graph =
          fallback.graph && typeof fallback.graph === "object"
            ? (fallback.graph as Record<string, unknown>)
            : {};
        const publish =
          fallback.publish && typeof fallback.publish === "object"
            ? (fallback.publish as Record<string, unknown>)
            : {};
        rawRecord[summary.workspaceId] = {
          workspace_id: summary.workspaceId,
          name:
            typeof fallback.name === "string" && fallback.name.trim().length > 0
              ? fallback.name.trim()
              : undefined,
          enabled: fallback.enabled !== false,
          graph,
          publish,
        };
      })
    );
    const normalized = normalizeStreamWorkspaceRecord(rawRecord);
    const revisions: Record<string, number> = {};
    for (const summary of summaries) {
      if (normalized[summary.workspaceId]) {
        revisions[summary.workspaceId] = summary.revision;
      }
    }
    setStreamWorkspaces(normalized);
    streamWorkspacesRef.current = normalized;
    setStreamWorkspaceRevisions(revisions);
    streamWorkspaceRevisionsRef.current = revisions;
    streamWorkspaceIdRef.current = nextWorkspaceCounter(normalized);
    await refreshWorkspaceStoreStatus(source, { notifyOnError: false });
    return true;
  };

  const deleteStreamAnalysisWorkspace = async (
    workspaceId: string,
    source: string
  ) => {
    if (!streamAnalysisReadyRef.current) {
      return;
    }
    const expectedRevision =
      streamWorkspaceRevisionsRef.current[workspaceId] ?? null;
    const resp = await deleteStreamWorkspace(workspaceId, expectedRevision);
    if (resp.ok) {
      setStreamWorkspaceRevisions((prev) => {
        if (!(workspaceId in prev)) {
          return prev;
        }
        const next = { ...prev };
        delete next[workspaceId];
        streamWorkspaceRevisionsRef.current = next;
        return next;
      });
      await refreshWorkspaceStoreStatus(source, { notifyOnError: false });
      return;
    }
    if (String(resp.error?.code ?? "").toLowerCase() === "revision_conflict") {
      notifications.show({
        color: "yellow",
        title: "Workspace changed elsewhere",
        message: `${source}: Reloaded latest workspace state.`,
      });
      await loadStreamAnalysisWorkspaces(source, { notifyOnError: false });
      return;
    }
    if (String(resp.error?.code ?? "").toLowerCase() !== "unknown_workspace") {
      notifications.show({
        color: "red",
        title: "stream_analysis sync failed",
        message: `${source}: ${
          resp.error?.message ?? "workspace.delete failed"
        }`,
      });
    }
  };

  const buildStreamAnalysisWorkspacePayload = (
    workspace: StreamAnalysisWorkspaceConfig
  ): Record<string, unknown> | null => {
    const graphNodes = Array.isArray(workspace.graphNodes)
      ? workspace.graphNodes
      : [];
    if (graphNodes.length <= 0) {
      return null;
    }
    const nodes: Array<Record<string, unknown>> = graphNodes.map((node) => {
      const spec = STREAM_DAG_OPS[node.op];
      const params: Record<string, unknown> = {};
      for (const field of spec.params) {
        const raw = node.params[field.name];
        const coerced = coerceDagParamValue(raw, field.kind);
        if (
          field.optional &&
          (coerced === "" || coerced === null || coerced === undefined)
        ) {
          continue;
        }
        params[field.name] = coerced;
      }
      const inputs: Record<string, unknown> = {};
      const allInputPorts = [...spec.inputs, ...(spec.optionalInputs ?? [])];
      for (const port of allInputPorts) {
        const sourceNodeId = String(node.inputs[port] ?? "").trim();
        if (sourceNodeId) {
          inputs[port] = sourceNodeId;
        }
      }
      const out: Record<string, unknown> = {
        node_id: node.nodeId,
        op: node.op,
        params,
      };
      if (allInputPorts.length > 0) {
        out.inputs = inputs;
      }
      return out;
    });

    const outputs = (
      Array.isArray(workspace.publishOutputs) ? workspace.publishOutputs : []
    )
      .map((output) => ({
        output_id: String(output.outputId ?? "").trim(),
        node_id: String(output.nodeId ?? "").trim(),
      }))
      .filter((output) => output.output_id && output.node_id);

    return {
      workspace_id: workspace.workspaceId,
      name: workspace.name,
      enabled: workspace.enabled !== false,
      graph: { nodes },
      publish: { outputs },
    };
  };

  const syncStreamAnalysisWorkspace = async (
    workspaceId: string,
    source: string
  ) => {
    if (!streamAnalysisReadyRef.current) {
      return;
    }
    const workspaceConfig = streamWorkspacesRef.current[workspaceId];
    if (!workspaceConfig) {
      await deleteStreamAnalysisWorkspace(workspaceId, source);
      return;
    }
    const workspace = buildStreamAnalysisWorkspacePayload(workspaceConfig);
    if (!workspace) {
      await deleteStreamAnalysisWorkspace(workspaceConfig.workspaceId, source);
      return;
    }
    const expectedRevision = Object.prototype.hasOwnProperty.call(
      streamWorkspaceRevisionsRef.current,
      workspaceConfig.workspaceId
    )
      ? streamWorkspaceRevisionsRef.current[workspaceConfig.workspaceId]
      : 0;
    const resp = await putStreamWorkspace(
      workspaceConfig.workspaceId,
      workspace,
      expectedRevision
    );
    if (
      !resp.ok &&
      String(resp.error?.code ?? "").toLowerCase() === "revision_conflict"
    ) {
      notifications.show({
        color: "yellow",
        title: "Workspace changed elsewhere",
        message: `${source}: Reloaded latest workspace state.`,
      });
      await loadStreamAnalysisWorkspaces(source, { notifyOnError: false });
      return;
    }
    if (!resp.ok) {
      notifications.show({
        color: "red",
        title: "stream_analysis sync failed",
        message: `${source}: ${resp.error?.message ?? "workspace.put failed"}`,
      });
      return;
    }
    const resultObj =
      resp.result && typeof resp.result === "object"
        ? (resp.result as Record<string, unknown>)
        : {};
    const raw =
      resultObj.raw && typeof resultObj.raw === "object"
        ? ({ [workspaceConfig.workspaceId]: resultObj.raw } as Record<
            string,
            unknown
          >)
        : ({ [workspaceConfig.workspaceId]: workspace } as Record<
            string,
            unknown
          >);
    const normalized = normalizeStreamWorkspaceRecord(raw);
    const nextWorkspace = normalized[workspaceConfig.workspaceId];
    if (nextWorkspace) {
      setStreamWorkspaces((prev) => ({
        ...prev,
        [workspaceConfig.workspaceId]: nextWorkspace,
      }));
      streamWorkspacesRef.current = {
        ...streamWorkspacesRef.current,
        [workspaceConfig.workspaceId]: nextWorkspace,
      };
    }
    const summaryRaw =
      resultObj.workspace && typeof resultObj.workspace === "object"
        ? (resultObj.workspace as Record<string, unknown>)
        : null;
    if (summaryRaw) {
      const summaries = normalizeWorkspaceSummaries([summaryRaw]);
      const summary = summaries[0];
      if (summary) {
        setStreamWorkspaceRevisions((prev) => ({
          ...prev,
          [workspaceConfig.workspaceId]: summary.revision,
        }));
        streamWorkspaceRevisionsRef.current = {
          ...streamWorkspaceRevisionsRef.current,
          [workspaceConfig.workspaceId]: summary.revision,
        };
      }
    }
    await refreshWorkspaceStoreStatus(source, { notifyOnError: false });
  };

  return {
    refreshWorkspaceStoreStatus,
    loadStreamAnalysisWorkspaces,
    deleteStreamAnalysisWorkspace,
    buildStreamAnalysisWorkspacePayload,
    syncStreamAnalysisWorkspace,
  };
}
