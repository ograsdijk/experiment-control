import { parse } from "yaml";
import { describe, expect, it } from "vitest";
import {
  deleteStep,
  duplicateStep,
  insertStepAtTopLevel,
  insertStepBelow,
  insertStepInside,
  moveStepDown,
  toggleStepEnabled,
} from "./editing/tree_ops";
import { buildSequencerStepOutline, flattenSequencerStepOutline } from "./outline";
import type { SequencerStepOutlineNode } from "./types";

// The same sequence in both list styles. PyYAML (yaml.safe_dump) writes
// lists in their key's column, as UNINDENTED does.
const INDENTED = [
  "version: 1",
  "steps:",
  "  - for:",
  "      bind: { value: level }",
  "      in: [1, 2]",
  "      do:",
  "        - call:",
  "            device: dev_a",
  "            action: read",
  "        - sleep: 0.5",
  "  - if:",
  "      condition: { all: [] }",
  "      then:",
  "        - sleep: 1",
  "      else:",
  "        - sleep: 2",
  "  - sleep: 3",
  "",
].join("\n");

const UNINDENTED = [
  "version: 1",
  "steps:",
  "- for:",
  "    bind: { value: level }",
  "    in: [1, 2]",
  "    do:",
  "    - call:",
  "        device: dev_a",
  "        action: read",
  "    - sleep: 0.5",
  "- if:",
  "    condition: { all: [] }",
  "    then:",
  "    - sleep: 1",
  "    else:",
  "    - sleep: 2",
  "- sleep: 3",
  "",
].join("\n");

type Shape = { kind: string; path: string | null; children: Shape[] };

function shape(nodes: SequencerStepOutlineNode[]): Shape[] {
  return nodes.map((node) => ({ kind: node.kind, path: node.path, children: shape(node.children) }));
}

function find(text: string, path: string): SequencerStepOutlineNode {
  const node = flattenSequencerStepOutline(buildSequencerStepOutline(text)).find(
    (candidate) => candidate.path === path
  );
  if (!node) {
    throw new Error(`no step at ${path}`);
  }
  return node;
}

describe("outline of lists in their key's column", () => {
  it("matches the indented style", () => {
    const indented = shape(buildSequencerStepOutline(INDENTED));
    expect(indented.length).toBe(3);
    expect(shape(buildSequencerStepOutline(UNINDENTED))).toEqual(indented);
  });

  it("gives each step its own lines", () => {
    const forStep = find(UNINDENTED, "steps[0]");
    expect([forStep.line, forStep.endLine]).toEqual([3, 10]);
    expect(find(UNINDENTED, "steps[0].for.do[1]").line).toBe(10);
    expect(find(UNINDENTED, "steps[1].if.else[0]").line).toBe(16);
  });

  it("keeps the YAML valid through the step edits", () => {
    const expectSteps = (text: string, count: number) => {
      expect(() => parse(text)).not.toThrow();
      expect((parse(text) as { steps: unknown[] }).steps.length).toBe(count);
      expect(buildSequencerStepOutline(text).length).toBe(count);
    };
    expectSteps(duplicateStep(UNINDENTED, find(UNINDENTED, "steps[0]")), 4);
    expectSteps(deleteStep(UNINDENTED, find(UNINDENTED, "steps[1]")), 2);
    expectSteps(insertStepBelow(UNINDENTED, find(UNINDENTED, "steps[2]"), "sleep"), 4);
    expectSteps(insertStepAtTopLevel(UNINDENTED, "sleep"), 4);
    expectSteps(toggleStepEnabled(UNINDENTED, find(UNINDENTED, "steps[2]")), 3);

    const moved = moveStepDown(UNINDENTED, find(UNINDENTED, "steps[0]"), find(UNINDENTED, "steps[1]"));
    expect(shape(buildSequencerStepOutline(moved)).map((step) => step.kind)).toEqual(["if", "for", "sleep"]);

    const inside = insertStepInside(UNINDENTED, find(UNINDENTED, "steps[0]"), "sleep", "do");
    expect(() => parse(inside)).not.toThrow();
    expect(find(inside, "steps[0]").children.length).toBe(3);
  });
});
