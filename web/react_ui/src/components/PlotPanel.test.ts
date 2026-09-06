import { describe, expect, it } from "vitest";

import type { TraceKey } from "../types";
import { RingBuffer } from "../utils/ringBuffer";
import { buildTelemetryData, computeTelemetryAutoYRange } from "./PlotPanel";

const traces: TraceKey[] = [
  { deviceId: "a", signal: "x" },
  { deviceId: "b", signal: "y" },
];

function referenceRange(
  buffers: Map<string, RingBuffer>,
  timeWindowS: number
): { min: number; max: number } | null {
  const data = buildTelemetryData(traces, buffers);
  const x = data[0];
  if (!x?.length) return null;
  let start = 0;
  const latest = x[x.length - 1];
  if (Number.isFinite(timeWindowS) && timeWindowS > 0 && Number.isFinite(latest)) {
    const minX = latest - timeWindowS;
    for (let index = x.length - 1; index >= 0; index -= 1) {
      if (x[index] < minX) {
        start = Math.min(x.length - 1, index + 1);
        break;
      }
    }
  }
  const values = data.slice(1).flatMap((series) =>
    series.slice(start).filter(Number.isFinite)
  );
  if (values.length === 0) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (min === max) {
    const pad = Math.abs(min) > 0 ? Math.abs(min) * 0.05 : 1;
    return { min: min - pad, max: max + pad };
  }
  return { min, max };
}

describe("telemetry plot data", () => {
  it("keeps auto range equivalent without constructing aligned arrays", () => {
    const a = new RingBuffer(10);
    const b = new RingBuffer(10);
    for (let index = 0; index < 15; index += 1) {
      a.push(index, index === 12 ? Number.NaN : index * 2);
      if (index >= 3) b.push(index + 0.1, -index);
    }
    const buffers = new Map([
      ["a:x", a],
      ["b:y", b],
    ]);
    for (const window of [0, 2, 6, 100]) {
      expect(computeTelemetryAutoYRange(traces, buffers, window)).toEqual(
        referenceRange(buffers, window)
      );
    }
  });

  it("returns no auto range when the first trace has no samples", () => {
    const b = new RingBuffer(10);
    b.push(1, 5);
    expect(computeTelemetryAutoYRange(traces, new Map([["b:y", b]]), 10)).toBeNull();
  });
});
