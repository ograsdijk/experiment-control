import { describe, expect, it } from "vitest";

import {
  STREAM_DAG_INPUT_KINDS,
  STREAM_DAG_OPS,
  STREAM_DAG_OP_OPTIONS,
  defaultParamsForOp,
} from "./dag";

describe("trace.block_average in the DAG editor", () => {
  it("is offered in the op picker", () => {
    expect(STREAM_DAG_OP_OPTIONS.map((o) => o.value)).toContain(
      "trace.block_average"
    );
  });

  it("takes a single trace input and emits a trace", () => {
    const def = STREAM_DAG_OPS["trace.block_average"];
    expect(def.inputs).toEqual(["trace"]);
    expect(def.outputKind).toBe("trace");
    expect(STREAM_DAG_INPUT_KINDS["trace.block_average"]).toEqual({
      trace: "trace",
    });
  });

  it("exposes block_traces as an integer param", () => {
    const params = STREAM_DAG_OPS["trace.block_average"].params;
    expect(params.map((p) => p.name)).toEqual(["block_traces"]);
    expect(params[0].kind).toBe("integer");
  });

  it("defaults to a block that actually averages", () => {
    // A default of 1 would make the op a silent pass-through, which is the
    // opposite of why someone adds it.
    expect(defaultParamsForOp("trace.block_average")).toEqual({
      block_traces: 8,
    });
  });

  it("stays distinct from trace.rolling_mean", () => {
    expect(STREAM_DAG_OPS["trace.rolling_mean"].params[0].name).toBe(
      "window_traces"
    );
    expect(STREAM_DAG_OPS["trace.block_average"].params[0].name).toBe(
      "block_traces"
    );
  });
});
