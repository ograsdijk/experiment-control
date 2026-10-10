import { describe, expect, it } from "vitest";
import { activeStepLine, resolveActiveStep } from "./active_step";
import { buildSequencerStepOutline } from "./outline";
import type { SequencerStepDetail } from "./types";

const YAML = [
  "version: 1", // 1
  "steps:", // 2
  "  - sleep: 1", // 3
  "  - repeat:", // 4
  "      times: 2", // 5
  "      do:", // 6
  "        - call:", // 7
  "            device: fs740", // 8
  "            action: flush", // 9
  "        - for:", // 10
  "            var: i", // 11
  "            values: [1, 2]", // 12
  "            do:", // 13
  "              - sleep: 1", // 14
  "  - call: {device: fs740, action: flush}", // 15
  "",
].join("\n");

const outline = buildSequencerStepOutline(YAML);

const detail = (over: Partial<SequencerStepDetail> = {}): SequencerStepDetail => ({
  kind: "call",
  summary: null,
  path: "steps[0]",
  line: 3,
  column: null,
  source: "a.yaml",
  branch: null,
  ...over,
});

const input = (over: Partial<Parameters<typeof activeStepLine>[0]> = {}) => ({
  runtimeState: "RUNNING",
  detail: detail(),
  loadedSource: "a.yaml",
  yamlDirty: false,
  ...over,
});

describe("activeStepLine", () => {
  it("returns the line while running or paused", () => {
    expect(activeStepLine(input())).toBe(3);
    expect(activeStepLine(input({ runtimeState: "PAUSED" }))).toBe(3);
    expect(activeStepLine(input({ runtimeState: "STOP_REQUESTED" }))).toBe(3);
  });

  it("shows nothing when not running", () => {
    for (const runtimeState of ["IDLE", "LOADED", "DONE", "ERROR", "STOPPED"]) {
      expect(activeStepLine(input({ runtimeState }))).toBeNull();
    }
  });

  it("shows nothing without a line or a detail", () => {
    expect(activeStepLine(input({ detail: null }))).toBeNull();
    expect(activeStepLine(input({ detail: detail({ line: null }) }))).toBeNull();
  });

  it("shows nothing when the editor text is not the loaded sequence", () => {
    expect(activeStepLine(input({ yamlDirty: true }))).toBeNull();
  });

  it("shows nothing for another source or an unknown source", () => {
    expect(activeStepLine(input({ detail: detail({ source: "sub.yaml" }) }))).toBeNull();
    expect(activeStepLine(input({ detail: detail({ source: null }) }))).toBeNull();
    expect(activeStepLine(input({ loadedSource: null }))).toBeNull();
  });
});

describe("resolveActiveStep", () => {
  it("highlights a leaf's whole block and bars its containers", () => {
    const active = resolveActiveStep(outline, 7)!;
    expect(active.highlight).toEqual({ from: 7, to: 9 });
    expect(active.bars).toEqual([{ from: 4, to: 14 }]);
    expect(active.ancestorIds).toHaveLength(1);
  });

  it("lists nested ancestors outermost first", () => {
    const active = resolveActiveStep(outline, 14)!;
    expect(active.highlight).toEqual({ from: 14, to: 14 });
    expect(active.bars).toEqual([
      { from: 4, to: 14 },
      { from: 10, to: 14 },
    ]);
  });

  it("highlights only the header when the container itself is active", () => {
    const repeat = resolveActiveStep(outline, 4)!;
    expect(repeat.highlight).toEqual({ from: 4, to: 6 });
    expect(repeat.bars).toEqual([]);
    const forStep = resolveActiveStep(outline, 10)!;
    expect(forStep.highlight).toEqual({ from: 10, to: 13 });
    expect(forStep.bars).toEqual([{ from: 4, to: 14 }]);
  });

  it("treats a one-line flow step as a one-line block", () => {
    const active = resolveActiveStep(outline, 15)!;
    expect(active.highlight).toEqual({ from: 15, to: 15 });
    expect(active.bars).toEqual([]);
  });

  it("returns null for a line that starts no step", () => {
    // Lines 8 and 12 are inside a step but start no step.
    expect(resolveActiveStep(outline, 4)).not.toBeNull();
    expect(resolveActiveStep(outline, 8)).toBeNull();
    expect(resolveActiveStep(outline, 12)).toBeNull();
  });

  it("returns null outside any step", () => {
    expect(resolveActiveStep(outline, 1)).toBeNull();
    expect(resolveActiveStep(outline, null)).toBeNull();
  });
});
