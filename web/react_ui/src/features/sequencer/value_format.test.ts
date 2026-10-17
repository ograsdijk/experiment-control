import { describe, expect, it } from "vitest";
import { formatNumber, formatShort, substituteFormula } from "./value_format";

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
    // Rounds up to 1e5 inside toPrecision.
    expect(formatNumber(99_999.7)).toBe("100e3");
  });
});

describe("formatShort", () => {
  it("summarizes lists, generators and strings", () => {
    expect(formatShort([1, 2, 3])).toBe("list[3]");
    expect(formatShort({ gen: { values: [0, 200, 400] } })).toBe("values ×3");
    expect(formatShort({ gen: { linspace: { start: 0, stop: 1, num: 11 } } })).toBe("linspace ×11");
    expect(formatShort("freq1")).toBe("freq1");
    // Quoted where bare text would read as something else.
    expect(formatShort("")).toBe('""');
    expect(formatShort("5")).toBe('"5"');
    expect(formatShort("true")).toBe('"true"');
    expect(formatShort("null")).toBe('"null"');
    expect(formatShort(" ")).toBe('" "');
    expect(formatShort(" abc")).toBe('" abc"');
    expect(formatShort(true)).toBe("true");
  });
});

describe("substituteFormula", () => {
  it("substitutes var values into a formula", () => {
    expect(substituteFormula("${freq_center_hz - 0.5 * sweep_step_hz * (n_steps - 1)}", { freq_center_hz: 1e9, sweep_step_hz: 1e6, n_steps: 30 })).toBe(
      "1e9 - 0.5 * 1e6 * (30 - 1)"
    );
  });
});
