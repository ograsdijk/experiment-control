import { describe, expect, it } from "vitest";
import {
  buildBandData,
  buildBinStatsPlotData,
  buildFitOverlayData,
  computeStreamBinStatsAutoYRange,
  type StreamBinStatsSeries,
} from "./StreamBinStatsPanel";
import { normalizeHistAggValue } from "../features/stream/messages";

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

  it("does not turn an unavailable SEM into a zero-width band", () => {
    const snapshot = normalizeHistAggValue({
      x_bins: [1, 2],
      mean: [10, 20],
      std: [0, 2],
      sem: [null, 1],
      count: [1, 2],
    });
    expect(snapshot).not.toBeNull();
    const [x, lower, upper, mean] = buildBandData(
      snapshot?.series ?? null,
      "sem",
      1
    );
    expect(x).toEqual([2]);
    expect(lower).toEqual([19]);
    expect(upper).toEqual([21]);
    expect(mean).toEqual([20]);
  });
});

describe("stream bin stats histogram overlays", () => {
  const open: StreamBinStatsSeries = {
    xBins: [1, 2, 3],
    mean: [10, 20, 30],
    std: [0, 0, 0],
    sem: [1, 1, 1],
    count: [4, 4, 1],
  };
  const closed: StreamBinStatsSeries = {
    xBins: [1, 2],
    mean: [5, 6],
    std: [0, 0],
    sem: [0.5, 0.5],
    count: [4, 3],
  };

  it("keeps the primary layout when no histogram overlays are selected", () => {
    const built = buildBinStatsPlotData(open, [], [], "sem", 1);
    expect(built.columns).toEqual([
      [1, 2, 3],
      [9, 19, 29],
      [11, 21, 31],
      [10, 20, 30],
    ]);
  });

  it("merges histograms with different bins onto the union of x values", () => {
    const built = buildBinStatsPlotData(
      closed,
      [],
      [{ label: "open", series: open }],
      "sem",
      1
    );
    expect(built.histOverlayLabels).toEqual(["open"]);
    expect(built.columns[0]).toEqual([1, 2, 3]);
    // Primary (closed) has no bin at x = 3.
    expect(built.columns[3]).toEqual([5, 6, null]);
    expect(built.columns[1]).toEqual([4.5, 5.5, null]);
    // Overlay lower, upper, mean.
    expect(built.columns[4]).toEqual([9, 19, 29]);
    expect(built.columns[5]).toEqual([11, 21, 31]);
    expect(built.columns[6]).toEqual([10, 20, 30]);
  });

  it("keeps an empty slot for an overlay that has no data yet", () => {
    const built = buildBinStatsPlotData(
      open,
      [{ label: "trace", values: [7, 8, 9] }],
      [{ label: "closed", series: null }],
      "sem",
      1
    );
    expect(built.columns).toHaveLength(4 + 1 + 3);
    expect(built.columns[4]).toEqual([7, 8, 9]);
    expect(built.columns.slice(5)).toEqual([
      [null, null, null],
      [null, null, null],
      [null, null, null],
    ]);
  });

  it("applies the x transform to overlay bins too", () => {
    const built = buildBinStatsPlotData(
      closed,
      [],
      [{ label: "open", series: open }],
      "sem",
      1,
      10,
      2
    );
    expect(built.columns[0]).toEqual([12, 14, 16]);
  });

  it("includes histogram overlay bands in the auto y range", () => {
    const range = computeStreamBinStatsAutoYRange(closed, "sem", 1, [], [], [
      { label: "open", series: open },
    ]);
    expect(range).toEqual({ min: 4.5, max: 31 });
  });

  it("auto-ranges on an overlay alone before the primary has data", () => {
    const range = computeStreamBinStatsAutoYRange(null, "sem", 1, [], [], [
      { label: "open", series: open },
    ]);
    expect(range).toEqual({ min: 9, max: 31 });
  });
});
