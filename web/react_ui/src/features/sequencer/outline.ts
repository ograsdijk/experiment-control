import type {
  SequencerAdaptiveDetail,
  SequencerAdaptiveFieldGroup,
  SequencerAdaptiveMetricDetail,
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

const STEP_CONTAINER_KEYS = new Set(["steps", "do", "then", "else"]);
const STEP_ITEM_PATTERN = /^(\s*)-\s*([A-Za-z_][A-Za-z0-9_]*)\s*:(.*)$/;
const CONTAINER_PATTERN = /^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:#.*)?$/;
const TOP_LEVEL_KEY_PATTERN = /^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$/;
const CHILD_KEY_PATTERN = /^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:/;
const FOR_GENERATOR_KINDS = new Set([
  "range",
  "linspace",
  "triangle",
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

function stripInlineComment(value: string): string {
  return value.replace(/\s+#.*$/, "").trim();
}

function lineIndent(line: string): number {
  return line.match(/^\s*/)?.[0].length ?? 0;
}

function parseKeyValueLine(
  line: string
): { indent: number; key: string; value: string } | null {
  const match = line.match(/^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$/);
  if (!match) {
    return null;
  }
  return {
    indent: match[1].length,
    key: match[2],
    value: stripInlineComment(match[3] ?? ""),
  };
}

function findSectionBaseIndent(lines: readonly string[]): number | null {
  let baseIndent: number | null = null;
  for (const line of lines) {
    if (!line.trim() || line.trim().startsWith("#")) {
      continue;
    }
    const indent = lineIndent(line);
    if (baseIndent === null || indent < baseIndent) {
      baseIndent = indent;
    }
  }
  return baseIndent;
}

function findScalarValue(
  lines: readonly string[],
  key: string,
  baseIndent: number | null
): string | null {
  if (baseIndent === null) {
    return null;
  }
  for (const line of lines) {
    const parsed = parseKeyValueLine(line);
    if (!parsed || parsed.indent !== baseIndent || parsed.key !== key) {
      continue;
    }
    return parsed.value || null;
  }
  return null;
}

function findSectionLines(
  lines: readonly string[],
  key: string,
  baseIndent: number | null
): string[] {
  if (baseIndent === null) {
    return [];
  }
  let startIndex = -1;
  for (let index = 0; index < lines.length; index += 1) {
    const parsed = parseKeyValueLine(lines[index]);
    if (!parsed || parsed.indent !== baseIndent || parsed.key !== key) {
      continue;
    }
    startIndex = index + 1;
    break;
  }
  if (startIndex < 0) {
    return [];
  }
  const sectionLines: string[] = [];
  for (let index = startIndex; index < lines.length; index += 1) {
    const line = lines[index];
    if (!line.trim()) {
      sectionLines.push(line);
      continue;
    }
    const indent = lineIndent(line);
    if (indent <= baseIndent) {
      break;
    }
    sectionLines.push(line);
  }
  return sectionLines;
}

function flattenSectionEntries(lines: readonly string[]): SequencerOutlineMetadataEntry[] {
  const entries: SequencerOutlineMetadataEntry[] = [];
  const stack: Array<{ indent: number; key: string }> = [];

  for (const line of lines) {
    if (!line.trim() || line.trim().startsWith("#") || line.trim().startsWith("- ")) {
      continue;
    }
    const parsed = parseKeyValueLine(line);
    if (!parsed) {
      continue;
    }
    while (stack.length > 0 && stack[stack.length - 1].indent >= parsed.indent) {
      stack.pop();
    }
    if (parsed.value) {
      const path = [...stack.map((item) => item.key), parsed.key].join(".");
      entries.push({
        name: path,
        value: parsed.value,
      });
      continue;
    }
    stack.push({ indent: parsed.indent, key: parsed.key });
  }

  return entries;
}

function parseScalarListEntries(lines: readonly string[]): SequencerOutlineMetadataEntry[] {
  const entries: SequencerOutlineMetadataEntry[] = [];
  let index = 0;
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }
    const itemMatch = line.match(/^\s*-\s*(.*)$/);
    if (!itemMatch) {
      continue;
    }
    entries.push({
      name: String(index),
      value: stripInlineComment(itemMatch[1] ?? "") || null,
    });
    index += 1;
  }
  return entries;
}

function parseNamedFieldGroups(lines: readonly string[]): SequencerAdaptiveFieldGroup[] {
  const groups: SequencerAdaptiveFieldGroup[] = [];
  const baseIndent = findSectionBaseIndent(lines);
  if (baseIndent === null) {
    return groups;
  }

  for (let index = 0; index < lines.length; index += 1) {
    const parsed = parseKeyValueLine(lines[index]);
    if (!parsed || parsed.indent !== baseIndent) {
      continue;
    }
    const childLines: string[] = [];
    for (let childIndex = index + 1; childIndex < lines.length; childIndex += 1) {
      const childLine = lines[childIndex];
      if (!childLine.trim()) {
        childLines.push(childLine);
        continue;
      }
      if (lineIndent(childLine) <= baseIndent) {
        break;
      }
      childLines.push(childLine);
    }
    const entries =
      childLines.length > 0
        ? flattenSectionEntries(childLines)
        : parsed.value
          ? [{ name: "value", value: parsed.value }]
          : [];
    groups.push({
      name: parsed.key,
      entries,
    });
  }

  return groups;
}

function parseAdaptiveMetrics(lines: readonly string[]): SequencerAdaptiveMetricDetail[] {
  return parseNamedFieldGroups(lines).map((group) => {
    const sourceKindEntry = group.entries.find((entry) => entry.name === "kind");
    const configEntries = group.entries
      .filter((entry) => entry.name !== "kind")
      .map((entry) => ({
        name: entry.name.startsWith("config.")
          ? entry.name.slice("config.".length)
          : entry.name,
        value: entry.value,
      }));
    return {
      name: group.name,
      sourceKind: sourceKindEntry?.value ?? null,
      config: configEntries,
    };
  });
}

function parseAdaptiveDetail(snippet: string): SequencerAdaptiveDetail {
  const snippetLines = snippet.split("\n");
  const bodyLines = snippetLines.slice(1);
  const baseIndent = findSectionBaseIndent(bodyLines);

  const controllerLines = findSectionLines(bodyLines, "controller", baseIndent);
  const controllerBase = findSectionBaseIndent(controllerLines);
  const controllerConfigLines = findSectionLines(
    controllerLines,
    "config",
    controllerBase
  );

  const observeLines = findSectionLines(bodyLines, "observe", baseIndent);
  const observeBase = findSectionBaseIndent(observeLines);
  const metricsLines = findSectionLines(observeLines, "metrics", observeBase);
  const aggregateLines = findSectionLines(observeLines, "aggregate", observeBase);

  return {
    id: findScalarValue(bodyLines, "id", baseIndent),
    controllerKind: findScalarValue(controllerLines, "kind", controllerBase),
    controllerConfig: flattenSectionEntries(controllerConfigLines),
    space: parseNamedFieldGroups(findSectionLines(bodyLines, "space", baseIndent)),
    bind: flattenSectionEntries(findSectionLines(bodyLines, "bind", baseIndent)),
    observeRepeats: findScalarValue(observeLines, "repeats", observeBase),
    metrics: parseAdaptiveMetrics(metricsLines),
    aggregate: flattenSectionEntries(aggregateLines),
    score: findScalarValue(observeLines, "score", observeBase),
    stopping: flattenSectionEntries(findSectionLines(bodyLines, "stopping", baseIndent)),
  };
}

function parseCallDetail(snippet: string): SequencerCallDetail {
  const snippetLines = snippet.split("\n");
  const bodyLines = snippetLines.slice(1);
  const baseIndent = findSectionBaseIndent(bodyLines);
  return {
    device: findScalarValue(bodyLines, "device", baseIndent),
    action: findScalarValue(bodyLines, "action", baseIndent),
    params: flattenSectionEntries(findSectionLines(bodyLines, "params", baseIndent)),
  };
}

function parseSleepDetail(
  inlineRemainder: string,
  snippet: string
): SequencerSleepDetail {
  const inline = stripInlineComment(inlineRemainder);
  if (inline) {
    return { duration: inline };
  }
  const snippetLines = snippet.split("\n");
  const bodyLines = snippetLines.slice(1);
  const baseIndent = findSectionBaseIndent(bodyLines);
  return {
    duration: findScalarValue(bodyLines, "duration", baseIndent),
  };
}

function parseSetDetail(snippet: string): SequencerSetDetail {
  const snippetLines = snippet.split("\n");
  const bodyLines = snippetLines.slice(1);
  const baseIndent = findSectionBaseIndent(bodyLines);
  return {
    device: findScalarValue(bodyLines, "device", baseIndent),
    name: findScalarValue(bodyLines, "name", baseIndent),
    value: findScalarValue(bodyLines, "value", baseIndent),
  };
}

function parseAssignDetail(snippet: string): SequencerAssignDetail {
  const snippetLines = snippet.split("\n");
  const bodyLines = snippetLines.slice(1);
  return {
    entries: flattenSectionEntries(bodyLines),
  };
}

function parseWaitUntilDetail(snippet: string): SequencerWaitUntilDetail {
  const snippetLines = snippet.split("\n");
  const bodyLines = snippetLines.slice(1);
  const baseIndent = findSectionBaseIndent(bodyLines);
  return {
    timeoutS: findScalarValue(bodyLines, "timeout_s", baseIndent),
    everyS: findScalarValue(bodyLines, "every_s", baseIndent),
    sample: flattenSectionEntries(findSectionLines(bodyLines, "sample", baseIndent)),
    condition: flattenSectionEntries(
      findSectionLines(bodyLines, "condition", baseIndent)
    ),
  };
}

function parseStreamItems(lines: readonly string[]): SequencerSetContextStreamDetail[] {
  const streams: SequencerSetContextStreamDetail[] = [];
  let current: SequencerSetContextStreamDetail | null = null;
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }
    const itemMatch = line.match(/^\s*-\s*(.*)$/);
    if (itemMatch) {
      if (current) {
        streams.push(current);
      }
      current = {
        device: null,
        stream: null,
      };
      const inline = stripInlineComment(itemMatch[1] ?? "");
      if (inline) {
        const inlineField = inline.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+)$/);
        if (inlineField && current) {
          const key = inlineField[1];
          const value = stripInlineComment(inlineField[2]);
          if (key === "device") {
            current.device = value || null;
          } else if (key === "stream") {
            current.stream = value || null;
          }
        }
      }
      continue;
    }
    if (!current) {
      continue;
    }
    const parsed = parseKeyValueLine(line);
    if (!parsed) {
      continue;
    }
    if (parsed.key === "device") {
      current.device = parsed.value || null;
    } else if (parsed.key === "stream") {
      current.stream = parsed.value || null;
    }
  }
  if (current) {
    streams.push(current);
  }
  return streams;
}

function parseSetContextDetail(snippet: string): SequencerSetContextDetail {
  const snippetLines = snippet.split("\n");
  const bodyLines = snippetLines.slice(1);
  const baseIndent = findSectionBaseIndent(bodyLines);
  return {
    streams: parseStreamItems(findSectionLines(bodyLines, "streams", baseIndent)),
    fields: flattenSectionEntries(findSectionLines(bodyLines, "fields", baseIndent)),
  };
}

function countDirectStepItems(lines: readonly string[]): number {
  const baseIndent = findSectionBaseIndent(lines);
  if (baseIndent === null) {
    return 0;
  }
  let count = 0;
  for (const line of lines) {
    const match = line.match(STEP_ITEM_PATTERN);
    if (!match) {
      continue;
    }
    if (match[1].length === baseIndent) {
      count += 1;
    }
  }
  return count;
}

function parseIfDetail(snippet: string): SequencerIfDetail {
  const snippetLines = snippet.split("\n");
  const bodyLines = snippetLines.slice(1);
  const baseIndent = findSectionBaseIndent(bodyLines);
  const thenLines = findSectionLines(bodyLines, "then", baseIndent);
  const elseLines = findSectionLines(bodyLines, "else", baseIndent);
  return {
    condition: flattenSectionEntries(findSectionLines(bodyLines, "condition", baseIndent)),
    thenCount: countDirectStepItems(thenLines),
    elseCount: countDirectStepItems(elseLines),
  };
}

function parseWhileDetail(snippet: string): SequencerWhileDetail {
  const snippetLines = snippet.split("\n");
  const bodyLines = snippetLines.slice(1);
  const baseIndent = findSectionBaseIndent(bodyLines);
  return {
    condition: flattenSectionEntries(findSectionLines(bodyLines, "condition", baseIndent)),
  };
}

function parseAtomicDetail(snippet: string): SequencerAtomicDetail {
  const snippetLines = snippet.split("\n");
  const bodyLines = snippetLines.slice(1);
  const baseIndent = findSectionBaseIndent(bodyLines);
  return {
    name: findScalarValue(bodyLines, "name", baseIndent),
  };
}

function parsePauseDetail(
  inlineRemainder: string,
  snippet: string
): SequencerPauseDetail {
  const inline = stripInlineComment(inlineRemainder);
  if (inline && !inline.startsWith("{")) {
    return { reason: inline };
  }
  const snippetLines = snippet.split("\n");
  const bodyLines = snippetLines.slice(1);
  const baseIndent = findSectionBaseIndent(bodyLines);
  return {
    reason: findScalarValue(bodyLines, "reason", baseIndent),
  };
}

function parseParallelDetail(childCount: number): SequencerParallelDetail {
  return {
    branchCount: childCount,
  };
}

function parseForDetail(snippet: string): SequencerForDetail {
  const snippetLines = snippet.split("\n");
  const bodyLines = snippetLines.slice(1);
  const baseIndent = findSectionBaseIndent(bodyLines);
  const bindLines = findSectionLines(bodyLines, "bind", baseIndent);
  const inLines = findSectionLines(bodyLines, "in", baseIndent);
  const inBase = findSectionBaseIndent(inLines);
  const genLines = findSectionLines(inLines, "gen", inBase);
  const genBase = findSectionBaseIndent(genLines);

  let sourceMode: "generator" | "direct" = "direct";
  let generatorKind: string | null = null;
  let directValue: string | null = null;
  let generatorModifiers: SequencerOutlineMetadataEntry[] = [];
  let iterableConfig: SequencerOutlineMetadataEntry[] = [];

  if (genBase !== null) {
    sourceMode = "generator";
    for (const line of genLines) {
      const parsed = parseKeyValueLine(line);
      if (!parsed || parsed.indent !== genBase) {
        continue;
      }
      if (FOR_GENERATOR_MODIFIER_KEYS.has(parsed.key)) {
        generatorModifiers.push({
          name: parsed.key,
          value: parsed.value || null,
        });
        continue;
      }
      if (!FOR_GENERATOR_KINDS.has(parsed.key)) {
        continue;
      }
      generatorKind = parsed.key;
      if (parsed.key === "values") {
        if (parsed.value) {
          iterableConfig = [{ name: "inline", value: parsed.value }];
        } else {
          iterableConfig = parseScalarListEntries(
            findSectionLines(genLines, parsed.key, genBase)
          );
        }
      } else if (parsed.value) {
        iterableConfig = [{ name: "value", value: parsed.value }];
      } else {
        iterableConfig = flattenSectionEntries(
          findSectionLines(genLines, parsed.key, genBase)
        );
      }
      break;
    }
  }

  if (!generatorKind) {
    const directIterable = findScalarValue(bodyLines, "in", baseIndent);
    if (directIterable) {
      sourceMode = "direct";
      directValue = directIterable;
    }
  }

  return {
    bind: flattenSectionEntries(bindLines),
    sourceMode,
    generatorKind,
    directValue,
    generatorModifiers,
    iterableConfig,
  };
}

function parseRepeatDetail(snippet: string): SequencerRepeatDetail {
  const snippetLines = snippet.split("\n");
  const bodyLines = snippetLines.slice(1);
  const baseIndent = findSectionBaseIndent(bodyLines);
  return {
    times: findScalarValue(bodyLines, "times", baseIndent),
  };
}

function deriveSummary(
  kind: string,
  inlineRemainder: string,
  snippet: string,
  childCount: number,
  adaptiveDetail: SequencerAdaptiveDetail | null,
  forDetail: SequencerForDetail | null,
  repeatDetail: SequencerRepeatDetail | null,
  ifDetail: SequencerIfDetail | null,
  whileDetail: SequencerWhileDetail | null,
  atomicDetail: SequencerAtomicDetail | null,
  pauseDetail: SequencerPauseDetail | null,
  parallelDetail: SequencerParallelDetail | null
): string | null {
  const inline = compactText(inlineRemainder.replace(/^#.*$/, "").trim());

  const findValue = (key: string): string | null => {
    const pattern = new RegExp(`^\\s*${key}:\\s*(.+?)\\s*$`);
    const lines = snippet.split("\n");
    for (const line of lines) {
      const match = line.match(pattern);
      if (!match) {
        continue;
      }
      const value = match[1].replace(/\s+#.*$/, "").trim();
      if (value) {
        return value;
      }
    }
    return null;
  };

  const countListItems = (sectionKey: string): number => {
    const lines = snippet.split("\n");
    let sectionIndent: number | null = null;
    let count = 0;
    for (const line of lines) {
      const sectionMatch = line.match(
        new RegExp(`^(\\s*)${sectionKey}:\\s*(?:#.*)?$`)
      );
      if (sectionMatch) {
        sectionIndent = sectionMatch[1].length;
        continue;
      }
      if (sectionIndent === null) {
        continue;
      }
      const indent = line.match(/^\s*/)?.[0].length ?? 0;
      if (line.trim() && indent <= sectionIndent) {
        break;
      }
      if (line.trim().startsWith("- ")) {
        count += 1;
      }
    }
    return count;
  };

  const collectMappedKeys = (sectionKey: string, limit = 3): string[] => {
    const lines = snippet.split("\n");
    let sectionIndent: number | null = null;
    const keys: string[] = [];
    for (const line of lines) {
      const sectionMatch = line.match(
        new RegExp(`^(\\s*)${sectionKey}:\\s*(?:#.*)?$`)
      );
      if (sectionMatch) {
        sectionIndent = sectionMatch[1].length;
        continue;
      }
      if (sectionIndent === null) {
        continue;
      }
      const indent = line.match(/^\s*/)?.[0].length ?? 0;
      if (line.trim() && indent <= sectionIndent) {
        break;
      }
      const keyMatch = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:/);
      if (!keyMatch) {
        continue;
      }
      keys.push(keyMatch[1]);
      if (keys.length >= limit) {
        break;
      }
    }
    return keys;
  };

  const collectBindPairs = (): string[] => {
    const lines = snippet.split("\n");
    let bindIndent: number | null = null;
    const out: string[] = [];
    for (const line of lines) {
      const bindMatch = line.match(/^(\s*)bind:\s*(?:#.*)?$/);
      if (bindMatch) {
        bindIndent = bindMatch[1].length;
        continue;
      }
      if (bindIndent === null) {
        continue;
      }
      const indent = line.match(/^\s*/)?.[0].length ?? 0;
      if (line.trim() && indent <= bindIndent) {
        break;
      }
      const pairMatch = line.match(
        /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$/
      );
      if (!pairMatch) {
        continue;
      }
      out.push(`${pairMatch[1]} -> ${pairMatch[2].replace(/\s+#.*$/, "").trim()}`);
      if (out.length >= 3) {
        break;
      }
    }
    return out;
  };

  const findFirstGeneratorKind = (): string | null => {
    const lines = snippet.split("\n");
    let inIndent: number | null = null;
    let genIndent: number | null = null;
    for (const line of lines) {
      if (inIndent === null) {
        const inMatch = line.match(/^(\s*)in:\s*(?:#.*)?$/);
        if (inMatch) {
          inIndent = inMatch[1].length;
        }
        continue;
      }
      const indent = line.match(/^\s*/)?.[0].length ?? 0;
      if (line.trim() && indent <= inIndent) {
        break;
      }
      if (genIndent === null) {
        const genMatch = line.match(/^(\s*)gen:\s*(?:#.*)?$/);
        if (genMatch) {
          genIndent = genMatch[1].length;
        }
        continue;
      }
      if (line.trim() && indent <= genIndent) {
        break;
      }
      const keyMatch = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:/);
      if (keyMatch) {
        return keyMatch[1];
      }
    }
    return null;
  };

  const summarizeCall = (): string | null => {
    const device = findValue("device");
    const action = findValue("action");
    if (device && action) {
      return `${device}.${action}`;
    }
    return device || action;
  };

  const summarizeSet = (): string | null => {
    const device = findValue("device");
    const name = findValue("name");
    const value = findValue("value");
    if (device && name && value) {
      return `${device}.${name} = ${compactText(value, 56)}`;
    }
    if (name && value) {
      return `${name} = ${compactText(value, 56)}`;
    }
    return null;
  };

  const summarizeAssign = (): string | null => {
    const lines = snippet.split("\n");
    for (let i = 1; i < lines.length; i += 1) {
      const match = lines[i].match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$/);
      if (!match) {
        continue;
      }
      return `${match[1]} = ${compactText(match[2].replace(/\s+#.*$/, "").trim(), 56)}`;
    }
    return null;
  };

  if (kind === "call") {
    const summary = summarizeCall();
    if (summary) {
      return summary;
    }
  }
  if (kind === "sleep" && inline.length > 0) {
    return `sleep ${inline}`;
  }
  if (kind === "set") {
    const summary = summarizeSet();
    if (summary) {
      return summary;
    }
  }
  if (kind === "assign") {
    const summary = summarizeAssign();
    if (summary) {
      return summary;
    }
  }
  if (kind === "repeat") {
    const times = repeatDetail?.times ?? findValue("times");
    if (times) {
      return `repeat ${times}${childCount > 0 ? ` (${childCount} step${childCount === 1 ? "" : "s"})` : ""}`;
    }
  }
  if (kind === "for") {
    const binds =
      forDetail?.bind.map((entry) => `${entry.name} -> ${entry.value ?? "n/a"}`) ??
      collectBindPairs();
    const bindSummary = binds.length > 0 ? binds.join(", ") : "bindings";
    if (forDetail?.sourceMode === "direct") {
      return `${bindSummary} over expression`;
    }
    const generatorKind = forDetail?.generatorKind ?? findFirstGeneratorKind();
    if (generatorKind) {
      return `${bindSummary} over ${generatorKind}`;
    }
    return bindSummary;
  }
  if (kind === "adaptive") {
    const id = adaptiveDetail?.id;
    const controllerKind = adaptiveDetail?.controllerKind;
    const spaceKeys = adaptiveDetail?.space
      .slice(0, 3)
      .map((entry) => entry.name) ?? [];
    const parts = [
      id ?? null,
      controllerKind,
      spaceKeys.length > 0 ? `space: ${spaceKeys.join(", ")}` : null,
    ].filter((part): part is string => Boolean(part));
    if (parts.length > 0) {
      return parts.join(" | ");
    }
  }
  if (kind === "wait_until") {
    const timeout = findValue("timeout_s");
    if (timeout) {
      return `timeout ${timeout}s`;
    }
  }
  if (kind === "if" && ifDetail) {
    const parts = [`then ${ifDetail.thenCount}`];
    if (ifDetail.elseCount > 0) {
      parts.push(`else ${ifDetail.elseCount}`);
    }
    return parts.join(" | ");
  }
  if (kind === "while") {
    const firstCondition =
      whileDetail?.condition[0]?.name ?? (findValue("condition") ? "condition" : null);
    if (firstCondition) {
      return `while ${firstCondition}`;
    }
  }
  if (kind === "atomic" && atomicDetail?.name) {
    return `name=${atomicDetail.name}`;
  }
  if (kind === "pause" && pauseDetail?.reason) {
    return `pause ${compactText(pauseDetail.reason, 56)}`;
  }
  if (kind === "parallel" && parallelDetail) {
    return `${parallelDetail.branchCount} branch${parallelDetail.branchCount === 1 ? "" : "es"}`;
  }
  if (kind === "set_context") {
    const streamCount = countListItems("streams");
    const fieldKeys = collectMappedKeys("fields", 3);
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

  const lines = snippet.split("\n");
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
    let endIndex = lines.length - 1;
    for (let lineIndex = current.startIndex + 1; lineIndex < lines.length; lineIndex += 1) {
      const nextLine = lines[lineIndex];
      const trimmed = nextLine.trim();
      if (!trimmed || trimmed.startsWith("#")) {
        continue;
      }
      if (lineIndent(nextLine) <= current.indent) {
        endIndex = lineIndex - 1;
        break;
      }
    }
    current.endIndex = Math.max(current.startIndex, endIndex);
  }

  return flat;
}

function finalizeSummaries(
  nodes: readonly SequencerStepOutlineNode[],
  flatMap: ReadonlyMap<string, FlatStepNode>
): void {
  for (const node of nodes) {
    finalizeSummaries(node.children, flatMap);
    const flatNode = flatMap.get(node.id);
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
    node.summary = deriveSummary(
      node.kind,
      flatNode.inlineRemainder,
        node.snippet,
        node.children.length,
        adaptiveDetail,
        forDetail,
        repeatDetail,
        ifDetail,
        whileDetail,
        atomicDetail,
        pauseDetail,
        parallelDetail
      );
  }
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

  const flatMap = new Map<string, FlatStepNode>();
  const roots: SequencerStepOutlineNode[] = [];
  const stack: Array<{ indent: number; node: SequencerStepOutlineNode }> = [];

  for (const flatNode of flatNodes) {
    flatMap.set(flatNode.id, flatNode);
    const node: SequencerStepOutlineNode = {
      id: flatNode.id,
      kind: flatNode.kind,
      line: flatNode.line,
      endLine: flatNode.endIndex + 1,
      indent: flatNode.indent,
      branchLabel:
        flatNode.containerKey === "then" || flatNode.containerKey === "else"
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

    while (
      stack.length > 0 &&
      flatNode.indent <= stack[stack.length - 1].indent
    ) {
      stack.pop();
    }

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
