import { Text } from "@codemirror/state";
import { describe, expect, it } from "vitest";
import {
  describeTemplate,
  describeVarDefinition,
  extractTemplateExpressions,
  formatNumber,
  formatShort,
  normalizeResolvedVars,
  overridesForDisplay,
  scalarEnv,
  splitTemplates,
  substituteFormula,
  type ValueContext,
} from "./resolved_values";
import { varDefinitionLines } from "./value_chips_editor";
import { buildSequencerStepOutline } from "./outline";
import { buildRuntimeOrigins } from "./resolved_values";

const resolved = normalizeResolvedVars({
  vars: {
    freq_center_hz: 1e9,
    sweep_step_hz: 1e6,
    n_steps: 30,
    n_traces: 50,
    settle_s: 0.05,
    sweep_start_hz: 985_500_000,
    values: { gen: { values: [0, 200, 400] } },
  },
  base_vars: { n_traces: 20 },
  formulas: { sweep_start_hz: "${freq_center_hz - 0.5 * sweep_step_hz * (n_steps - 1)}" },
  derived: ["sweep_start_hz"],
  overridden: ["n_traces"],
  errors: {},
  expressions: {
    "${freq_hz}": { ok: false, runtime: true, names: ["freq_hz"] },
    "${settle_s * 2}": { ok: true, value: 0.1 },
    "${bad / 0}": { ok: false, error: "division by zero in ${bad / 0}" },
  },
});

const ctx = (patch: Partial<ValueContext> = {}): ValueContext => ({
  resolved,
  liveEnv: null,
  nextRunOverrides: null,
  ...patch,
});

describe("formatNumber", () => {
  it("uses engineering notation with about 5 significant figures", () => {
    expect(formatNumber(985_500_000)).toBe("985.5e6");
    expect(formatNumber(1_014_500_000)).toBe("1.0145e9");
    expect(formatNumber(1e9)).toBe("1e9");
    expect(formatNumber(30)).toBe("30");
    expect(formatNumber(0.05)).toBe("0.05");
    expect(formatNumber(1.23456789)).toBe("1.2346");
    expect(formatNumber(2.5e-6)).toBe("2.5e-6");
    expect(formatNumber(-3e7)).toBe("-30e6");
    expect(formatNumber(999_999_999.7)).toBe("1e9");
    // toPrecision's own exponent form starts at 1e5.
    expect(formatNumber(12_345.67)).toBe("12346");
    expect(formatNumber(123_456.7)).toBe("123.46e3");
    expect(formatNumber(999_999.7)).toBe("1e6");
  });
});

describe("formatShort", () => {
  it("summarizes lists, generators and strings", () => {
    expect(formatShort([1, 2, 3])).toBe("list[3]");
    expect(formatShort({ gen: { values: [0, 200, 400] } })).toBe("values ×3");
    expect(formatShort({ gen: { linspace: { start: 0, stop: 1, num: 11 } } })).toBe("linspace ×11");
    expect(formatShort("freq1")).toBe('"freq1"');
    expect(formatShort(true)).toBe("true");
  });
});

describe("templates", () => {
  it("splits text and extracts distinct expressions", () => {
    expect(splitTemplates("sleep ${a} then ${b + 1}")).toEqual([
      { kind: "text", text: "sleep " },
      { kind: "template", text: "${a}", inner: "a" },
      { kind: "text", text: " then " },
      { kind: "template", text: "${b + 1}", inner: "b + 1" },
    ]);
    expect(extractTemplateExpressions("${a} ${a} ${b}")).toEqual(["${a}", "${b}"]);
  });

  it("substitutes var values into a formula", () => {
    expect(substituteFormula("${freq_center_hz - 0.5 * sweep_step_hz * (n_steps - 1)}", resolved!.vars)).toBe(
      "1e9 - 0.5 * 1e6 * (30 - 1)"
    );
  });
});

describe("describeTemplate", () => {
  it("shows a plain var", () => {
    const chip = describeTemplate("${settle_s}", "settle_s", ctx());
    expect(chip).toMatchObject({ kind: "vars", name: "settle_s", value: "0.05", formula: false });
  });

  it("marks an override with the value it replaces and the next run's value", () => {
    const chip = describeTemplate("${vars.n_traces}", "vars.n_traces", ctx({ nextRunOverrides: { n_traces: 70 } }));
    expect(chip).toMatchObject({ kind: "override", name: "n_traces", value: "50", overrideDot: true });
    expect(chip?.detail).toContain("Without overrides: 20");
    expect(chip?.detail).toContain("Next run: 70");
  });

  it("shows a derived var with its formula", () => {
    const chip = describeTemplate("${sweep_start_hz}", "sweep_start_hz", ctx());
    expect(chip).toMatchObject({ kind: "derived", value: "985.5e6", formula: true, overrideDot: false });
    expect(chip?.detail).toEqual([
      "sweep_start_hz = 985,500,000",
      "freq_center_hz - 0.5 * sweep_step_hz * (n_steps - 1)",
      "= 1e9 - 0.5 * 1e6 * (30 - 1)",
    ]);
  });

  it("evaluates inline expressions, runtime names and errors", () => {
    expect(describeTemplate("${settle_s * 2}", "settle_s * 2", ctx())).toMatchObject({
      kind: "derived",
      name: "settle_s * 2",
      value: "0.1",
    });
    expect(describeTemplate("${freq_hz}", "freq_hz", ctx())).toMatchObject({ kind: "runtime", value: "runtime" });
    expect(describeTemplate("${freq_hz}", "freq_hz", ctx({ liveEnv: { freq_hz: 1.0035e9 } }))).toMatchObject({
      kind: "live",
      value: "1.0035e9",
    });
    expect(describeTemplate("${bad / 0}", "bad / 0", ctx())).toMatchObject({ kind: "error" });
    expect(describeTemplate("${unknown}", "unknown", ctx())).toBeNull();
    expect(describeTemplate("${settle_s}", "settle_s", ctx({ resolved: null }))).toBeNull();
  });
});

describe("vars that read runtime names", () => {
  const withLabel = normalizeResolvedVars({
    vars: { label: "run_${i}" },
    derived: ["label"],
    runtime_vars: { label: ["i"] },
    expressions: {},
  });

  it("shows them as runtime, with where the name comes from", () => {
    const chip = describeTemplate(
      "${label}",
      "label",
      ctx({
        resolved: withLabel,
        runtimeOrigins: { i: { label: "loop", description: "loop variable of for at L3", line: 3, over: null } },
      })
    );
    expect(chip).toMatchObject({ kind: "runtime", value: "loop", formula: true });
    expect(chip?.detail).toEqual([
      'label = "run_${i}"',
      "i: loop variable of for at L3",
      "Value known once the step runs.",
    ]);
    expect(describeVarDefinition("label", ctx({ resolved: withLabel }))?.kind).toBe("runtime");
  });
});

describe("stale values", () => {
  it("dims chips and says why", () => {
    const chip = describeTemplate("${settle_s}", "settle_s", ctx({ resolved: { ...resolved!, stale: true } }));
    expect(chip?.stale).toBe(true);
    expect(chip?.detail[chip.detail.length - 1]).toMatch(/out of date/);
  });
});

describe("describeVarDefinition", () => {
  it("only shows derived, overridden or changed vars", () => {
    expect(describeVarDefinition("settle_s", ctx())).toBeNull();
    expect(describeVarDefinition("sweep_start_hz", ctx())?.kind).toBe("derived");
    expect(describeVarDefinition("n_traces", ctx())?.kind).toBe("override");
  });
});

describe("overridesForDisplay", () => {
  it("uses sidebar overrides when idle", () => {
    expect(overridesForDisplay(false, { a: 1 }, { a: 2 })).toEqual({ overrides: { a: 2 }, nextRun: null });
  });

  it("uses the running overrides and lists differing sidebar ones for the next run", () => {
    expect(overridesForDisplay(true, { a: 1 }, { a: 1, b: 3 })).toEqual({
      overrides: { a: 1 },
      nextRun: { b: 3 },
    });
    expect(overridesForDisplay(true, { a: 1 }, { a: 1 })).toEqual({ overrides: { a: 1 }, nextRun: null });
  });
});

describe("scalarEnv", () => {
  it("keeps scalars only", () => {
    expect(scalarEnv({ freq_hz: 1, ok: true, name: "x", resp: { result: 1 }, big: "x".repeat(200) })).toEqual({
      freq_hz: 1,
      ok: true,
      name: "x",
    });
    expect(scalarEnv(null)).toBeNull();
  });
});

describe("varDefinitionLines", () => {
  it("finds the top-level vars entries", () => {
    const doc = Text.of([
      "version: 1",
      "vars:",
      "  a: 1",
      "  # note",
      "  b: ${a * 2}",
      "  gen_var:",
      "    gen: {values: [1]}",
      "steps:",
      "  - sleep: ${a}",
    ]);
    expect(Array.from(varDefinitionLines(doc).entries())).toEqual([
      [3, "a"],
      [5, "b"],
      [6, "gen_var"],
    ]);
  });
});

describe("runtime origins", () => {
  const yamlText = [
    "steps:",
    "  - for:",
    "      bind:",
    "        value: freq_hz",
    "        index: freq_step_idx",
    "      in:",
    "        gen:",
    "          linspace:",
    "            start: ${sweep_start_hz}",
    "            stop: ${settle_s}",
    "            num: ${n_steps}",
    "      do:",
    "        - assign: {shots: 1}",
    "        - call:",
    "            device: scope",
    "            action: read",
    "          save_as: resp",
  ].join("\n");
  const origins = buildRuntimeOrigins(buildSequencerStepOutline(yamlText));

  it("finds loop variables, assign targets and save_as names", () => {
    expect(origins.freq_hz).toMatchObject({ label: "loop", description: "loop variable of for at L2", line: 2 });
    expect(origins.freq_hz.over).toBe("linspace(start: ${sweep_start_hz}, stop: ${settle_s}, num: ${n_steps})");
    expect(origins.freq_step_idx).toMatchObject({ label: "index", over: null });
    expect(origins.shots).toMatchObject({ label: "assign", description: "set by assign at L13" });
    expect(origins.resp).toMatchObject({ label: "save_as" });
  });

  it("labels a loop variable and says what it runs over, with values", () => {
    const chip = describeTemplate("${freq_hz}", "freq_hz", ctx({ runtimeOrigins: origins }));
    expect(chip).toMatchObject({ kind: "runtime", value: "loop" });
    expect(chip?.detail).toEqual([
      "freq_hz: loop variable of for at L2",
      "  over linspace(start: 985.5e6, stop: 0.05, num: 30)",
      "Value known once the step runs.",
    ]);
  });
});
