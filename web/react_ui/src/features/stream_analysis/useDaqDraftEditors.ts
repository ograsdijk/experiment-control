import { notifications } from "@mantine/notifications";

import {
  STREAM_DAG_OPS,
  defaultInputsForOp,
  defaultParamsForOp,
  isPublishableNodeKind,
  nodeKindFromOp,
} from "../stream/dag";
import type { StreamDagOpId } from "../stream/types";
import { useStreamAnalysis } from "./StreamAnalysisContext";

/**
 * DAQ draft node/output editors.
 *
 * Pure draft-state mutators for the DAQ workspace modal — adding,
 * removing, renaming, and reconfiguring graph nodes plus their
 * publish outputs. None of these handlers talk to the API or touch
 * the panels list; they only mutate the draft state in
 * StreamAnalysisContext, which `applyDaqWorkspace` later validates +
 * commits.
 *
 * **Handlers**:
 *
 * - `setDaqNodeId(index, value)` — renames a node, cascading the id
 *   change into any other node's `inputs` and any output that
 *   references it.
 * - `setDaqNodeOp(index, opRaw)` — switches a node's op, resetting
 *   its params + inputs to the new op's defaults.
 * - `setDaqNodeInput(index, port, sourceNodeId)` — wires one of a
 *   node's input ports to another node.
 * - `setDaqNodeParam(index, paramName, value)` — sets one of a node's
 *   op-specific params.
 * - `addDaqNode()` — appends a default `trace.integrate` node with a
 *   unique `node_N` id.
 * - `removeDaqNode(index)` — drops a node + any outputs that
 *   referenced it.
 * - `setDaqOutputId(index, outputId)` — renames an output.
 * - `setDaqOutputNode(index, nodeId)` — switches an output's source
 *   node.
 * - `addDaqOutput()` — appends a new `out_N` output bound to the
 *   first publishable node (or warns if none exist).
 * - `removeDaqOutput(index)` — drops an output.
 */
export function useDaqDraftEditors() {
  const {
    daqDraftNodes,
    setDaqDraftNodes,
    daqDraftOutputs,
    setDaqDraftOutputs,
  } = useStreamAnalysis();

  const setDaqNodeId = (index: number, value: string) => {
    const nextId = value.trim();
    if (!nextId) {
      return;
    }
    const current = daqDraftNodes[index];
    if (!current) {
      return;
    }
    if (current.nodeId === nextId) {
      return;
    }
    if (daqDraftNodes.some((node, idx) => idx !== index && node.nodeId === nextId)) {
      notifications.show({
        color: "red",
        title: "Duplicate node ID",
        message: `Node id '${nextId}' is already in use.`,
      });
      return;
    }
    const oldId = current.nodeId;
    setDaqDraftNodes((prev) =>
      prev.map((node, idx) => {
        if (idx === index) {
          return { ...node, nodeId: nextId };
        }
        let changed = false;
        const nextInputs: Record<string, string> = {};
        for (const [port, source] of Object.entries(node.inputs ?? {})) {
          if (source === oldId) {
            nextInputs[port] = nextId;
            changed = true;
          } else {
            nextInputs[port] = source;
          }
        }
        return changed ? { ...node, inputs: nextInputs } : node;
      })
    );
    setDaqDraftOutputs((prev) =>
      prev.map((output) =>
        output.nodeId === oldId ? { ...output, nodeId: nextId } : output
      )
    );
  };

  const setDaqNodeOp = (index: number, opRaw: string | null) => {
    if (!opRaw || !Object.prototype.hasOwnProperty.call(STREAM_DAG_OPS, opRaw)) {
      return;
    }
    const op = opRaw as StreamDagOpId;
    setDaqDraftNodes((prev) =>
      prev.map((node, idx) =>
        idx === index
          ? {
              ...node,
              op,
              params: defaultParamsForOp(op),
              inputs: defaultInputsForOp(op),
            }
          : node
      )
    );
  };

  const setDaqNodeInput = (
    index: number,
    port: string,
    sourceNodeId: string | null
  ) => {
    setDaqDraftNodes((prev) =>
      prev.map((node, idx) =>
        idx === index
          ? {
              ...node,
              inputs: {
                ...node.inputs,
                [port]: String(sourceNodeId ?? "").trim(),
              },
            }
          : node
      )
    );
  };

  const setDaqNodeParam = (index: number, paramName: string, value: string) => {
    setDaqDraftNodes((prev) =>
      prev.map((node, idx) =>
        idx === index
          ? {
              ...node,
              params: {
                ...node.params,
                [paramName]: value,
              },
            }
          : node
      )
    );
  };

  const addDaqNode = () => {
    const existingIds = new Set(daqDraftNodes.map((node) => node.nodeId));
    let counter = daqDraftNodes.length + 1;
    let nodeId = `node_${counter}`;
    while (existingIds.has(nodeId)) {
      counter += 1;
      nodeId = `node_${counter}`;
    }
    const op: StreamDagOpId = "trace.integrate";
    setDaqDraftNodes((prev) => [
      ...prev,
      {
        nodeId,
        op,
        params: defaultParamsForOp(op),
        inputs: defaultInputsForOp(op),
      },
    ]);
  };

  const removeDaqNode = (index: number) => {
    const removed = daqDraftNodes[index];
    if (!removed) {
      return;
    }
    const removedId = removed.nodeId;
    setDaqDraftNodes((prev) => prev.filter((_, idx) => idx !== index));
    setDaqDraftOutputs((prev) =>
      prev.filter((output) => output.nodeId !== removedId)
    );
  };

  const setDaqOutputId = (index: number, outputId: string) => {
    setDaqDraftOutputs((prev) =>
      prev.map((output, idx) =>
        idx === index ? { ...output, outputId: outputId.trim() } : output
      )
    );
  };

  const setDaqOutputNode = (index: number, nodeId: string | null) => {
    setDaqDraftOutputs((prev) =>
      prev.map((output, idx) =>
        idx === index
          ? { ...output, nodeId: String(nodeId ?? "").trim() }
          : output
      )
    );
  };

  const addDaqOutput = () => {
    const publishableNodeIds = daqDraftNodes
      .filter((node) => isPublishableNodeKind(nodeKindFromOp(node.op)))
      .map((node) => node.nodeId);
    if (publishableNodeIds.length <= 0) {
      notifications.show({
        color: "yellow",
        title: "No publishable nodes",
        message:
          "Add a scalar, trace, fit_1d, hist_agg, hist2d, or params_map node first.",
      });
      return;
    }
    const usedOutputIds = new Set(
      daqDraftOutputs.map((output) => output.outputId)
    );
    let counter = daqDraftOutputs.length + 1;
    let outputId = `out_${counter}`;
    while (usedOutputIds.has(outputId)) {
      counter += 1;
      outputId = `out_${counter}`;
    }
    setDaqDraftOutputs((prev) => [
      ...prev,
      {
        outputId,
        nodeId: publishableNodeIds[0],
      },
    ]);
  };

  const removeDaqOutput = (index: number) => {
    setDaqDraftOutputs((prev) => prev.filter((_, idx) => idx !== index));
  };

  return {
    setDaqNodeId,
    setDaqNodeOp,
    setDaqNodeInput,
    setDaqNodeParam,
    addDaqNode,
    removeDaqNode,
    setDaqOutputId,
    setDaqOutputNode,
    addDaqOutput,
    removeDaqOutput,
  };
}
