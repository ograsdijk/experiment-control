import { describe, expect, it } from "vitest";
import {
  buildTelemetrySmoothingOverlays,
  smoothTelemetrySeriesEma,
  smoothTelemetrySeriesSma,
} from "./telemetry_smoothing";

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
