import { describe, expect, it } from "vitest";

import { RingBuffer } from "../../utils/ringBuffer";
import { pushTelemetrySampleToPanels } from "./useTelemetryPipeline";

describe("telemetry panel routing", () => {
  it("pushes and dirties only panels indexed for the trace", () => {
    const traceKey = "dev1:temp";
    const reverse = new Map([[traceKey, new Set(["A", "C"])]]);
    const buffers = new Map([
      ["A", new Map([[traceKey, new RingBuffer(10)]])],
      ["B", new Map([["dev2:pressure", new RingBuffer(10)]])],
      ["C", new Map([[traceKey, new RingBuffer(10)]])],
    ]);
    const dirty = new Set<string>();

    pushTelemetrySampleToPanels(reverse, buffers, traceKey, 12, 34, dirty);

    expect([...dirty]).toEqual(["A", "C"]);
    expect(buffers.get("A")?.get(traceKey)?.latest()).toEqual({ time: 12, value: 34 });
    expect(buffers.get("B")?.get("dev2:pressure")?.latest()).toBeNull();
    expect(buffers.get("C")?.get(traceKey)?.latest()).toEqual({ time: 12, value: 34 });
  });
});
