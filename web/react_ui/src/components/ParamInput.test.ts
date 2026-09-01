import { describe, expect, it } from "vitest";

import { coerceParamValue, hasIntegerAnnotation } from "./ParamInput";

describe("ParamInput annotation handling", () => {
  it("treats integer Literal annotations as integer parameters", () => {
    expect(hasIntegerAnnotation("typing.Literal[0, 1]")).toBe(true);
    expect(hasIntegerAnnotation("Literal[-1, 0, 1]")).toBe(true);
    expect(hasIntegerAnnotation("typing.Literal['ch1', 'ch2']")).toBe(false);
  });

  it("coerces SynthHD Literal channel input to an integer", () => {
    const channel = {
      name: "channel",
      required: true,
      default: null,
      annotation: "typing.Literal[0, 1]",
    };

    expect(coerceParamValue("0", channel)).toBe(0);
    expect(coerceParamValue("1", channel)).toBe(1);
    expect(coerceParamValue("ch1", channel)).toBe("ch1");
  });
});
