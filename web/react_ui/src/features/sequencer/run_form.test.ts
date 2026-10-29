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
  rangeProblems,
  rangeValues,
  totalShots,
  type ScanTarget,
} from "./run_form";

const YAML = `
version: 1
vars:
  targets: [{id: a, kind: synthhd_frequency, device: syn}]
  target: {id: a, kind: synthhd_frequency, device: syn}
  relative: true
  lo: -1.0
  hi: 1.0
  points: 5
  passes: 3
  bursts: 2
  shots: 10
  wm_on: true
  channels: [1, 2]
  channel_options: [{value: 1, label: RC}, {value: 2}]
  untouched: 7
form:
  title: Scan
  presets: [{label: Off, set: {wm_on: false}}]
  summary: {points: points, passes: passes, bursts: bursts, shots: shots}
  sections:
    - title: Target
      fields:
        - {type: target, var: target, catalog: targets}
        - {type: range, target: target, relative: relative, from: lo, to: hi, points: points}
    - title: Wavemeter
      toggle: wm_on
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

  it("is null without a form or for broken YAML", () => {
    expect(parseRunForm("version: 1\nsteps: []\n")).toBeNull();
    expect(parseRunForm("form: [oops")).toBeNull();
  });
});

describe("overrides", () => {
  it("sends every var the form sets, and only those", () => {
    const parsed = parseRunForm(YAML)!;
    expect(formVarNames(parsed.form).sort()).toEqual(
      ["channels", "hi", "lo", "points", "relative", "target", "wm_on"].sort()
    );
    const values = { ...initialFormValues(parsed), lo: -2.0, untouched: 99 };
    const overrides = buildRunOverrides(parsed.form, values);
    expect(overrides.lo).toBe(-2.0);
    expect(overrides.channels).toEqual([1, 2]);
    expect("untouched" in overrides).toBe(false);
  });

  it("multiplies the summary vars into total shots", () => {
    const parsed = parseRunForm(YAML)!;
    expect(totalShots(parsed.form.summary, parsed.vars)).toEqual({ points: 5, shots: 300 });
  });
});

describe("options", () => {
  it("labels default to the value", () => {
    expect(optionsOf([{ value: 1, label: "RC" }, { value: 2 }, "bad"])).toEqual([
      { value: 1, label: "RC" },
      { value: 2, label: "2" },
    ]);
  });

  it("reads an options_call result", () => {
    const call = { device: "pb", action: "list_profiles", list: "profiles", value: "name" };
    const result = { profiles: [{ name: "yag_16hz" }, { name: "cu1" }] };
    expect(optionsFromCallResult(call, result)).toEqual([
      { value: "yag_16hz", label: "yag_16hz" },
      { value: "cu1", label: "cu1" },
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
    expect(targets.every((t) => t.kind === "generic" && t.lock === "none")).toBe(true);
  });
});
