import { describe, expect, it } from "vitest";

import { heatmapColorExact, writeHeatmapPixel } from "./heatmapColormap";

function lutColor(t: number): number[] {
  const data = new Uint8ClampedArray(4);
  writeHeatmapPixel(data, 0, t);
  return [...data];
}

describe("heatmap colormap lookup table", () => {
  it("matches the exact ramp at both ends", () => {
    for (const t of [0, 1]) {
      expect(lutColor(t)).toEqual([...heatmapColorExact(t), 255]);
    }
  });

  it("stays within two levels of the exact ramp everywhere", () => {
    for (let i = 0; i <= 10000; i += 1) {
      const t = i / 10000;
      const exact = heatmapColorExact(t);
      const fast = lutColor(t);
      for (let channel = 0; channel < 3; channel += 1) {
        expect(Math.abs(fast[channel] - exact[channel])).toBeLessThanOrEqual(2);
      }
    }
  });

  it("clamps out-of-range and non-finite values like the original ramp", () => {
    expect(lutColor(-3)).toEqual([...heatmapColorExact(0), 255]);
    expect(lutColor(7)).toEqual([...heatmapColorExact(1), 255]);
    expect(lutColor(Number.NaN)).toEqual([...heatmapColorExact(0), 255]);
  });

  it("writes at the requested pixel offset only", () => {
    const data = new Uint8ClampedArray(12);
    writeHeatmapPixel(data, 4, 1);
    expect([...data.slice(0, 4)]).toEqual([0, 0, 0, 0]);
    expect([...data.slice(4, 8)]).toEqual([...heatmapColorExact(1), 255]);
    expect([...data.slice(8)]).toEqual([0, 0, 0, 0]);
  });
});
