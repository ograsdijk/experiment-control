import { describe, expect, it } from "vitest";
import {
  buildLocalConditionDiagnostics,
  mergeDiagnostics,
} from "./validation";
import type { SequencerDiagnostic } from "./types";

describe("sequencer condition validation diagnostics", () => {
  it("produces line-referenced errors for invalid compare arguments", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - if:",
      "      condition:",
      "        gt: [, 0]",
      "      then: []",
      "",
    ].join("\n");
    const diagnostics = buildLocalConditionDiagnostics(yaml);

    expect(diagnostics.length).toBeGreaterThan(0);
    expect(diagnostics.some((diag) => diag.severity === "error")).toBe(true);
    expect(diagnostics.some((diag) => diag.line === 4)).toBe(true);
  });

  it("reports warnings for raw/unsupported condition expressions", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - while:",
      "      condition:",
      "        foo: bar",
      "      do:",
      "        - sleep: 0.1",
      "",
    ].join("\n");
    const diagnostics = buildLocalConditionDiagnostics(yaml);

    expect(diagnostics.some((diag) => diag.severity === "warning")).toBe(true);
    expect(
      diagnostics.some((diag) => (diag.source ?? "").includes("condition.local"))
    ).toBe(true);
  });

  it("deduplicates merged diagnostics by location/message/source", () => {
    const first: SequencerDiagnostic[] = [
      {
        severity: "error",
        message: "x",
        line: 10,
        column: null,
        source: "condition.local",
      },
    ];
    const second: SequencerDiagnostic[] = [
      {
        severity: "error",
        message: "x",
        line: 10,
        column: null,
        source: "condition.local",
      },
      {
        severity: "warning",
        message: "y",
        line: 11,
        column: null,
        source: "condition.local",
      },
    ];
    const merged = mergeDiagnostics(first, second);
    expect(merged).toHaveLength(2);
  });
});

