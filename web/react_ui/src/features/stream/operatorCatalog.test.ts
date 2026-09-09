import { describe, expect, it } from "vitest";

import {
  FALLBACK_FIT_MODEL_OPTIONS,
  STREAM_DAG_OPS,
  collectNodeParamValues,
  mergeOpParamChoices,
  parseOperatorCatalogChoices,
} from "./dag";
import type { StreamDagOpDef, StreamDagOpId } from "./types";

const CATALOG = [
  {
    op: "fit.curve_1d",
    params: [
      {
        name: "model",
        kind: "string",
        choices: [
          { value: "gaussian", label: "gaussian" },
          { value: "lorentzian", label: "lorentzian" },
          { value: "reciprocal_normal", label: "reciprocal normal (beam arrival)" },
        ],
      },
      { name: "every_n", kind: "integer" },
    ],
  },
];

function modelOptions(ops: Record<StreamDagOpId, StreamDagOpDef>) {
  return ops["fit.curve_1d"].params.find((f) => f.name === "model")?.options ?? [];
}

describe("parseOperatorCatalogChoices", () => {
  it("extracts choices keyed by op and param", () => {
    const choices = parseOperatorCatalogChoices(CATALOG);
    expect(choices["fit.curve_1d"].model.map((c) => c.value)).toEqual([
      "gaussian",
      "lorentzian",
      "reciprocal_normal",
    ]);
    expect(choices["fit.curve_1d"].every_n).toBeUndefined();
  });

  it("survives malformed payloads instead of throwing", () => {
    for (const bad of [null, undefined, 42, "nope", [null], [{}], [{ op: 1 }]]) {
      expect(() => parseOperatorCatalogChoices(bad)).not.toThrow();
      expect(parseOperatorCatalogChoices(bad)).toEqual({});
    }
  });

  it("drops choice entries that are not usable", () => {
    const choices = parseOperatorCatalogChoices([
      {
        op: "fit.curve_1d",
        params: [{ name: "model", choices: [{ value: "" }, { label: "x" }, 7, null] }],
      },
    ]);
    expect(choices).toEqual({});
  });

  it("falls back to the value when a label is missing", () => {
    const choices = parseOperatorCatalogChoices([
      { op: "fit.curve_1d", params: [{ name: "model", choices: [{ value: "voigt" }] }] },
    ]);
    expect(choices["fit.curve_1d"].model).toEqual([
      { value: "voigt", label: "voigt" },
    ]);
  });
});

describe("mergeOpParamChoices", () => {
  it("overlays fetched choices onto the static table", () => {
    const merged = mergeOpParamChoices(
      STREAM_DAG_OPS,
      parseOperatorCatalogChoices(CATALOG)
    );
    expect(modelOptions(merged).map((o) => o.value)).toContain("reciprocal_normal");
  });

  it("returns the static table unchanged when nothing was fetched", () => {
    expect(mergeOpParamChoices(STREAM_DAG_OPS, {})).toBe(STREAM_DAG_OPS);
    expect(modelOptions(STREAM_DAG_OPS)).toEqual(FALLBACK_FIT_MODEL_OPTIONS);
  });

  it("never adds, removes, or reorders param fields", () => {
    const merged = mergeOpParamChoices(
      STREAM_DAG_OPS,
      parseOperatorCatalogChoices(CATALOG)
    );
    for (const op of Object.keys(STREAM_DAG_OPS) as StreamDagOpId[]) {
      expect(merged[op].params.map((f) => f.name)).toEqual(
        STREAM_DAG_OPS[op].params.map((f) => f.name)
      );
      expect(merged[op].inputs).toEqual(STREAM_DAG_OPS[op].inputs);
      expect(merged[op].outputKind).toEqual(STREAM_DAG_OPS[op].outputKind);
    }
  });

  it("ignores ops the static table does not declare", () => {
    const merged = mergeOpParamChoices(STREAM_DAG_OPS, {
      "fit.does_not_exist": { model: [{ value: "x", label: "x" }] },
    });
    expect(Object.keys(merged)).toEqual(Object.keys(STREAM_DAG_OPS));
  });

  it("does not turn a free-text param into a dropdown", () => {
    const merged = mergeOpParamChoices(STREAM_DAG_OPS, {
      "fit.curve_1d": { every_n: [{ value: "1", label: "1" }] },
    });
    const everyN = merged["fit.curve_1d"].params.find((f) => f.name === "every_n");
    expect(everyN?.options).toBeUndefined();
  });

  it("preserves a saved value the catalog does not list", () => {
    // Otherwise the Select renders it as the first option, silently
    // misreporting which model is actually running.
    const merged = mergeOpParamChoices(
      STREAM_DAG_OPS,
      {},
      { "fit.curve_1d": ["reciprocal_normal"] }
    );
    const values = modelOptions(merged).map((o) => o.value);
    expect(values).toContain("reciprocal_normal");
    expect(modelOptions(merged).find((o) => o.value === "reciprocal_normal")?.label).toBe(
      "reciprocal_normal (unknown)"
    );
  });

  it("does not duplicate a current value the catalog already lists", () => {
    const merged = mergeOpParamChoices(
      STREAM_DAG_OPS,
      parseOperatorCatalogChoices(CATALOG),
      { "fit.curve_1d": ["reciprocal_normal"] }
    );
    const matches = modelOptions(merged).filter(
      (o) => o.value === "reciprocal_normal"
    );
    expect(matches).toHaveLength(1);
    expect(matches[0].label).toBe("reciprocal normal (beam arrival)");
  });
});

describe("collectNodeParamValues", () => {
  it("collects only values of params rendered as dropdowns", () => {
    const values = collectNodeParamValues([
      { op: "fit.curve_1d", params: { model: "reciprocal_normal", every_n: 4 } },
      { op: "fit.curve_1d", params: { model: "gaussian" } },
      { op: "unknown.op", params: { model: "whatever" } },
    ]);
    expect(values["fit.curve_1d"]).toEqual(["reciprocal_normal", "gaussian"]);
    expect(values["unknown.op"]).toBeUndefined();
  });

  it("ignores blank and non-string values", () => {
    const values = collectNodeParamValues([
      { op: "fit.curve_1d", params: { model: "  " } },
      { op: "fit.curve_1d", params: { model: 3 } },
    ]);
    expect(values).toEqual({});
  });
});

describe("fit op params", () => {
  it("exposes t0_s on both fit ops", () => {
    for (const op of ["fit.curve_1d", "fit.from_hist_agg"] as StreamDagOpId[]) {
      expect(STREAM_DAG_OPS[op].params.map((f) => f.name)).toContain("t0_s");
    }
  });
});
