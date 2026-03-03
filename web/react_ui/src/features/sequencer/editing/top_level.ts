import type { SequencerOutlineMetadataEntry } from "../types";
import {
  buildTopLevelMetadataSectionLines,
  findTopLevelSectionRange,
  joinLines,
  splitLines,
} from "./shared";

function applyEditedTopLevelMetadataSection(
  yamlText: string,
  key: "vars" | "context_columns",
  replacement: string[]
): string {
  const { lines, hasTrailingNewline } = splitLines(yamlText);
  const range = findTopLevelSectionRange(lines, key);
  let nextLines: string[];
  if (range) {
    const startIndex = range.startLine - 1;
    const endIndex = range.endLine - 1;
    nextLines = [
      ...lines.slice(0, startIndex),
      ...replacement,
      ...lines.slice(endIndex + 1),
    ];
  } else {
    const stepsIndex = lines.findIndex((line) => /^steps:\s*/.test(line.trim()));
    if (stepsIndex >= 0) {
      nextLines = [
        ...lines.slice(0, stepsIndex),
        ...replacement,
        ...lines.slice(stepsIndex),
      ];
    } else {
      nextLines = [...lines, ...replacement];
    }
  }
  return joinLines(nextLines, hasTrailingNewline);
}

export function applyEditedVars(
  yamlText: string,
  entries: SequencerOutlineMetadataEntry[]
): string {
  return applyEditedTopLevelMetadataSection(
    yamlText,
    "vars",
    buildTopLevelMetadataSectionLines("vars", entries)
  );
}

export function applyEditedContextColumns(
  yamlText: string,
  entries: SequencerOutlineMetadataEntry[]
): string {
  return applyEditedTopLevelMetadataSection(
    yamlText,
    "context_columns",
    buildTopLevelMetadataSectionLines("context_columns", entries)
  );
}
