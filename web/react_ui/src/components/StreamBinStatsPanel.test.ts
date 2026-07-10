import { describe, expect, it } from "vitest";
import {
  buildBandData,
  buildFitOverlayData,
  type StreamBinStatsSeries,
} from "./StreamBinStatsPanel";

const SERIES: StreamBinStatsSeries = {
  xBins: [1, 2],
  mean: [10, 20],
  std: [2, 4],
  sem: [1, 2],
  count: [3, 3],
};

describe("stream bin stats x-axis transforms", () => {
  it("applies scale then offset to bin coordinates", () => {
    const [x, lower, upper, mean] = buildBandData(
      SERIES,
      "sem",
      1,
      -1,
      2
    );

    expect(x).toEqual([1, 3]);
    expect(lower).toEqual([9, 18]);
    expect(upper).toEqual([11, 22]);
    expect(mean).toEqual([10, 20]);
  });

  it("uses the same transform for fit overlay coordinates", () => {
    const overlays = buildFitOverlayData(
      [{ label: "fit", x: [1, 2], y: [5, 6] }],
      -1,
      2
    );

    expect(overlays).toEqual([{ label: "fit", x: [1, 3], y: [5, 6] }]);
  });

  it("falls back to the identity transform for invalid values", () => {
    const [x] = buildBandData(SERIES, "sem", 1, Number.NaN, 0);
    expect(x).toEqual([1, 2]);
  });
});
