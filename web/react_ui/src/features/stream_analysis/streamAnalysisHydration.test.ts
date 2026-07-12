import { describe, expect, it } from "vitest";

import {
  isStreamAnalysisRefreshOutputRequested,
  normalizeStreamAnalysisRefreshRequests,
  streamAnalysisHydrationInvalidationRequests,
} from "./streamAnalysisHydration";

describe("isStreamAnalysisRefreshOutputRequested", () => {
  const request = { workspaceId: "ws-a", outputIds: ["hist-a"] };

  it("accepts only explicitly requested workspace outputs", () => {
    expect(
      isStreamAnalysisRefreshOutputRequested(request, "ws-a", "hist-a")
    ).toBe(true);
    expect(
      isStreamAnalysisRefreshOutputRequested(request, "ws-a", "scalar-a")
    ).toBe(false);
    expect(
      isStreamAnalysisRefreshOutputRequested(request, "ws-b", "hist-a")
    ).toBe(false);
  });
});

describe("normalizeStreamAnalysisRefreshRequests", () => {
  it("merges only explicitly requested outputs per workspace", () => {
    expect(
      normalizeStreamAnalysisRefreshRequests([
        { workspaceId: " ws-a ", outputIds: ["trace-a", ""] },
        {
          workspaceId: "ws-a",
          outputIds: ["trace-b", "trace-a"],
          maxTracePoints: 1200,
        },
        { workspaceId: "ws-b", outputIds: ["hist-a"] },
      ])
    ).toEqual([
      {
        workspaceId: "ws-a",
        outputIds: ["trace-a", "trace-b"],
        maxTracePoints: 1200,
      },
      { workspaceId: "ws-b", outputIds: ["hist-a"] },
    ]);
  });

  it("drops malformed and output-less requests", () => {
    expect(
      normalizeStreamAnalysisRefreshRequests([
        { workspaceId: "", outputIds: ["scalar-a"] },
        { workspaceId: "ws-a", outputIds: [] },
      ])
    ).toEqual([]);
  });
});

describe("streamAnalysisHydrationInvalidationRequests", () => {
  it("normalizes requests without broadening their output scope", () => {
    const event = new CustomEvent("test", {
      detail: {
        requests: [
          { workspaceId: "ws-a", outputIds: ["hist-a", "hist-a"] },
        ],
      },
    });
    expect(streamAnalysisHydrationInvalidationRequests(event)).toEqual([
      { workspaceId: "ws-a", outputIds: ["hist-a"] },
    ]);
  });

  it("ignores malformed events", () => {
    expect(
      streamAnalysisHydrationInvalidationRequests(new Event("test"))
    ).toEqual([]);
    expect(
      streamAnalysisHydrationInvalidationRequests(
        new CustomEvent("test", { detail: { requests: "ws-a" } })
      )
    ).toEqual([]);
  });
});
