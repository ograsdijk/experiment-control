import type {
  SequencerAdaptiveDetail,
  SequencerAtomicDetail,
  SequencerAssignDetail,
  SequencerCallDetail,
  SequencerForDetail,
  SequencerIfDetail,
  SequencerOutlineMetadata,
  SequencerOutlineMetadataEntry,
  SequencerParallelDetail,
  SequencerPauseDetail,
  SequencerSetContextDetail,
  SequencerSetContextStreamDetail,
  SequencerSetDetail,
  SequencerSleepDetail,
  SequencerRepeatDetail,
  SequencerStepOutlineNode,
  SequencerWaitUntilDetail,
  SequencerWhileDetail,
} from "./types";
import type { Node } from "yaml";
import {
  childNode,
  conditionEntries,
  flattenEntries,
  isMap,
  isScalar,
  isSeq,
  leafText,
  namedGroups,
  normalizeScan2d,
  readStep,
  scalarField,
  sectionEntries,
  seqLength,
} from "./yaml_detail";

const STEP_CONTAINER_KEYS = new Set(["steps", "do", "then", "else", "finally"]);
const STEP_ITEM_PATTERN = /^(\s*)-\s*([A-Za-z_][A-Za-z0-9_]*)\s*:(.*)$/;
const CONTAINER_PATTERN = /^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:#.*)?$/;
const TOP_LEVEL_KEY_PATTERN = /^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$/;
const CHILD_KEY_PATTERN = /^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:/;
const FOR_GENERATOR_KINDS = new Set([
  "range",
  "linspace",
  "triangle",
  "centered_triangle",
  "logspace",
  "geomspace",
  "values",
  "scan2d",
]);
const FOR_GENERATOR_MODIFIER_KEYS = new Set([
  "offset",
  "shuffle",
  "seed",
  "serpentine",
]);

type FlatStepNode = {
  id: string;
  kind: string;
  line: number;
  startIndex: number;
  endIndex: number;
  indent: number;
  containerKey: string;
  inlineRemainder: string;
};

type StepContainerContext = {
  key: string;
  baseIndent: number;
  listIndent: number | null;
};

function compactText(value: string, maxLength = 96): string {
  const compact = value.replace(/\s+/g, " ").trim();
  if (compact.length <= maxLength) {
    return compact;
  }
  return `${compact.slice(0, Math.max(0, maxLength - 1)).trimEnd()}...`;
}

function normalizeSnippet(lines: string[], indent: number): string {
  let start = 0;
  let end = lines.length - 1;
  while (start <= end && lines[start].trim() === "") {
    start += 1;
  }
  while (end >= start && lines[end].trim() === "") {
    end -= 1;
  }
  if (start > end) {
    return "";
  }
  return lines
    .slice(start, end + 1)
    .map((line) => line.slice(Math.min(indent, line.length)))
    .join("\n");
}

function lineIndent(line: string): number {
  return line.match(/^\s*/)?.[0].length ?? 0;
}

function parseAdaptiveDetail(snippet: string): SequencerAdaptiveDetail {
  const step = readStep(snippet);
  const body = step?.body ?? null;
  const src = step?.src ?? snippet;
  const controller = childNode(body, "controller");
  const observe = childNode(body, "observe");
  const metrics = namedGroups(observe, "metrics", src).map((group) => {
    const kindEntry = group.entries.find((entry) => entry.name === "kind");
    const config = group.entries
      .filter((entry) => entry.name !== "kind")
      .map((entry) => ({
        name: entry.name.startsWith("config.")
          ? entry.name.slice("config.".length)
          : entry.name,
        value: entry.value,
      }));
    return { name: group.name, sourceKind: kindEntry?.value ?? null, config };
  });
  return {
    id: scalarField(body, "id", src),
    controllerKind: scalarField(controller, "kind", src),
    controllerConfig: sectionEntries(controller, "config", src),
    space: namedGroups(body, "space", src),
    bind: sectionEntries(body, "bind", src),
    observeRepeats: scalarField(observe, "repeats", src),
    metrics,
    aggregate: sectionEntries(observe, "aggregate", src),
    score: scalarField(observe, "score", src),
    stopping: sectionEntries(body, "stopping", src),
  };
}

function parseCallDetail(snippet: string): SequencerCallDetail {
  const step = readStep(snippet);
  const body = step?.body ?? null;
  const src = step?.src ?? snippet;
  const process = scalarField(body, "process", src);
  return {
    targetKind: process !== null ? "process" : "device",
    device: scalarField(body, "device", src),
    process,
    action: scalarField(body, "action", src),
    params: sectionEntries(body, "params", src),
  };
}

function parseSleepDetail(
  _inlineRemainder: string,
  snippet: string
): SequencerSleepDetail {
  const step = readStep(snippet);
  const body = step?.body ?? null;
  const src = step?.src ?? snippet;
  if (isScalar(body)) {
    return { duration: leafText(body, src) };
  }
  return { duration: scalarField(body, "duration", src) };
}

function parseSetDetail(snippet: string): SequencerSetDetail {
  const step = readStep(snippet);
  const body = step?.body ?? null;
  const src = step?.src ?? snippet;
  return {
    device: scalarField(body, "device", src),
    name: scalarField(body, "name", src),
    value: scalarField(body, "value", src),
  };
}

function parseAssignDetail(snippet: string): SequencerAssignDetail {
  const step = readStep(snippet);
  const body = step?.body ?? null;
  const src = step?.src ?? snippet;
  return {
    entries: isMap(body) ? flattenEntries(body, src) : [],
  };
}

function parseWaitUntilDetail(snippet: string): SequencerWaitUntilDetail {
  const step = readStep(snippet);
  const body = step?.body ?? null;
  const src = step?.src ?? snippet;
  return {
    timeoutS: scalarField(body, "timeout_s", src),
    everyS: scalarField(body, "every_s", src),
    sample: sectionEntries(body, "sample", src),
    condition: conditionEntries(body, "condition", src),
  };
}

function readStreamItems(
  seqNode: Node | null,
  src: string
): SequencerSetContextStreamDetail[] {
  if (!isSeq(seqNode)) {
    return [];
  }
  return seqNode.items.map((item) => {
    if (isMap(item)) {
      return {
        device: scalarField(item as Node, "device", src),
        stream: scalarField(item as Node, "stream", src),
      };
    }
    return { device: leafText(item, src), stream: null };
  });
}

function parseSetContextDetail(snippet: string): SequencerSetContextDetail {
  const step = readStep(snippet);
  const body = step?.body ?? null;
  const src = step?.src ?? snippet;
  return {
    streams: readStreamItems(childNode(body, "streams"), src),
    fields: sectionEntries(body, "fields", src),
  };
}

function parseIfDetail(snippet: string): SequencerIfDetail {
  const step = readStep(snippet);
  const body = step?.body ?? null;
  const src = step?.src ?? snippet;
  return {
    condition: conditionEntries(body, "condition", src),
    thenCount: seqLength(body, "then"),
    elseCount: seqLength(body, "else"),
  };
}

function parseWhileDetail(snippet: string): SequencerWhileDetail {
  const step = readStep(snippet);
  const body = step?.body ?? null;
  const src = step?.src ?? snippet;
  return {
    condition: conditionEntries(body, "condition", src),
  };
}

function parseAtomicDetail(snippet: string): SequencerAtomicDetail {
  const step = readStep(snippet);
  const body = step?.body ?? null;
  const src = step?.src ?? snippet;
  return {
    name: scalarField(body, "name", src),
  };
}

function parsePauseDetail(
  _inlineRemainder: string,
  snippet: string
): SequencerPauseDetail {
  const step = readStep(snippet);
  const body = step?.body ?? null;
  const src = step?.src ?? snippet;
  if (isScalar(body)) {
    return { reason: leafText(body, src) };
  }
  return {
    reason: scalarField(body, "reason", src),
  };
}

function parseParallelDetail(childCount: number): SequencerParallelDetail {
  return {
    branchCount: childCount,
  };
}

function parseForDetail(snippet: string): SequencerForDetail {
  const step = readStep(snippet);
  const body = step?.body ?? null;
  const src = step?.src ?? snippet;

  let bind: SequencerOutlineMetadataEntry[] = [];
  const bindNode = childNode(body, "bind");
  if (isScalar(bindNode)) {
    // Scalar shorthand `bind: hv` binds the record field `value` to local `hv`.
    bind = [{ name: "value", value: leafText(bindNode, src) }];
  } else if (isMap(bindNode)) {
    bind = flattenEntries(bindNode, src);
  }

  let sourceMode: "generator" | "direct" = "direct";
  let generatorKind: string | null = null;
  let directValue: string | null = null;
  const generatorModifiers: SequencerOutlineMetadataEntry[] = [];
  let iterableConfig: SequencerOutlineMetadataEntry[] = [];

  const inNode = childNode(body, "in");
  const genNode = childNode(inNode, "gen");
  if (isMap(genNode)) {
    sourceMode = "generator";
    for (const pair of genNode.items) {
      const key = pair.key == null ? "" : String(pair.key).trim();
      if (!key) {
        continue;
      }
      const value = (pair.value as Node | null) ?? null;
      if (FOR_GENERATOR_MODIFIER_KEYS.has(key)) {
        generatorModifiers.push({ name: key, value: leafText(value, src) });
      } else if (key === "sample") {
        for (const entry of flattenEntries(value, src)) {
          generatorModifiers.push({ name: `sample.${entry.name}`, value: entry.value });
        }
      } else if (FOR_GENERATOR_KINDS.has(key)) {
        generatorKind = key;
        if (key === "values") {
          iterableConfig = isSeq(value)
            ? value.items.map((item, index) => ({
                name: String(index),
                value: leafText(item, src),
              }))
            : [{ name: "inline", value: leafText(value, src) }];
        } else if (key === "scan2d") {
          iterableConfig = normalizeScan2d(value, src);
        } else {
          iterableConfig = isMap(value)
            ? flattenEntries(value, src)
            : [{ name: "value", value: leafText(value, src) }];
        }
      }
    }
  } else if (inNode) {
    sourceMode = "direct";
    directValue = leafText(inNode, src);
  }

  return {
    bind,
    sourceMode,
    generatorKind,
    directValue,
    generatorModifiers,
    iterableConfig,
  };
}

function parseRepeatDetail(snippet: string): SequencerRepeatDetail {
  const step = readStep(snippet);
  const body = step?.body ?? null;
  const src = step?.src ?? snippet;
  return {
    times: scalarField(body, "times", src),
  };
}

function deriveSummary(
  node: SequencerStepOutlineNode,
  inlineRemainder: string
): string | null {
  const kind = node.kind;
  const childCount = node.children.length;
  const inline = compactText(inlineRemainder.replace(/^#.*$/, "").trim());

  if (kind === "call" && node.callDetail) {
    const { device, process, action } = node.callDetail;
    const target = device || process;
    if (target && action) {
      return `${target}.${action}`;
    }
    if (target || action) {
      return target || action;
    }
  }
  if (kind === "sleep") {
    const duration = node.sleepDetail?.duration ?? (inline || null);
    if (duration) {
      return `sleep ${duration}`;
    }
  }
  if (kind === "set" && node.setDetail) {
    const { device, name, value } = node.setDetail;
    if (device && name && value) {
      return `${device}.${name} = ${compactText(value, 56)}`;
    }
    if (name && value) {
      return `${name} = ${compactText(value, 56)}`;
    }
  }
  if (kind === "assign" && node.assignDetail) {
    const entry = node.assignDetail.entries[0];
    if (entry && entry.value) {
      return `${entry.name} = ${compactText(entry.value, 56)}`;
    }
  }
  if (kind === "repeat") {
    const times = node.repeatDetail?.times;
    if (times) {
      return `repeat ${times}${childCount > 0 ? ` (${childCount} step${childCount === 1 ? "" : "s"})` : ""}`;
    }
  }
  if (kind === "for" && node.forDetail) {
    const detail = node.forDetail;
    const binds = detail.bind.map((entry) => `${entry.name} -> ${entry.value ?? "n/a"}`);
    const bindSummary = binds.length > 0 ? binds.join(", ") : "bindings";
    if (detail.sourceMode === "direct") {
      return `${bindSummary} over ${compactText(detail.directValue ?? "expression", 48)}`;
    }
    if (detail.generatorKind) {
      return `${bindSummary} over ${detail.generatorKind}`;
    }
    return bindSummary;
  }
  if (kind === "adaptive" && node.adaptiveDetail) {
    const detail = node.adaptiveDetail;
    const spaceKeys = detail.space.slice(0, 3).map((entry) => entry.name);
    const parts = [
      detail.id ?? null,
      detail.controllerKind,
      spaceKeys.length > 0 ? `space: ${spaceKeys.join(", ")}` : null,
    ].filter((part): part is string => Boolean(part));
    if (parts.length > 0) {
      return parts.join(" | ");
    }
  }
  if (kind === "wait_until" && node.waitUntilDetail?.timeoutS) {
    return `timeout ${node.waitUntilDetail.timeoutS}s`;
  }
  if (kind === "if" && node.ifDetail) {
    const parts = [`then ${node.ifDetail.thenCount}`];
    if (node.ifDetail.elseCount > 0) {
      parts.push(`else ${node.ifDetail.elseCount}`);
    }
    return parts.join(" | ");
  }
  if (kind === "while" && node.whileDetail) {
    const firstCondition = node.whileDetail.condition[0]?.name;
    if (firstCondition) {
      return `while ${firstCondition}`;
    }
  }
  if (kind === "atomic" && node.atomicDetail?.name) {
    return `name=${node.atomicDetail.name}`;
  }
  if (kind === "pause" && node.pauseDetail?.reason) {
    return `pause ${compactText(node.pauseDetail.reason, 56)}`;
  }
  if (kind === "parallel" && node.parallelDetail) {
    const count = node.parallelDetail.branchCount;
    return `${count} branch${count === 1 ? "" : "es"}`;
  }
  if (kind === "set_context" && node.setContextDetail) {
    const detail = node.setContextDetail;
    const streamCount = detail.streams.length;
    const fieldKeys = detail.fields.slice(0, 3).map((entry) => entry.name);
    const parts = [
      streamCount > 0 ? `${streamCount} stream${streamCount === 1 ? "" : "s"}` : null,
      fieldKeys.length > 0 ? `fields: ${fieldKeys.join(", ")}` : null,
    ].filter((part): part is string => Boolean(part));
    if (parts.length > 0) {
      return parts.join(" | ");
    }
  }

  if (inline.length > 0) {
    return inline;
  }

  const lines = node.snippet.split("\n");
  const detailLines: string[] = [];
  for (let i = 1; i < lines.length; i += 1) {
    const trimmed = lines[i].trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }
    if (trimmed === "do:" || trimmed === "steps:") {
      continue;
    }
    if (trimmed.startsWith("- ")) {
      continue;
    }
    detailLines.push(compactText(trimmed, 72));
    if (detailLines.length >= 2) {
      break;
    }
  }
  if (detailLines.length > 0) {
    return detailLines.join(" | ");
  }
  if (childCount > 0) {
    return `${childCount} nested step${childCount === 1 ? "" : "s"}`;
  }
  return kind;
}

function splitLines(yamlText: string): string[] {
  return yamlText.replace(/\r\n/g, "\n").split("\n");
}

function buildFlatStepNodes(lines: string[]): FlatStepNode[] {
  const flat: FlatStepNode[] = [];
  const containerStack: StepContainerContext[] = [];

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const indent = line.match(/^\s*/)?.[0].length ?? 0;
    while (
      containerStack.length > 0 &&
      indent <= containerStack[containerStack.length - 1].baseIndent
    ) {
      containerStack.pop();
    }

    const containerMatch = line.match(CONTAINER_PATTERN);
    if (containerMatch) {
      const key = containerMatch[2];
      if (STEP_CONTAINER_KEYS.has(key)) {
        containerStack.push({
          key,
          baseIndent: containerMatch[1].length,
          listIndent: null,
        });
        continue;
      }
    }

    const stepMatch = line.match(STEP_ITEM_PATTERN);
    if (!stepMatch || containerStack.length <= 0) {
      continue;
    }

    const currentContainer = containerStack[containerStack.length - 1];
    if (indent <= currentContainer.baseIndent) {
      continue;
    }
    if (currentContainer.listIndent === null) {
      currentContainer.listIndent = indent;
    }
    if (indent !== currentContainer.listIndent) {
      continue;
    }

    flat.push({
      id: `step-${index + 1}`,
      kind: stepMatch[2],
      line: index + 1,
      startIndex: index,
      endIndex: lines.length - 1,
      indent,
      containerKey: currentContainer.key,
      inlineRemainder: stepMatch[3]?.trim() ?? "",
    });
  }

  for (let i = 0; i < flat.length; i += 1) {
    const current = flat[i];
    // The step spans up to its last line indented deeper than the step itself.
    // Trailing blank lines and outer-scope comments (indent <= the step's) are
    // excluded — including them would otherwise sweep sibling/outer comments
    // into the snippet, where normalizeSnippet over-strips them into garbage.
    let endIndex = current.startIndex;
    for (let lineIndex = current.startIndex + 1; lineIndex < lines.length; lineIndex += 1) {
      const nextLine = lines[lineIndex];
      const trimmed = nextLine.trim();
      if (!trimmed) {
        continue;
      }
      if (lineIndent(nextLine) <= current.indent) {
        break;
      }
      endIndex = lineIndex;
    }
    current.endIndex = Math.max(current.startIndex, endIndex);
  }

  return flat;
}

function finalizeSummaries(
  nodes: readonly SequencerStepOutlineNode[],
  flatMap: ReadonlyMap<number, FlatStepNode>
): void {
  for (const node of nodes) {
    finalizeSummaries(node.children, flatMap);
    const flatNode = flatMap.get(node.line);
    if (!flatNode) {
      continue;
    }
    const callDetail = node.kind === "call" ? parseCallDetail(node.snippet) : null;
    const sleepDetail =
      node.kind === "sleep"
        ? parseSleepDetail(flatNode.inlineRemainder, node.snippet)
        : null;
    const setDetail = node.kind === "set" ? parseSetDetail(node.snippet) : null;
    const assignDetail =
      node.kind === "assign" ? parseAssignDetail(node.snippet) : null;
    const waitUntilDetail =
      node.kind === "wait_until" ? parseWaitUntilDetail(node.snippet) : null;
      const setContextDetail =
        node.kind === "set_context" ? parseSetContextDetail(node.snippet) : null;
      const ifDetail = node.kind === "if" ? parseIfDetail(node.snippet) : null;
      const whileDetail =
        node.kind === "while" ? parseWhileDetail(node.snippet) : null;
      const atomicDetail =
        node.kind === "atomic" ? parseAtomicDetail(node.snippet) : null;
      const pauseDetail =
        node.kind === "pause"
          ? parsePauseDetail(flatNode.inlineRemainder, node.snippet)
          : null;
      const parallelDetail =
        node.kind === "parallel" ? parseParallelDetail(node.children.length) : null;
      const forDetail = node.kind === "for" ? parseForDetail(node.snippet) : null;
      const repeatDetail =
        node.kind === "repeat" ? parseRepeatDetail(node.snippet) : null;
      const adaptiveDetail =
        node.kind === "adaptive" ? parseAdaptiveDetail(node.snippet) : null;
    node.callDetail = callDetail;
    node.sleepDetail = sleepDetail;
    node.setDetail = setDetail;
      node.assignDetail = assignDetail;
      node.waitUntilDetail = waitUntilDetail;
      node.setContextDetail = setContextDetail;
      node.ifDetail = ifDetail;
      node.whileDetail = whileDetail;
      node.atomicDetail = atomicDetail;
      node.pauseDetail = pauseDetail;
      node.parallelDetail = parallelDetail;
      node.forDetail = forDetail;
      node.repeatDetail = repeatDetail;
      node.adaptiveDetail = adaptiveDetail;
    node.summary = deriveSummary(node, flatNode.inlineRemainder);
  }
}

function buildStableStepId(
  parentId: string | null,
  containerKey: string,
  siblingIndex: number,
  kind: string
): string {
  const scope = parentId ?? "root";
  return `${scope}/${containerKey}:${siblingIndex}:${kind}`;
}

function buildCanonicalStepPath(
  parentNode: SequencerStepOutlineNode | null,
  containerKey: string,
  siblingIndex: number
): string {
  if (!parentNode || containerKey === "steps") {
    return `steps[${siblingIndex}]`;
  }
  const parentPath = parentNode.path ?? parentNode.id;
  if (containerKey === "then" || containerKey === "else") {
    return `${parentPath}.if.${containerKey}[${siblingIndex}]`;
  }
  if (containerKey === "finally") {
    return `${parentPath}.try.finally[${siblingIndex}]`;
  }
  return `${parentPath}.${parentNode.kind}.${containerKey}[${siblingIndex}]`;
}

function canonicalChildPrefix(
  parentNode: SequencerStepOutlineNode,
  containerKey: string
): string {
  const parentPath = parentNode.path ?? parentNode.id;
  if (containerKey === "then" || containerKey === "else") {
    return `${parentPath}.if.${containerKey}[`;
  }
  if (containerKey === "finally") {
    return `${parentPath}.try.finally[`;
  }
  return `${parentPath}.${parentNode.kind}.${containerKey}[`;
}

function containerSiblingIndex(
  parentNode: SequencerStepOutlineNode | null,
  roots: readonly SequencerStepOutlineNode[],
  containerKey: string
): number {
  if (!parentNode || containerKey === "steps") {
    return roots.length;
  }
  const prefix = canonicalChildPrefix(parentNode, containerKey);
  return parentNode.children.filter((child) => child.path?.startsWith(prefix)).length;
}

export function buildSequencerStepOutline(
  yamlText: string
): SequencerStepOutlineNode[] {
  if (!yamlText.trim()) {
    return [];
  }
  const lines = splitLines(yamlText);
  const flatNodes = buildFlatStepNodes(lines);
  if (flatNodes.length <= 0) {
    return [];
  }

  const flatMap = new Map<number, FlatStepNode>();
  const roots: SequencerStepOutlineNode[] = [];
  const stack: Array<{ indent: number; node: SequencerStepOutlineNode }> = [];

  for (const flatNode of flatNodes) {
    flatMap.set(flatNode.line, flatNode);
    while (
      stack.length > 0 &&
      flatNode.indent <= stack[stack.length - 1].indent
    ) {
      stack.pop();
    }

    const parentNode = stack.length > 0 ? stack[stack.length - 1].node : null;
    const siblingIndex = containerSiblingIndex(parentNode, roots, flatNode.containerKey);
    const node: SequencerStepOutlineNode = {
      id: buildStableStepId(
        parentNode?.id ?? null,
        flatNode.containerKey,
        siblingIndex,
        flatNode.kind
      ),
      path: buildCanonicalStepPath(parentNode, flatNode.containerKey, siblingIndex),
      kind: flatNode.kind,
      line: flatNode.line,
      endLine: flatNode.endIndex + 1,
      indent: flatNode.indent,
      branchLabel:
        flatNode.containerKey === "then" ||
        flatNode.containerKey === "else" ||
        flatNode.containerKey === "finally"
          ? flatNode.containerKey
          : null,
      summary: null,
      snippet: normalizeSnippet(
        lines.slice(flatNode.startIndex, flatNode.endIndex + 1),
        flatNode.indent
      ),
      children: [],
      callDetail: null,
      sleepDetail: null,
      setDetail: null,
      assignDetail: null,
      waitUntilDetail: null,
      setContextDetail: null,
      ifDetail: null,
      whileDetail: null,
      atomicDetail: null,
      pauseDetail: null,
      parallelDetail: null,
      forDetail: null,
      repeatDetail: null,
      adaptiveDetail: null,
    };

    if (stack.length > 0) {
      stack[stack.length - 1].node.children.push(node);
    } else {
      roots.push(node);
    }
    stack.push({ indent: flatNode.indent, node });
  }

  finalizeSummaries(roots, flatMap);
  return roots;
}

export function flattenSequencerStepOutline(
  nodes: readonly SequencerStepOutlineNode[]
): SequencerStepOutlineNode[] {
  const out: SequencerStepOutlineNode[] = [];
  const walk = (items: readonly SequencerStepOutlineNode[]) => {
    for (const item of items) {
      out.push(item);
      walk(item.children);
    }
  };
  walk(nodes);
  return out;
}

export function buildSequencerOutlineMetadata(
  yamlText: string
): SequencerOutlineMetadata {
  const metadata: SequencerOutlineMetadata = {
    version: null,
    vars: [],
    contextColumns: [],
  };
  if (!yamlText.trim()) {
    return metadata;
  }

  const lines = splitLines(yamlText);
  let activeSection: "vars" | "context_columns" | null = null;
  let childIndent: number | null = null;

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }
    const indent = line.match(/^\s*/)?.[0].length ?? 0;

    if (indent === 0) {
      activeSection = null;
      childIndent = null;

      const topLevelMatch = trimmed.match(TOP_LEVEL_KEY_PATTERN);
      if (!topLevelMatch) {
        continue;
      }
      const key = topLevelMatch[1];
      const remainder = topLevelMatch[2].replace(/\s+#.*$/, "").trim();
      if (key === "version") {
        metadata.version = remainder || null;
        continue;
      }
      if (key === "vars" || key === "context_columns") {
        activeSection = key;
      }
      continue;
    }

    if (!activeSection) {
      continue;
    }

    if (childIndent === null) {
      childIndent = indent;
    }
    if (indent < childIndent) {
      activeSection = null;
      childIndent = null;
      continue;
    }
    if (indent !== childIndent) {
      continue;
    }

    const childMatch = line.match(CHILD_KEY_PATTERN);
    if (!childMatch) {
      continue;
    }
    const key = childMatch[2];
    const value = line
      .slice(line.indexOf(":") + 1)
      .replace(/\s+#.*$/, "")
      .trim();
    const entry: SequencerOutlineMetadataEntry = {
      name: key,
      value: value || null,
    };
    if (activeSection === "vars") {
      metadata.vars.push(entry);
    } else {
      metadata.contextColumns.push(entry);
    }
  }

  return metadata;
}
