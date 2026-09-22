import { describe, expect, it } from "vitest";
import {
  formatHdfPhaseTimings,
  hdfFileOpOutcomeKey,
  normalizeHdfFileOp,
  normalizeHdfFileOpOutcome,
  normalizeHdfFileState,
} from "./utils";

describe("normalizeHdfFileState", () => {
  it("passes through known states", () => {
    expect(normalizeHdfFileState("closing", true)).toBe("closing");
    expect(normalizeHdfFileState("rotating", true)).toBe("rotating");
  });

  it("falls back to writing_active for writers without file_state", () => {
    expect(normalizeHdfFileState(undefined, true)).toBe("writing");
    expect(normalizeHdfFileState("bogus", false)).toBe("idle");
  });
});

describe("normalizeHdfFileOp", () => {
  it("parses an in-flight op", () => {
    expect(
      normalizeHdfFileOp({
        op: "rotate",
        file: "/data/a.h5",
        new_file: "/data/b.h5",
        elapsed_s: 4.2,
        held_messages: 10,
      })
    ).toEqual({ op: "rotate", file: "/data/a.h5", newFile: "/data/b.h5", elapsedS: 4.2 });
  });

  it("returns null when absent", () => {
    expect(normalizeHdfFileOp(null)).toBeNull();
    expect(normalizeHdfFileOp({})).toBeNull();
  });
});

describe("normalizeHdfFileOpOutcome", () => {
  it("parses a failed op with timings", () => {
    const outcome = normalizeHdfFileOpOutcome({
      op: "stop",
      ok: false,
      file: "/data/a.h5",
      started_wall: 1700000000.5,
      duration_s: 31.7,
      error: { code: "stop_failed", message: "close failed: disk full" },
      phase_timings_s: { drain: 20.1, close: 11.2, bogus: "x" },
      held_dropped: 3,
    });
    expect(outcome).toEqual({
      op: "stop",
      ok: false,
      file: "/data/a.h5",
      newFile: null,
      startedWall: 1700000000.5,
      durationS: 31.7,
      errorMessage: "close failed: disk full",
      phaseTimingsS: { drain: 20.1, close: 11.2 },
      heldDropped: 3,
    });
    expect(hdfFileOpOutcomeKey(outcome)).toBe("stop:1700000000.5:/data/a.h5");
  });

  it("returns null when absent", () => {
    expect(normalizeHdfFileOpOutcome(undefined)).toBeNull();
    expect(hdfFileOpOutcomeKey(null)).toBeNull();
  });
});

describe("formatHdfPhaseTimings", () => {
  it("lists the slowest phases first", () => {
    expect(
      formatHdfPhaseTimings({ drain: 2.06, close: 11.84, flush: 0.2, strict_streams: 0.01 })
    ).toBe("close 11.8 s, drain 2.1 s, flush 0.2 s");
  });
});
