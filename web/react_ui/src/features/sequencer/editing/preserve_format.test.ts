import { describe, expect, it } from "vitest";
import { parseDocument } from "yaml";
import {
  buildSequencerOutlineMetadata,
  buildSequencerStepOutline,
  flattenSequencerStepOutline,
} from "../outline";
import {
  applyEditedCallStep,
  applyEditedForStep,
  applyEditedVars,
  toggleStepEnabled,
} from "../editing";
import type { SequencerStepOutlineNode } from "../types";

// Shapes taken from the state-preparation-b-detection sequences: a folded
// description, hand-formatted numbers, inline flow steps and a scan2d grid.
const SEQUENCE = [
  "version: 1",
  "meta:",
  "  name: Scan",
  "  description: >",
  "    A long folded description that the old vars writer reflowed into one",
  "    line whenever any var was edited.",
  "",
  "vars:",
  "  synth_device: synthhd_cavity2_1",
  "  # Keep freq_points odd.",
  "  freq_span_hz: 20.0e+6",
  "  freq_points: 11",
  "  ramp_step_hz: 2.5e6  # per command",
  "",
  "steps:",
  "  - call: {device: fs740, action: flush_timestamp_buffer}",
  "  - call:",
  "      device: pulseblaster",
  "      action: precompile",
  "      params:",
  '        name: "${profile}"',
  "  - for:",
  "      bind: {x: x, y: y, index: spot_index}",
  "      in:",
  "        gen:",
  "          scan2d:",
  "            center:",
  '              x: "${(grid_ax + grid_bx) / 2}"',
  '              y: "${(grid_ay + grid_by) / 2}"',
  "            size:",
  '              width: "${abs(grid_bx - grid_ax)}"',
  '              height: "${abs(grid_by - grid_ay)}"',
  '            pitch: "${grid_step}"',
  '          sample: {count: "${m_spots}", replace: true}',
  "      do:",
  '        - call: {device: zabertmm, action: move_absolute, params: {x: "${x}", y: "${y}"}}',
  "  - for:",
  "      bind: qswitch_delay_us",
  '      in: "${qswitch_delays_us}"',
  "      do:",
  "        - sleep: 1.0",
  "",
].join("\n");

function steps(yaml: string): SequencerStepOutlineNode[] {
  return flattenSequencerStepOutline(buildSequencerStepOutline(yaml));
}

function changedLines(a: string, b: string): string[] {
  const x = a.split("\n");
  const y = b.split("\n");
  let pre = 0;
  while (pre < x.length && pre < y.length && x[pre] === y[pre]) pre += 1;
  let suf = 0;
  while (
    suf < x.length - pre &&
    suf < y.length - pre &&
    x[x.length - 1 - suf] === y[y.length - 1 - suf]
  ) {
    suf += 1;
  }
  return y.slice(pre, y.length - suf);
}

describe("vars edits touch only their own entry", () => {
  const vars = buildSequencerOutlineMetadata(SEQUENCE).vars;

  it("is a no-op when nothing changed", () => {
    expect(applyEditedVars(SEQUENCE, vars)).toBe(SEQUENCE);
  });

  it("changes one line for one value, keeping comments and number formats", () => {
    const next = applyEditedVars(
      SEQUENCE,
      vars.map((entry) => (entry.name === "freq_points" ? { ...entry, value: "13" } : entry))
    );
    expect(changedLines(SEQUENCE, next)).toEqual(["  freq_points: 13"]);
  });

  it("keeps a trailing comment on the edited line", () => {
    const next = applyEditedVars(
      SEQUENCE,
      vars.map((entry) => (entry.name === "ramp_step_hz" ? { ...entry, value: "5e6" } : entry))
    );
    expect(changedLines(SEQUENCE, next)).toEqual(["  ramp_step_hz: 5e6  # per command"]);
  });

  it("adds, renames and removes entries in place", () => {
    const added = applyEditedVars(SEQUENCE, [...vars, { name: "n_traces", value: "16" }]);
    expect(changedLines(SEQUENCE, added)).toEqual(["  n_traces: 16"]);

    const renamed = applyEditedVars(
      SEQUENCE,
      vars.map((entry) => (entry.name === "freq_points" ? { ...entry, name: "num_points" } : entry))
    );
    expect(changedLines(SEQUENCE, renamed)).toEqual(["  num_points: 11"]);

    const removed = applyEditedVars(
      SEQUENCE,
      vars.filter((entry) => entry.name !== "freq_points")
    );
    expect(changedLines(SEQUENCE, removed)).toEqual([]);
    expect((parseDocument(removed).toJSON() as { vars: object }).vars).not.toHaveProperty(
      "freq_points"
    );
    expect(removed).toContain("    line whenever any var was edited.");
  });
});

describe("step edits keep the step's formatting", () => {
  it("leaves the text unchanged on no-op saves of every call and for step", () => {
    for (const node of steps(SEQUENCE)) {
      if (node.callDetail) {
        const d = node.callDetail;
        expect(
          applyEditedCallStep(SEQUENCE, node, d.targetKind, d.device ?? "", d.process ?? "", d.action ?? "", d.params)
        ).toBe(SEQUENCE);
      }
      if (node.forDetail) {
        const d = node.forDetail;
        expect(
          applyEditedForStep(SEQUENCE, node, d.bind, d.sourceMode, d.generatorKind, d.directValue ?? "", d.generatorModifiers, d.iterableConfig)
        ).toBe(SEQUENCE);
      }
    }
  });

  it("edits one param of an inline call without restyling it", () => {
    const node = steps(SEQUENCE).find((n) => n.kind === "call" && n.snippet.includes("move_absolute"))!;
    const d = node.callDetail!;
    const params = d.params.map((p) => (p.name === "y" ? { ...p, value: "${y + 1}" } : p));
    const next = applyEditedCallStep(SEQUENCE, node, d.targetKind, d.device ?? "", d.process ?? "", d.action ?? "", params);
    expect(changedLines(SEQUENCE, next)).toEqual([
      '        - call: {device: zabertmm, action: move_absolute, params: {x: "${x}", y: "${y + 1}"}}',
    ]);
  });

  it("edits a for bind without rewriting its scan2d grid", () => {
    const node = steps(SEQUENCE).find((n) => n.snippet.includes("scan2d"))!;
    const d = node.forDetail!;
    const bind = d.bind.map((b) => (b.name === "index" ? { ...b, value: "spot" } : b));
    const next = applyEditedForStep(SEQUENCE, node, bind, d.sourceMode, d.generatorKind, d.directValue ?? "", d.generatorModifiers, d.iterableConfig);
    expect(changedLines(SEQUENCE, next)).toEqual(["      bind: {x: x, y: y, index: spot}"]);
  });
});

describe("disable toggle", () => {
  it("keeps every step valid and round-trips exactly", () => {
    for (const node of steps(SEQUENCE)) {
      const disabled = toggleStepEnabled(SEQUENCE, node);
      const doc = parseDocument(disabled);
      expect(doc.errors).toEqual([]);
      const again = steps(disabled).find((n) => n.line === node.line)!;
      expect(again.disabled).toBe(true);
      expect(toggleStepEnabled(disabled, again)).toBe(SEQUENCE);
    }
  });
});
