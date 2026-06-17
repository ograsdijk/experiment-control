import { YAMLMap, YAMLSeq } from "yaml";
import type { Document } from "yaml";
import type {
  SequencerOutlineMetadataEntry,
  SequencerSetContextStreamDetail,
  SequencerStepOutlineNode,
} from "../types";
import { replaceStepSnippet } from "./shared";
import {
  bodyMap,
  cleanEntries,
  editStep,
  emptyMap,
  entriesToMap,
  setCondition,
  setScalarOrDelete,
  textToNode,
} from "./yaml_write";

function buildStreams(
  doc: Document,
  streams: ReadonlyArray<SequencerSetContextStreamDetail>
): YAMLSeq {
  const seq = new YAMLSeq();
  if (streams.length <= 0) {
    seq.flow = true;
    return seq;
  }
  for (const item of streams) {
    const device = (item.device ?? "").trim();
    const stream = (item.stream ?? "").trim();
    // `- "scope.trace"` string shorthand (no separate stream).
    if (device && !stream) {
      seq.add(textToNode(doc, item.device ?? ""));
      continue;
    }
    const map = new YAMLMap();
    map.set("device", textToNode(doc, item.device ?? ""));
    map.set("stream", textToNode(doc, item.stream ?? ""));
    seq.add(map);
  }
  return seq;
}

export function applyEditedSleepStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  duration: string
): string {
  const out = editStep(node.snippet, (doc, item) => {
    item.set("sleep", textToNode(doc, duration));
  });
  return replaceStepSnippet(yamlText, node, out);
}

export function applyEditedSetStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  device: string,
  name: string,
  value: string
): string {
  const out = editStep(node.snippet, (doc, item) => {
    const body = bodyMap(item, "set");
    body.set("device", textToNode(doc, device));
    body.set("name", textToNode(doc, name));
    body.set("value", textToNode(doc, value));
  });
  return replaceStepSnippet(yamlText, node, out);
}

export function applyEditedWaitUntilStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  timeoutS: string,
  everyS: string,
  sample: SequencerOutlineMetadataEntry[],
  condition: SequencerOutlineMetadataEntry[]
): string {
  const out = editStep(node.snippet, (doc, item) => {
    const body = bodyMap(item, "wait_until");
    setScalarOrDelete(doc, body, "timeout_s", timeoutS);
    setScalarOrDelete(doc, body, "every_s", everyS);
    body.set(
      "sample",
      cleanEntries(sample).length > 0 ? entriesToMap(doc, sample) : emptyMap()
    );
    setCondition(doc, body, condition);
  });
  return replaceStepSnippet(yamlText, node, out);
}

export function applyEditedRepeatStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  times: string
): string {
  const out = editStep(node.snippet, (doc, item) => {
    const body = bodyMap(item, "repeat");
    body.set("times", textToNode(doc, times));
  });
  return replaceStepSnippet(yamlText, node, out);
}

export function applyEditedAssignStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>
): string {
  const out = editStep(node.snippet, (doc, item) => {
    item.set(
      "assign",
      cleanEntries(entries).length > 0 ? entriesToMap(doc, entries) : emptyMap()
    );
  });
  return replaceStepSnippet(yamlText, node, out);
}

export function applyEditedSetContextStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  streams: ReadonlyArray<SequencerSetContextStreamDetail>,
  fields: ReadonlyArray<SequencerOutlineMetadataEntry>
): string {
  const out = editStep(node.snippet, (doc, item) => {
    const body = bodyMap(item, "set_context");
    body.set("streams", buildStreams(doc, streams));
    body.set(
      "fields",
      cleanEntries(fields).length > 0 ? entriesToMap(doc, fields) : emptyMap()
    );
  });
  return replaceStepSnippet(yamlText, node, out);
}

export function applyEditedIfStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  condition: SequencerOutlineMetadataEntry[]
): string {
  const out = editStep(node.snippet, (doc, item) => {
    setCondition(doc, bodyMap(item, "if"), condition);
  });
  return replaceStepSnippet(yamlText, node, out);
}

export function applyEditedWhileStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  condition: SequencerOutlineMetadataEntry[]
): string {
  const out = editStep(node.snippet, (doc, item) => {
    setCondition(doc, bodyMap(item, "while"), condition);
  });
  return replaceStepSnippet(yamlText, node, out);
}
