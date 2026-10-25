import { describe, expect, it } from "vitest";

import {
  buildStreamWaterfallGrid,
  colorWaterfallRows,
  waterfallGridZRange,
} from "./StreamWaterfallPanel";
import type { StreamFrame } from "./StreamRawPanel";
import { writeHeatmapPixel, writeHeatmapRow } from "../utils/heatmapColormap";

function frame(seq: number, values: number[]): StreamFrame {
  return { seq, values, shape: [values.length] } as unknown as StreamFrame;
}

function frames(from: number, to: number, cols = 6): StreamFrame[] {
  const out: StreamFrame[] = [];
  for (let seq = from; seq <= to; seq += 1) {
    out.push(frame(seq, Array.from({ length: cols }, (_, c) => Math.sin(seq * 0.7 + c))));
  }
  return out;
}

describe("waterfall incremental colouring", () => {
  it("matches a full recolour and colours only the new row when the scale holds", () => {
    const first = buildStreamWaterfallGrid(frames(1, 5), 5, 0);
    const second = buildStreamWaterfallGrid(frames(2, 6), 5, 0);
    const initial = colorWaterfallRows(first, -1, 1, null);
    expect(initial.coloredRows).toBe(5);

    const incremental = colorWaterfallRows(second, -1, 1, initial.cache);
    const full = colorWaterfallRows(second, -1, 1, null);
    expect(incremental.coloredRows).toBe(1);
    expect([...incremental.cache.data]).toEqual([...full.cache.data]);
  });

  it("recolours every row when the range or width changes", () => {
    const grid = buildStreamWaterfallGrid(frames(1, 4), 4, 0);
    const cache = colorWaterfallRows(grid, -1, 1, null).cache;
    expect(colorWaterfallRows(grid, -2, 1, cache).coloredRows).toBe(4);
    const wider = buildStreamWaterfallGrid(frames(1, 4, 8), 4, 0);
    expect(colorWaterfallRows(wider, -1, 1, cache).coloredRows).toBe(4);
  });

  it("does not reuse rows when the same seqs carry other data (channel switch)", () => {
    const channelFrames = (offset: number) =>
      [1, 2, 3].map((seq) => frame(seq, Array.from({ length: 6 }, (_, c) => Math.sin(seq + c + offset))));
    const channel0 = buildStreamWaterfallGrid(channelFrames(0), 3, 0);
    const channel1 = buildStreamWaterfallGrid(channelFrames(100), 3, 0);
    // Manual scale: identical z range for both.
    const cache = colorWaterfallRows(channel0, -1, 1, null).cache;
    const switched = colorWaterfallRows(channel1, -1, 1, cache);
    expect(switched.coloredRows).toBe(3);
    expect([...switched.cache.data]).toEqual([...colorWaterfallRows(channel1, -1, 1, null).cache.data]);
  });

  it("derives the auto range from the grid it was given", () => {
    const grid = buildStreamWaterfallGrid([frame(1, [1, 5]), frame(2, [-2, 3])], 2, 0);
    expect(grid.rowSeqs).toEqual([1, 2]);
    expect(waterfallGridZRange(grid)).toEqual({ min: -2, max: 5 });
  });
});

describe("writeHeatmapRow", () => {
  it("matches writeHeatmapPixel for every value, including out of range and NaN", () => {
    const values = [-5, -1, -0.3, 0, 0.25, 0.5, 0.99, 1, 3, Number.NaN];
    const zMin = -1;
    const span = 2;
    const row = new Uint8ClampedArray(values.length * 4);
    writeHeatmapRow(row, 0, values, 0, values.length, zMin, span);
    const expected = new Uint8ClampedArray(values.length * 4);
    values.forEach((value, i) => writeHeatmapPixel(expected, i * 4, (value - zMin) / span));
    expect([...row]).toEqual([...expected]);
  });
});
