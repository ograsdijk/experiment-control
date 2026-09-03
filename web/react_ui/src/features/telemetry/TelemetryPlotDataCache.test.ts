import { describe, expect, it, vi } from "vitest";

import { RingBuffer } from "../../utils/ringBuffer";
import type { TraceKey } from "../../types";
import { TelemetryPlotDataCache } from "./TelemetryPlotDataCache";

const traces: TraceKey[] = [
  { deviceId: "a", signal: "x" },
  { deviceId: "b", signal: "y" },
];

describe("TelemetryPlotDataCache", () => {
  it("aligns tails and reuses arrays when buffers have not changed", () => {
    const a = new RingBuffer(10);
    const b = new RingBuffer(10);
    a.push(1, 10);
    a.push(2, 20);
    a.push(3, 30);
    b.push(2, 200);
    b.push(3, 300);
    const cache = new TelemetryPlotDataCache();
    const buffers = new Map([
      ["a:x", a],
      ["b:y", b],
    ]);
    const first = cache.update(traces, buffers);
    expect(first).toEqual([
      [2, 3],
      [20, 30],
      [200, 300],
    ]);
    const seriesRefs = [...first];
    const second = cache.update(traces, buffers);
    expect(second).toBe(first);
    expect(second[0]).toBe(seriesRefs[0]);
    expect(second[1]).toBe(seriesRefs[1]);

    const unchangedCopy = vi.spyOn(b, "copyTailValuesInto");
    a.push(4, 40);
    expect(cache.update(traces, buffers)).toEqual([
      [3, 4],
      [30, 40],
      [200, 300],
    ]);
    expect(unchangedCopy).not.toHaveBeenCalled();
  });

  it("preserves the existing empty-first-trace alignment behavior", () => {
    const b = new RingBuffer(10);
    b.push(1, 100);
    const cache = new TelemetryPlotDataCache();
    expect(cache.update(traces, new Map([["b:y", b]]))).toEqual([
      [],
      [],
      [100],
    ]);
  });
});
