import { describe, expect, it } from "vitest";
import {
  normalizeSequencerErrorDetail,
  normalizeSequencerProgress,
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
