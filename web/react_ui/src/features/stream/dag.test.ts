import { describe, expect, it } from "vitest";

import { isResettableHistogramAggregateOp, normalizeDagNode } from "./dag";

describe("normalizeDagNode", () => {
  it("accepts UI draft camelCase node ids", () => {
    const node = normalizeDagNode({
      nodeId: "fluor_bg",
      op: "trace.subtract_background",
      inputs: { trace: "fluor_src" },
      params: { bg_start_idx: 200, bg_stop_idx: 1200 },
    });

    expect(node).toEqual({
      nodeId: "fluor_bg",
      op: "trace.subtract_background",
      inputs: { trace: "fluor_src" },
      params: { bg_start_idx: 200, bg_stop_idx: 1200 },
    });
  });
});

describe("isResettableHistogramAggregateOp", () => {
  it("includes regular and ratio histogram aggregators", () => {
    expect(isResettableHistogramAggregateOp("aggregate.bin_stats")).toBe(true);
    expect(isResettableHistogramAggregateOp("aggregate.bin_ratio_stats")).toBe(true);
  });

  it("excludes other DAG operators", () => {
    expect(isResettableHistogramAggregateOp("aggregate.bin2d_stats")).toBe(false);
    expect(isResettableHistogramAggregateOp("trace.integrate")).toBe(false);
  });
});
