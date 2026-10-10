import { describe, expect, it } from "vitest";
import {
  formatSequencerEta,
  holdSequencerPercent,
  normalizeSequencerErrorDetail,
  normalizeSequencerProgress,
  sequencerDisplayPercent,
  normalizeSequencerStepDetail,
  sameSequencerStatus,
} from "./utils";
import type { SequencerStatus } from "./types";

describe("sequencer status normalization", () => {
  it("normalizes structured step and error details", () => {
    const step = normalizeSequencerStepDetail({
      kind: "call",
      summary: "call fs740.timestamp",
      path: "steps[0]",
      line: 3,
      source: "test.yaml",
      branch: "finally",
      target_kind: "device",
      device: "fs740",
      action: "timestamp",
    });

    expect(step).toMatchObject({
      kind: "call",
      summary: "call fs740.timestamp",
      path: "steps[0]",
      line: 3,
      branch: "finally",
      targetKind: "device",
      device: "fs740",
      action: "timestamp",
    });

    const detail = normalizeSequencerErrorDetail({
      message: "timeout",
      formatted: "timeout [call fs740.timestamp]",
      step,
      cleanup_errors: [{ message: "cleanup timeout", formatted: "cleanup timeout" }],
    });

    expect(detail?.formatted).toBe("timeout [call fs740.timestamp]");
    expect(detail?.cleanupErrors).toHaveLength(1);
    expect(detail?.cleanupErrors[0].message).toBe("cleanup timeout");
  });

  it("normalizes progress estimate reason", () => {
    const progress = normalizeSequencerProgress({
      completed_steps: 2,
      total_steps: null,
      total_steps_known: false,
      estimate_reason: "while loop has unknown iteration count",
    });

    expect(progress?.totalSteps).toBeNull();
    expect(progress?.totalStepsKnown).toBe(false);
    expect(progress?.estimateReason).toContain("while");
  });

  it("normalizes time-based progress fields and tolerates their absence", () => {
    const progress = normalizeSequencerProgress({
      completed_steps: 40,
      total_steps: 100,
      percent: 40,
      time_percent: 62.5,
      eta_s: 75,
      eta_wall_ts: 1_700_000_000,
      approximate: true,
      scope: "loop",
      phase: "cleanup",
      cleanup_completed_steps: 1,
      cleanup_total_steps: 3,
    });
    expect(progress).toMatchObject({
      timePercent: 62.5,
      etaWallTs: 1_700_000_000,
      approximate: true,
      scope: "loop",
      phase: "cleanup",
      cleanupCompletedSteps: 1,
      cleanupTotalSteps: 3,
    });
    expect(sequencerDisplayPercent(progress)).toBe(62.5);

    const legacy = normalizeSequencerProgress({ completed_steps: 1, percent: 10 });
    expect(legacy).toMatchObject({
      timePercent: null,
      approximate: false,
      scope: "run",
      phase: "run",
    });
    expect(sequencerDisplayPercent(legacy)).toBe(10);
  });

  it("keeps the displayed percent from moving backwards while it measures the same thing", () => {
    let hold: ReturnType<typeof holdSequencerPercent> = null;
    const step = (raw: Record<string, unknown>, value: number) => {
      hold = holdSequencerPercent(hold, normalizeSequencerProgress(raw), value);
      return hold?.value;
    };
    expect(step({ run_id: 1 }, 30)).toBe(30);
    // Total grew (another while iteration): hold.
    expect(step({ run_id: 1 }, 25)).toBe(30);
    // The ETA appeared: the bar is now time-based and restarts.
    expect(step({ run_id: 1, time_percent: 5 }, 5)).toBe(5);
    expect(step({ run_id: 1, time_percent: 4 }, 4)).toBe(5);
    // Cleanup and a new run restart it too.
    expect(step({ run_id: 1, time_percent: 1, phase: "cleanup" }, 1)).toBe(1);
    expect(step({ run_id: 2 }, 0)).toBe(0);
  });

  it("restarts the bar for each loop of a continuous run", () => {
    let hold: ReturnType<typeof holdSequencerPercent> = null;
    const loop = (loopsCompleted: number, value: number) => {
      const progress = normalizeSequencerProgress({
        run_id: 1,
        scope: "loop",
        loops_completed: loopsCompleted,
        time_percent: value,
      });
      hold = holdSequencerPercent(hold, progress, value);
      return hold?.value;
    };
    expect(loop(0, 99)).toBe(99);
    expect(loop(1, 3)).toBe(3);
  });

  it("formats the ETA with an approximate marker", () => {
    const exact = normalizeSequencerProgress({ eta_s: 125 });
    expect(formatSequencerEta(exact)).toBe("2:05");
    const approx = normalizeSequencerProgress({ eta_s: 125, approximate: true });
    expect(formatSequencerEta(approx)).toBe("~2:05");
    expect(formatSequencerEta(normalizeSequencerProgress({}))).toBeNull();
  });

  it("compares new status fields", () => {
    const base: SequencerStatus = {
      runId: 1,
      state: "RUNNING",
      currentStep: "CallStep",
      currentStepDetail: null,
      loopMode: "once",
      loopsCompleted: 0,
      loopsTarget: 1,
      error: null,
      errorDetail: null,
      cleanupActive: false,
      loaded: true,
      activeSequenceId: null,
      contextColumns: null,
      loadedSource: null,
      autoloadError: null,
      progress: null,
      loadedAdaptiveIds: [],
      adaptiveStudies: {},
    };

    expect(
      sameSequencerStatus(base, {
        ...base,
        currentStepDetail: { kind: "call", summary: "call a.b", path: "steps[0]", line: 1, column: null, source: "x", branch: null },
      })
    ).toBe(false);
  });
});
