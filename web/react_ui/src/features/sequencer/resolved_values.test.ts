import { describe, expect, it } from "vitest";
import {
  describeTemplate,
  describeVarDefinition,
  normalizeResolvedVars,
  overridesForDisplay,
  scalarEnv,
  type ValueContext,
} from "./resolved_values";
import { buildSequencerStepOutline } from "./outline";
import { buildRuntimeOrigins } from "./runtime_origins";

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

describe("names that are also Object.prototype keys", () => {
  it("are looked up as own keys only", () => {
    const withProto = normalizeResolvedVars({
      vars: { a: 1 },
      expressions: { "${toString}": { ok: false, runtime: true, names: ["toString"] } },
    });
    expect(describeTemplate("${toString}", "toString", ctx({ resolved: withProto }))).toMatchObject({
      kind: "runtime",
      name: "toString",
    });
    expect(describeVarDefinition("constructor", ctx({ resolved: withProto }))).toBeNull();
  });
});

describe("stale values", () => {
  it("dims chips and says why", () => {
    const chip = describeTemplate("${settle_s}", "settle_s", ctx({ resolved: { ...resolved!, staleReason: "the YAML doesn't parse right now" } }));
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


describe("runtime origins in chips", () => {
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

describe("unknown names", () => {
  const unknownCtx = (names: string[]) =>
    ctx({
      resolved: normalizeResolvedVars({
        vars: { f2: "${freq_hzz * 2}" },
        runtime_vars: { f2: ["freq_hzz"] },
        expressions: { "${freq_hzz}": { ok: false, runtime: true, names: ["freq_hzz"] } },
      }),
      runtimeOrigins: Object.fromEntries(
        names.map((name) => [name, { label: "loop", description: "loop variable of for at L2", line: 2, over: null }])
      ),
    });

  it("are flagged when no step sets them", () => {
    expect(describeTemplate("${freq_hzz}", "freq_hzz", unknownCtx([]))).toMatchObject({ kind: "unknown" });
    expect(describeTemplate("${f2}", "f2", unknownCtx([]))).toMatchObject({ kind: "unknown", formula: true });
    expect(describeTemplate("${freq_hzz}", "freq_hzz", unknownCtx([]))?.detail[0]).toBe(
      "freq_hzz: not a var, and no step in this sequence sets it (typo?)"
    );
  });

  it("are not flagged when a step sets them", () => {
    expect(describeTemplate("${freq_hzz}", "freq_hzz", unknownCtx(["freq_hzz"]))).toMatchObject({ kind: "runtime" });
  });

});
