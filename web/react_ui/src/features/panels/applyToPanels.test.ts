import { describe, expect, it } from "vitest";

import { RingBuffer } from "../../utils/ringBuffer";
import { pushStreamScalarSample } from "./applyToPanels";

describe("pushStreamScalarSample", () => {
  it("does not append the same timestamp from overlapping snapshots", () => {
    const buffer = new RingBuffer(20);

    expect(pushStreamScalarSample(buffer, 10, 2.5)).toBe(true);
    expect(pushStreamScalarSample(buffer, 10, 2.5)).toBe(false);
    expect(buffer.toArrays()).toEqual([[10], [2.5]]);
  });

  it("continues appending newer samples", () => {
    const buffer = new RingBuffer(20);

    pushStreamScalarSample(buffer, 10, 2.5);
    expect(pushStreamScalarSample(buffer, 11, 3.5)).toBe(true);
    expect(buffer.toArrays()).toEqual([
      [10, 11],
      [2.5, 3.5],
    ]);
  });
});
