import { describe, expect, it } from "vitest";
import {
  buildRunOverrides,
  formVarNames,
  initialFormValues,
  linspace,
  optionsFromCallResult,
  optionsOf,
  otherTargetsFor,
  parseRunForm,
  runFormFromLibraryResult,
  rangeProblems,
  rangeValues,
  sectionBlocks,
  settleFormValues,
  summaryFigures,
  targetSetValues,
  type RunForm,
  type ScanTarget,
} from "./run_form";

const YAML = `
version: 1
vars:
  targets: [{id: a, kind: source_frequency, device: syn}]
  target: {id: a, kind: source_frequency, device: syn}
  relative: true
  lo: -1.0
  hi: 1.0
  points: 5
  passes: 3
  bursts: 2
  shots: 10
  monitor_on: true
  channels: [1, 2]
  channel_options: [{value: 1, label: First}, {value: 2}]
  untouched: 7
form:
  title: Scan
  presets: [{label: Off, set: {monitor_on: false}}]
  summary:
    - {label: Values, product: [points]}
    - {label: Total, product: [points, passes, bursts, shots]}
  sections:
    - title: Target
      fields:
        - {type: target, var: target, catalog: targets}
        - {type: range, target: target, relative: relative, from: lo, to: hi, points: points}
    - title: Monitor
      toggle: monitor_on
      fields:
        - {type: chips, var: channels, options: channel_options}
steps:
  - sleep: 0
`;

describe("parseRunForm", () => {
  it("reads the form and vars", () => {
    const parsed = parseRunForm(YAML);
    expect(parsed?.form.title).toBe("Scan");
    expect(parsed?.form.sections).toHaveLength(2);
    expect(parsed?.vars.points).toBe(5);
  });

  it("reads a sequencer.library.form result", () => {
    const parsed = runFormFromLibraryResult({
      sequence_id: "scan",
      form: { sections: [{ fields: [{ type: "integer", var: "passes" }] }] },
      vars: { passes: 3 },
    });
    expect(parsed?.vars.passes).toBe(3);
    expect(parsed?.form.sections[0].fields[0]).toMatchObject({ var: "passes" });
    expect(runFormFromLibraryResult({ form: { sections: [] } })).toBeNull();
    expect(runFormFromLibraryResult(null)).toBeNull();
  });

  it("is null without a form or for broken YAML", () => {
    expect(parseRunForm("version: 1\nsteps: []\n")).toBeNull();
    expect(parseRunForm("form: [oops")).toBeNull();
  });
});

describe("overrides", () => {
  it("sends every var the form sets, and only those", () => {
    const parsed = parseRunForm(YAML)!;
    expect(formVarNames(parsed.form).sort()).toEqual(
      ["channels", "hi", "lo", "points", "relative", "target", "monitor_on"].sort()
    );
    const values = { ...initialFormValues(parsed), lo: -2.0, untouched: 99 };
    const overrides = buildRunOverrides(parsed.form, values);
    expect(overrides.lo).toBe(-2.0);
    expect(overrides.channels).toEqual([1, 2]);
    expect("untouched" in overrides).toBe(false);
  });

  it("multiplies each summary figure's vars", () => {
    const parsed = parseRunForm(YAML)!;
    expect(summaryFigures(parsed.form.summary, parsed.vars)).toEqual([
      { label: "Values", value: 5 },
      { label: "Total", value: 300 },
    ]);
    expect(summaryFigures(parsed.form.summary, { points: "x" })[0].value).toBeNull();
  });
});

describe("options", () => {
  it("labels default to the value", () => {
    expect(optionsOf([{ value: 1, label: "First" }, { value: 2 }, "bad"])).toEqual([
      { value: 1, label: "First" },
      { value: 2, label: "2" },
    ]);
  });

  it("reads an options_call result", () => {
    const call = { device: "timer", action: "list_profiles", list: "profiles", value: "name" };
    const result = { profiles: [{ name: "profile_a" }, { name: "profile_b" }] };
    expect(optionsFromCallResult(call, result)).toEqual([
      { value: "profile_a", label: "profile_a" },
      { value: "profile_b", label: "profile_b" },
    ]);
    expect(optionsFromCallResult(call, null)).toEqual([]);
  });
});

describe("range", () => {
  it("matches the sequencer's linspace", () => {
    expect(linspace(0, 1, 5)).toEqual([0, 0.25, 0.5, 0.75, 1]);
    expect(linspace(3, 9, 1)).toEqual([3]);
  });

  it("is relative to the origin, and unknown until the origin is", () => {
    expect(rangeValues(true, -1, 1, 3, 10)).toEqual([9, 10, 11]);
    expect(rangeValues(false, 2, 4, 3, 10)).toEqual([2, 3, 4]);
    expect(rangeValues(true, -1, 1, 3, null)).toBeNull();
  });

  it("flags values outside the target's limits", () => {
    const target = { id: "p", min: 0, max: 1500, unit: "V" } as unknown as ScanTarget;
    expect(rangeProblems(target, [100, 200])).toEqual([]);
    expect(rangeProblems(target, [-5, 1600])).toEqual([
      "The range goes below the minimum, 0 V",
      "The range goes above the maximum, 1500 V",
    ]);
    expect(rangeProblems(null, null)).toEqual(["Choose a target"]);
  });
});

describe("otherTargetsFor", () => {
  const method = (name: string, params: string[]) => ({
    name,
    kind: "method",
    params: params.map((p) => ({ name: p })),
  });

  it("offers set_/get_ pairs with a value, or a channel and a value", () => {
    const targets = otherTargetsFor("dev", [
      method("set_level", ["value"]),
      method("get_level", []),
      method("set_power", ["channel", "dbm"]),
      method("get_power", ["channel"]),
      method("set_orphan", ["x"]),
      method("set_many", ["a", "b"]),
      method("get_many", []),
    ]);
    expect(targets.map((t) => [t.set_action, t.value_param, t.channel_param])).toEqual([
      ["set_level", "value", null],
      ["set_power", "dbm", "channel"],
    ]);
    expect(targets.every((t) => t.kind === "generic")).toBe(true);
  });

  it("adds the sequence's defaults without replacing the call's keys", () => {
    const [target] = otherTargetsFor(
      "dev",
      [method("set_level", ["value"]), method("get_level", [])],
      { settle_s: 0.5, kind: "nope", set_action: "nope" }
    );
    expect(target.settle_s).toBe(0.5);
    expect(target.kind).toBe("generic");
    expect(target.set_action).toBe("set_level");
  });
});

describe("target sets and chip includes", () => {
  const form: RunForm = {
    presets: [],
    summary: [],
    sections: [
      {
        fields: [
          { type: "target", var: "target", catalog: "targets", sets: { park: "wm" } },
          { type: "chips", var: "cycle", options: "channels", includes: "park" },
          { type: "select", var: "park", options: "channels" },
        ],
      },
    ],
  };
  const vars = { channels: [1, 2, 5, 16].map((value) => ({ value, label: String(value) })) };

  it("sets the var from the chosen target and adds it to the chips in order", () => {
    const values = { target: { id: "d1", wm: 5 }, park: 16, cycle: [1, 16] };
    expect(targetSetValues(form, values)).toEqual({ park: 5 });
    expect(settleFormValues(form, vars, values)).toMatchObject({ park: 5, cycle: [1, 5, 16] });
  });

  it("leaves the var to the user when the target has no value for it", () => {
    const values = { target: { id: "x", wm: null }, park: 16, cycle: [1] };
    expect(targetSetValues(form, values)).toEqual({});
    expect(settleFormValues(form, vars, values)).toMatchObject({ park: 16, cycle: [1, 16] });
  });
});

describe("sectionBlocks", () => {
  it("puts wide sections on their own and alternates the rest unless placed", () => {
    const s = (title: string, extra: object = {}) => ({ title, fields: [], ...extra });
    const blocks = sectionBlocks([
      s("target", { wide: true }),
      s("a"),
      s("b"),
      s("c", { column: "left" }),
      s("d"),
    ]);
    const titles = (list: { title?: string }[]) => list.map((x) => x.title);
    expect(
      blocks.map((b) => ("wide" in b ? ["wide", b.wide.title] : [titles(b.left), titles(b.right)]))
    ).toEqual([
      ["wide", "target"],
      [["a", "c", "d"], ["b"]],
    ]);
  });
});
