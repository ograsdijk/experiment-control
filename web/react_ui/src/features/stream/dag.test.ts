import { describe, expect, it } from "vitest";

import { normalizeDagNode } from "./dag";

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
