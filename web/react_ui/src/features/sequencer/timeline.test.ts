import { describe, expect, it } from "vitest";
import { buildSequencerStepOutline } from "./outline";
import { normalizeSequencerLoops, normalizeSequencerPlan, planStepsByLine, type SequencerPlan } from "./plan";
import {
  buildTimelineSteps,
  collapseDoneBlocks,
  doneInPass,
  overviewSegments,
  parentPlanPath,
  passBreakdown,
  timelineBlocks,
  unwrapTemplate,
  wrapTimeline,
  type TimelineStep,
} from "./timeline";

// A cut-down laser_frequency_randomized_grid_scan: setup, a try whose body
// loops over frequencies (spots x traces inside), and a finally.
const YAML = `version: 1
vars:
  n_traces: 3
steps:
  - assign:
      center: {call: {device: synth, action: get_frequency}}
  - call: {process: ramp, action: prepare_targets, params: {values: "\${grid}"}}
  - try:
      do:
        - use: pmt_hv_prepare
        - call: {device: fs740, action: flush}
        - sleep: 2
        - for:
            bind: {value: freq_hz}
            in: {gen: {values: "\${targets}"}}
            do:
              - call: {process: ramp, action: start}
              - wait_until: {timeout_s: 60, condition: true}
              - for:
                  bind: {x: x, y: y}
                  in: {gen: {scan2d: {}, sample: {count: 4}}}
                  do:
                    - call: {device: mirror, action: move}
                    - repeat:
                        times: "\${n_traces}"
                        do:
                          - call: {device: scope, action: read}
      finally:
        - call: {device: yag, action: disarm}
        - call: {device: gauge, action: on}
`;

const outline = buildSequencerStepOutline(YAML);
const lineOf = (needle: string): number => {
  const index = YAML.split("\n").findIndex((line) => line.includes(needle));
  // A for step's line is the `- for:` just above its `bind:`.
  return needle.startsWith("bind:") ? index : index + 1;
};

function entry(path: string, needle: string, kind: string, count: number, extra: Record<string, unknown> = {}) {
  return {
    path,
    line: lineOf(needle),
    source: "scan.yaml",
    use_id: null,
    kind,
    summary: needle,
    disabled: false,
    count,
    certainty: "exact",
    note: null,
    runs_done: 0,
    mean_s: null,
    ...extra,
  };
}

function rawPlan(overrides: (e: Record<string, unknown>) => Record<string, unknown> = (e) => e) {
  // The parser wraps everything in a try for `requires:` in the real
  // sequence; here the paths are plain, the matching is by line anyway.
  const steps = [
    entry("steps[0]", "- assign:", "assign", 1),
    entry("steps[1]", "prepare_targets", "call", 1),
    entry("steps[2]", "- try:", "try", 1),
    entry("steps[2].try.do[0]", "use: pmt_hv_prepare", "use", 1),
    { ...entry("steps[2].try.do[0].use.steps[0]", "@@", "for", 1, { iterations: 2 }), line: null, use_id: "pmt_hv_prepare", summary: "for pmt_channel" },
    { ...entry("steps[2].try.do[0].use.steps[0].for.do[0]", "@@", "call", 2), line: null, use_id: "pmt_hv_prepare", summary: "call iseg.ensure_on" },
    { ...entry("steps[2].try.do[0].use.steps[1]", "@@", "wait_until", 1, { timeout_s: 300 }), line: null, use_id: "pmt_hv_prepare", summary: "wait_until ready" },
    entry("steps[2].try.do[1]", "action: flush", "call", 1),
    entry("steps[2].try.do[2]", "- sleep: 2", "sleep", 1, { seconds: 2 }),
    entry("steps[2].try.do[3]", "bind: {value: freq_hz}", "for", 1, {
      iterations: 11,
      iterations_certainty: "inferred",
      iterations_note: "targets comes from ramp.prepare_targets; assumed one item per entry of grid (11)",
    }),
    entry("steps[2].try.do[3].for.do[0]", "action: start", "call", 11, { certainty: "inferred" }),
    entry("steps[2].try.do[3].for.do[1]", "wait_until", "wait_until", 11, { certainty: "inferred", timeout_s: 60 }),
    entry("steps[2].try.do[3].for.do[2]", "bind: {x: x, y: y}", "for", 11, { certainty: "inferred", iterations: 44 }),
    entry("steps[2].try.do[3].for.do[2].for.do[0]", "action: move", "call", 44, { certainty: "inferred" }),
    entry("steps[2].try.do[3].for.do[2].for.do[1]", "- repeat:", "repeat", 44, { certainty: "inferred", iterations: 132 }),
    entry("steps[2].try.do[3].for.do[2].for.do[1].repeat.do[0]", "action: read", "call", 132, { certainty: "inferred" }),
    entry("steps[2].try.finally[0]", "action: disarm", "call", 1),
    entry("steps[2].try.finally[1]", "action: on", "call", 1),
  ].map(overrides);
  return { run_id: 1, state: "IDLE", live: false, total_runs: 220, certainty: "inferred", steps };
}

function stepsFor(plan: SequencerPlan | null, expanded: ReadonlySet<string> = new Set()): TimelineStep[] {
  return buildTimelineSteps(outline, plan, planStepsByLine(plan), expanded);
}

describe("timeline layout", () => {
  const plan = normalizeSequencerPlan(rawPlan())!;

  it("matches plan entries to outline steps by line", () => {
    const steps = stepsFor(plan);
    expect(steps.map((step) => step.kind)).toEqual(["assign", "call", "try"]);
    const tryStep = steps[2];
    expect(tryStep.children.map((child) => child.kind)).toEqual(["use", "call", "sleep", "for", "finally"]);
    const freq = tryStep.children[3];
    expect(freq.plan?.iterations).toBe(11);
    expect(freq.plan?.iterationsCertainty).toBe("inferred");
    const read = freq.children[2].children[1].children[0];
    expect(read.plan?.count).toBe(132);
  });

  it("gives every container a band over its steps and wraps onto lines", () => {
    const blocks = timelineBlocks(stepsFor(plan));
    // assign, call, use, flush, sleep, start, wait, move, read, disarm, on
    expect(blocks).toHaveLength(11);
    expect(blocks[8].ancestors.map((a) => a.kind)).toEqual(["try", "for", "for", "repeat"]);

    const lines = wrapTimeline(blocks, 6);
    expect(lines.map((line) => line.blocks.length)).toEqual([6, 5]);
    const tryBands = lines.map((line) => line.bands.find((band) => band.step.kind === "try")!);
    expect(tryBands[0]).toMatchObject({ col0: 3, col1: 7, continuesFromPrevious: false, continuesOnNext: true });
    expect(tryBands[1]).toMatchObject({ col0: 1, col1: 6, continuesFromPrevious: true, continuesOnNext: false });
    // The frequency loop starts on line 1 and ends on line 2.
    const freqBands = lines.map((line) =>
      line.bands.find((band) => band.step.kind === "for" && band.depth === 1)
    );
    expect(freqBands[0]).toMatchObject({ col0: 6, col1: 7, continuesOnNext: true });
    expect(freqBands[1]).toMatchObject({ col0: 1, col1: 4, continuesFromPrevious: true });
    expect(lines[1].bandRows).toBe(4);
    const finallyBand = lines[1].bands.find((band) => band.step.kind === "finally");
    expect(finallyBand).toMatchObject({ depth: 1, col0: 4, col1: 6 });
  });

  it("shows the steps of a use: sub-sequence when it is expanded", () => {
    const useId = stepsFor(plan)[2].children[0].id;
    const expanded = stepsFor(plan, new Set([useId]));
    const use = expanded[2].children[0];
    expect(use.children.map((child) => child.kind)).toEqual(["for", "wait_until"]);
    expect(use.children[0].children[0].plan?.count).toBe(2);
    expect(timelineBlocks(expanded)).toHaveLength(12);
    expect(parentPlanPath("a.use.steps[0].for.do[1]")).toBe("a.use.steps[0]");
    expect(parentPlanPath("a.use.steps[0]")).toBe("a");
    expect(parentPlanPath("steps[2].try.finally[0]")).toBe("steps[2]");
  });

  it("splits the overview into setup, the frequency loop and finally", () => {
    const segments = overviewSegments(timelineBlocks(stepsFor(plan)), [], false);
    expect(segments.map((s) => s.kind)).toEqual(["steps", "loop", "finally"]);
    expect(segments[1].passes).toBe(11);
    expect(segments[0].label).toBe("5 steps");
    const shares = segments.map((s) => s.share);
    expect(shares.reduce((a, b) => a + b, 0)).toBeCloseTo(1);
    expect(shares[1]).toBeGreaterThan(0.8);
  });

  it("follows a live run: folds finished setup, marks the pass and its position", () => {
    const livePlan = normalizeSequencerPlan({
      ...rawPlan((e) => {
        const path = String(e.path);
        const done: Record<string, number> = {
          "steps[0]": 1,
          "steps[1]": 1,
          "steps[2]": 1,
          "steps[2].try.do[0]": 1,
          "steps[2].try.do[1]": 1,
          "steps[2].try.do[2]": 1,
          "steps[2].try.do[3]": 1,
          "steps[2].try.do[3].for.do[0]": 6,
          "steps[2].try.do[3].for.do[1]": 6,
          "steps[2].try.do[3].for.do[2]": 5,
          "steps[2].try.do[3].for.do[2].for.do[0]": 23,
          "steps[2].try.do[3].for.do[2].for.do[1]": 22,
          "steps[2].try.do[3].for.do[2].for.do[1].repeat.do[0]": 68,
        };
        return { ...e, certainty: "exact", runs_done: done[path] ?? 0, mean_s: done[path] ? 0.1 : null };
      }),
      live: true,
    })!;
    const loops = normalizeSequencerLoops([
      { kind: "for", line: lineOf("bind: {value: freq_hz}"), iteration: 6, count: 11, vars: ["freq_hz"], values: [] },
      { kind: "for", line: lineOf("bind: {x: x, y: y}"), iteration: 3, count: 4, vars: ["x", "y"] },
      { kind: "repeat", line: lineOf("- repeat:"), iteration: 2, count: 3 },
    ]);
    const steps = stepsFor(livePlan);
    const blocks = timelineBlocks(steps);
    const folded = collapseDoneBlocks(blocks, true, new Set());
    // assign + call fold (2 is too few: stays); use, flush, sleep fold into one.
    expect(folded.map((b) => b.type)).toEqual([
      "step",
      "step",
      "collapsed",
      "step",
      "step",
      "step",
      "step",
      "step",
      "step",
    ]);
    const collapsed = folded[2];
    expect(collapsed.type === "collapsed" ? collapsed.steps.length : 0).toBe(3);
    expect(collapseDoneBlocks(blocks, true, new Set([collapsed.type === "collapsed" ? collapsed.id : ""]))).toHaveLength(11);
    expect(collapseDoneBlocks(blocks, false, new Set())).toHaveLength(11);

    const read = steps[2].children[3].children[2].children[1].children[0];
    const done = doneInPass(folded, read.id);
    const freq = steps[2].children[3];
    expect([...done].sort()).toEqual(
      [freq.children[0].id, freq.children[1].id, freq.children[2].children[0].id, steps[0].id, steps[1].id].sort()
    );

    const segments = overviewSegments(folded, loops, true);
    expect(segments[1]).toMatchObject({ kind: "loop", passes: 11, currentPass: 6, passesDone: 5 });
    expect(segments[0].fractionDone).toBe(1);

    const pass = passBreakdown(freq);
    expect(pass).not.toBeNull();
    expect(pass!.parts.map((part) => part.kind)).toEqual(["call", "wait_until", "for"]);
    // means x counts / 11 passes: 0.1*11/11, 0.1*11/11, (0.1*44 + 0.1*132)/11
    expect(pass!.parts[2].seconds).toBeCloseTo(1.6);
  });

  it("folds a finished setup loop together with the setup around it", () => {
    const yaml = `version: 1
steps:
  - call: {device: a, action: one}
  - for:
      bind: {value: ch}
      in: [1, 2]
      do:
        - call: {device: wm, action: move}
        - sleep: 1
  - call: {device: a, action: two}
  - repeat:
      times: 5
      do:
        - call: {device: scope, action: read}
`;
    const tree = buildSequencerStepOutline(yaml);
    const lines = yaml.split("\n");
    const at = (needle: string) => lines.findIndex((line) => line.includes(needle)) + 1;
    const step = (path: string, needle: string, kind: string, count: number, done: number) => ({
      path,
      line: at(needle),
      kind,
      count,
      runs_done: done,
      certainty: "exact",
      disabled: false,
      use_id: null,
    });
    const plan = normalizeSequencerPlan({
      live: true,
      steps: [
        step("steps[0]", "action: one", "call", 1, 1),
        step("steps[1]", "- for:", "for", 1, 1),
        step("steps[1].for.do[0]", "action: move", "call", 2, 2),
        step("steps[1].for.do[1]", "sleep: 1", "sleep", 2, 2),
        step("steps[2]", "action: two", "call", 1, 1),
        step("steps[3]", "- repeat:", "repeat", 1, 1),
        step("steps[3].repeat.do[0]", "action: read", "call", 5, 2),
      ],
    })!;
    const steps = buildTimelineSteps(tree, plan, planStepsByLine(plan), new Set());
    const loops = normalizeSequencerLoops([
      { kind: "repeat", line: at("- repeat:"), iteration: 3, count: 5 },
    ]);
    const folded = collapseDoneBlocks(timelineBlocks(steps), true, new Set(), loops);
    expect(folded.map((b) => b.type)).toEqual(["collapsed", "step"]);
    expect(folded[0].type === "collapsed" ? folded[0].steps.length : 0).toBe(4);
    expect(folded[0].ancestors).toEqual([]);
    // The loop the run is still inside does not fold, even with its steps done.
    const stillIn = normalizeSequencerLoops([{ kind: "for", line: at("- for:"), iteration: 2, count: 2 }]);
    expect(collapseDoneBlocks(timelineBlocks(steps), true, new Set(), stillIn).map((b) => b.type)).toEqual([
      "step",
      "step",
      "step",
      "step",
      "step",
    ]);
  });

  it("drops YAML quotes from a templated name", () => {
    expect(unwrapTemplate('"ch${synthhd_channel}_pll_locked"')).toBe("ch${synthhd_channel}_pll_locked");
    expect(unwrapTemplate('"${wm_dwell_s}"')).toBe("wm_dwell_s");
  });

  it("puts if branches side by side in one block", () => {
    const yaml = `version: 1
steps:
  - call: {device: d, action: lock_state, save_as: locked}
  - if:
      condition: {eq: ["\${locked}", true]}
      then:
        - call: {device: scope, action: acquire}
        - call: {device: scope, action: save}
      else:
        - pause: {reason: laser unlocked}
`;
    const steps = buildTimelineSteps(buildSequencerStepOutline(yaml), null, new Map(), new Set());
    const blocks = timelineBlocks(steps);
    expect(blocks.map((b) => b.type)).toEqual(["step", "lanes"]);
    expect(blocks[1].width).toBe(2);
    const lanes = blocks[1].type === "lanes" ? blocks[1].step.lanes : null;
    expect(lanes?.map((lane) => [lane.label, lane.steps.length])).toEqual([
      ["then", 2],
      ["else", 1],
    ]);
  });
});
