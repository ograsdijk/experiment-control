import { describe, expect, it } from "vitest";

import { buildStreamRawData } from "./StreamRawPanel";

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
});
