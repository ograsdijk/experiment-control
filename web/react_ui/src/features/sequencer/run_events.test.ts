import { describe, expect, it } from "vitest";
import {
  formatSequencerPause,
  normalizeSequencerPause,
  normalizeSequencerRunEvents,
  runEventToDiagnostic,
} from "./utils";

const RAW = {
  run_id: 3,
  seq: 9,
  dropped: 0,
  events: [
    {
      severity: "warning",
      kind: "pause",
      message: "Paused by watchdog: Lock dropped",
      source: "watchdog",
      trigger: { watchdog_id: "lock", rule: "lock_fault", severity: "critical", trip_id: "t1" },
      step: { kind: "call", summary: "fs740.flush", path: "steps[1]", line: 12, branch: null },
      elapsed_s: 75,
      count: 1,
    },
    { severity: "critical", kind: "log", message: "ramp stalled", source: "process:nltl", step: null, elapsed_s: 80, count: 3 },
  ],
};

describe("run events", () => {
  it("normalizes events and maps critical to error", () => {
    const events = normalizeSequencerRunEvents(RAW);
    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({ kind: "pause", severity: "warning", step: { line: 12 } });
    expect(events[0].trigger).toEqual({ watchdogId: "lock", rule: "lock_fault", severity: "critical", tripId: "t1" });
    expect(events[1]).toMatchObject({ severity: "error", count: 3, step: null });
  });

  it("turns an event into a run diagnostic on its line", () => {
    const [pause, log] = normalizeSequencerRunEvents(RAW);
    expect(runEventToDiagnostic(pause, false)).toMatchObject({
      severity: "warning",
      line: 12,
      origin: "run",
      stale: false,
      message: "Run 1:15: Paused by watchdog: Lock dropped",
    });
    const logDiag = runEventToDiagnostic(log, true);
    expect(logDiag.line).toBeNull();
    expect(logDiag.stale).toBe(true);
    expect(logDiag.message).toContain("(x3)");
  });

  it("says who paused and why", () => {
    expect(
      formatSequencerPause(
        normalizeSequencerPause({
          reason: "Lock dropped",
          source: "watchdog",
          trigger: { watchdog_id: "lock", rule: "lock_fault", severity: "critical" },
        })!
      )
    ).toBe("Paused by watchdog lock (rule lock_fault, critical): Lock dropped");
    expect(formatSequencerPause(normalizeSequencerPause({ source: "operator" })!)).toBe(
      "Paused by operator"
    );
  });
});
