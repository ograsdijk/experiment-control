import { describe, expect, it, vi } from "vitest";
import {
  computeSequencerDiagnosticJumpPlan,
  focusSequencerDiagnosticOffset,
  lineColumnToOffset,
} from "./diagnostics_jump";

describe("sequencer diagnostic jump helpers", () => {
  it("computes text offsets for line/column positions", () => {
    const yaml = ["steps:", "  - sleep: 0.1", "  - call: {}", ""].join("\n");
    expect(lineColumnToOffset(yaml, 1, 1)).toBe(0);
    expect(lineColumnToOffset(yaml, 2, 5)).toBe(11);
    expect(lineColumnToOffset(yaml, 99, 1)).toBe(yaml.length);
  });

  it("returns a jump plan requiring edit mode when currently in preview", () => {
    const yaml = ["steps:", "  - sleep: 0.1", ""].join("\n");
    const plan = computeSequencerDiagnosticJumpPlan(yaml, "preview", 2, 6);

    expect(plan).not.toBeNull();
    expect(plan?.requiresEditMode).toBe(true);
    expect(plan?.offset).toBe(lineColumnToOffset(yaml, 2, 6));
  });

  it("returns a jump plan without mode switch when already in edit", () => {
    const yaml = ["steps:", "  - sleep: 0.1", ""].join("\n");
    const plan = computeSequencerDiagnosticJumpPlan(yaml, "edit", 2, 6);

    expect(plan).not.toBeNull();
    expect(plan?.requiresEditMode).toBe(false);
    expect(plan?.offset).toBe(lineColumnToOffset(yaml, 2, 6));
  });

  it("focuses the editor at the computed offset", () => {
    const focusAtOffset = vi.fn<(offset: number) => void>();
    const ok = focusSequencerDiagnosticOffset({ focusAtOffset, focus: vi.fn() }, 42);

    expect(ok).toBe(true);
    expect(focusAtOffset).toHaveBeenCalledWith(42);
  });

  it("returns false when no editor handle is available", () => {
    expect(focusSequencerDiagnosticOffset(null, 10)).toBe(false);
  });
});

