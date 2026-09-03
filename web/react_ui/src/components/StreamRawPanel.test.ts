import { describe, expect, it } from "vitest";

import {
  buildStreamRawData,
  extractTrace,
  sampleIndexArray,
} from "./StreamRawPanel";

describe("buildStreamRawData", () => {
  it("refuses to plot a truncated frame", () => {
    const built = buildStreamRawData(
      [
        {
          seq: 4,
          shape: [200_000],
          values: [1, 2, 3],
          truncated: true,
          originalShape: [5, 120_000],
          originalPointCount: 600_000,
          maxPayloadPoints: 200_000,
        },
      ],
      1,
      0
    );

    expect(built.data).toEqual([[], []]);
  });

  it("reuses ingestion-normalized traces and cached sample indexes", () => {
    const normalized = [1, 2, 3];
    const frame = {
      seq: 5,
      shape: [3],
      values: ["unused"],
      normalizedTrace: normalized,
      normalizedChannelCount: 4,
    };
    expect(extractTrace(frame, 2)).toEqual({ y: normalized, channelCount: 4 });
    expect(extractTrace(frame, 2).y).toBe(normalized);
    const built = buildStreamRawData([frame], 1, 2);
    expect(built.data[0]).toBe(sampleIndexArray(3));
    expect(built.data[1]).toBe(normalized);
    expect(sampleIndexArray(3)).toBe(sampleIndexArray(3));
  });
});
