import type { SequencerOutlineMetadataEntry, SequencerStepOutlineNode } from "../types";
import { replaceStepSnippet } from "./shared";
import {
  bodyMap,
  cleanEntries,
  editStep,
  emptyMap,
  entriesToMap,
  textToNode,
} from "./yaml_write";

export function applyEditedCallStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  targetKind: "device" | "process",
  device: string,
  process: string,
  action: string,
  params: SequencerOutlineMetadataEntry[]
): string {
  const out = editStep(node.snippet, (doc, item) => {
    const body = bodyMap(item, "call");
    if (targetKind === "process") {
      body.delete("device");
      body.set("process", textToNode(doc, process));
    } else {
      body.delete("process");
      body.set("device", textToNode(doc, device));
    }
    body.set("action", textToNode(doc, action));
    body.set(
      "params",
      cleanEntries(params).length > 0 ? entriesToMap(doc, params) : emptyMap()
    );
  });
  return replaceStepSnippet(yamlText, node, out);
}
