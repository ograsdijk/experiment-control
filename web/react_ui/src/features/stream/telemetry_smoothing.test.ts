import { describe, expect, it } from "vitest";
import {
  buildTelemetrySmoothingOverlays,
  smoothTelemetrySeriesEma,
  smoothTelemetrySeriesSma,
  TelemetrySmoothingCache,
} from "./telemetry_smoothing";

function referenceSma(
  time: readonly number[],
  values: readonly number[],
  windowS: number
): number[] {
  const out = new Array<number>(Math.min(time.length, values.length)).fill(Number.NaN);
  const width = Math.max(1e-6, Number(windowS));
  for (let i = 0; i < out.length; i += 1) {
    const ti = time[i];
    if (!Number.isFinite(ti)) {
      continue;
    }
    let sum = 0;
    let count = 0;
    for (let j = i; j >= 0; j -= 1) {
      const tj = time[j];
      if (!Number.isFinite(tj)) {
        continue;
      }
      if (ti - tj > width) {
        break;
      }
      if (Number.isFinite(values[j])) {
        sum += values[j];
        count += 1;
      }
    }
    if (count > 0) {
      out[i] = sum / count;
    }
  }
  return out;
}

function expectSeriesClose(actual: readonly number[], expected: readonly number[]) {
  expect(actual).toHaveLength(expected.length);
  for (let index = 0; index < expected.length; index += 1) {
    if (Number.isNaN(expected[index])) {
      expect(actual[index]).toBeNaN();
    } else {
      expect(actual[index]).toBeCloseTo(expected[index], 12);
    }
  }
}

describe("telemetry smoothing", () => {
  it("computes time-windowed SMA", () => {
    const time = [0, 1, 2, 3];
    const values = [0, 2, 4, 6];
    const out = smoothTelemetrySeriesSma(time, values, 2);
    expect(out[0]).toBeCloseTo(0);
    expect(out[1]).toBeCloseTo(1);
    expect(out[2]).toBeCloseTo(2);
    expect(out[3]).toBeCloseTo(4);
  });

  it("computes EMA and resets on large timestamp gaps", () => {
    const time = [0, 1, 20];
    const values = [1, 3, 100];
    const out = smoothTelemetrySeriesEma(time, values, 2);
    expect(out[0]).toBeCloseTo(1);
    expect(out[1]).toBeGreaterThan(1);
    expect(out[1]).toBeLessThan(3);
    expect(out[2]).toBeCloseTo(100);
  });

  it.each([
    { name: "one sample", time: [4], values: [7], window: 2 },
    { name: "constant", time: [0, 1, 2, 3], values: [5, 5, 5, 5], window: 1 },
    {
      name: "irregular gaps and boundary equality",
      time: [0, 0.25, 2, 4, 4.5],
      values: [1, 2, 3, 4, 5],
      window: 2,
    },
    {
      name: "non-finite values and timestamps",
      time: [0, Number.NaN, 1, 2, Number.POSITIVE_INFINITY, 3],
      values: [1, 50, Number.NaN, 3, 80, Number.NEGATIVE_INFINITY],
      window: 2,
    },
    {
      name: "duplicate timestamps",
      time: [0, 1, 1, 2],
      values: [1, 2, 4, 8],
      window: 1,
    },
    {
      name: "backwards timestamps",
      time: [0, 3, 1, 2, -1, 4],
      values: [1, 2, 3, 4, 5, 6],
      window: 1.5,
    },
  ])("matches the legacy SMA for $name", ({ time, values, window }) => {
    expectSeriesClose(
      smoothTelemetrySeriesSma(time, values, window),
      referenceSma(time, values, window)
    );
  });

  it("writes SMA and EMA into caller-owned reusable arrays", () => {
    const smaTarget = [99, 99, 99, 99];
    const emaTarget = [99];
    expect(smoothTelemetrySeriesSma([0, 1], [2, 4], 2, smaTarget)).toBe(smaTarget);
    expect(smaTarget).toEqual([2, 3]);
    expect(smoothTelemetrySeriesEma([0, 1], [2, 4], 2, emaTarget)).toBe(emaTarget);
    expect(emaTarget).toHaveLength(2);
  });

  it("uses linear work for monotonic SMA input", () => {
    const sizes = [1_000, 10_000];
    const operations = sizes.map((size) => {
      const work = { operations: 0 };
      const time = Array.from({ length: size }, (_, index) => index * 0.1);
      const values = Array.from({ length: size }, (_, index) => index % 17);
      smoothTelemetrySeriesSma(time, values, 2, [], work);
      return work.operations;
    });
    expect(operations[0]).toBeLessThanOrEqual(sizes[0] * 4);
    expect(operations[1]).toBeLessThanOrEqual(sizes[1] * 4);
    expect(operations[1] / operations[0]).toBeLessThan(10.1);
  });

  it.each(["sma", "ema"] as const)(
    "processes only appended samples for incremental %s updates",
    (mode) => {
      const cache = new TelemetrySmoothingCache();
      const time = [0, 1, 2];
      const values = [1, 3, 5];
      const reference = mode === "sma" ? smoothTelemetrySeriesSma : smoothTelemetrySeriesEma;
      expectSeriesClose(
        cache.update(time, values, mode, 2, 3, 3, 0, 0),
        reference(time, values, 2)
      );
      time.push(3, 4);
      values.push(7, 9);
      expectSeriesClose(
        cache.update(time, values, mode, 2, 5, 5, 0, 0),
        reference(time, values, 2)
      );
      expect(cache.stats.rebuilds).toBe(1);
      expect(cache.stats.processedSamples).toBe(5);
    }
  );

  it("rebuilds cache after eviction, clear/resize, window changes, and backwards time", () => {
    const cache = new TelemetrySmoothingCache();
    const update = (
      time: number[],
      values: number[],
      sequence: number,
      structure: number,
      window = 2
    ) => cache.update(time, values, "sma", window, sequence, sequence, structure, structure);
    update([0, 1, 2], [1, 2, 3], 3, 0);
    expectSeriesClose(update([1, 2, 3], [2, 3, 4], 4, 0), [2, 2.5, 3]);
    expectSeriesClose(update([4], [8], 5, 1), [8]);
    expectSeriesClose(update([4, 5], [8, 10], 6, 1, 1), [8, 9]);
    expectSeriesClose(
      update([4, 5, 3], [8, 10, 12], 7, 1, 1),
      referenceSma([4, 5, 3], [8, 10, 12], 1)
    );
    expect(cache.stats.rebuilds).toBe(5);
  });

  it("builds overlays only for numeric traces", () => {
    const time = [0, 1, 2];
    const overlays = buildTelemetrySmoothingOverlays(
      time,
      [
        { deviceId: "d1", signal: "x", valueKind: "number" },
        { deviceId: "d1", signal: "flag", valueKind: "boolean" },
      ],
      [
        [1, 2, 3],
        [0, 1, 1],
      ],
      "sma",
      2
    );
    expect(overlays).toHaveLength(1);
    expect(overlays[0].traceIndex).toBe(0);
    expect(overlays[0].values).toHaveLength(3);
  });
});
