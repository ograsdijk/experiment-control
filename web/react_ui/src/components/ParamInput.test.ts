import { describe, expect, it } from "vitest";

import { coerceParamValue, hasIntegerAnnotation, integerLiteralValues } from "./ParamInput";

describe("ParamInput annotation handling", () => {
  it("treats integer Literal annotations as integer parameters", () => {
    expect(hasIntegerAnnotation("typing.Literal[1, 2]")).toBe(true);
    expect(hasIntegerAnnotation("Literal[-1, 0, 1]")).toBe(true);
    expect(hasIntegerAnnotation("typing.Literal['ch1', 'ch2']")).toBe(false);
    expect(integerLiteralValues("typing.Literal[1, 2]")).toEqual([1, 2]);
  });

  it("only coerces exact SynthHD physical-channel Literal members", () => {
    const channel = {
      name: "channel",
      required: true,
      default: null,
      annotation: "typing.Literal[1, 2]",
    };

    expect(coerceParamValue("1", channel)).toBe(1);
    expect(coerceParamValue("2", channel)).toBe(2);
    expect(coerceParamValue("0", channel)).toBe("0");
    expect(coerceParamValue("0.9", channel)).toBe("0.9");
    expect(coerceParamValue("1.9", channel)).toBe("1.9");
    expect(coerceParamValue("3", channel)).toBe("3");
    expect(coerceParamValue("ch1", channel)).toBe("ch1");
  });
});
