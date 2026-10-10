import { describe, expect, it } from "vitest";
import { mapDiagnosticsToSteps, stepPathAtLine } from "./diagnostic_locations";
import { buildSequencerStepOutline } from "./outline";
import type { SequencerDiagnostic } from "./types";

const YAML = [
  "version: 1", // 1
  "steps:", // 2
  "  - sleep: 1", // 3
  "  - repeat:", // 4
  "      times: 2", // 5
  "      do:", // 6
  "        - call:", // 7
  "            device: fs740", // 8
  "            action: flush", // 9
  "  - sleep: 2", // 10
  "",
].join("\n");

const diag = (line: number | null, severity: SequencerDiagnostic["severity"] = "error", stale = false): SequencerDiagnostic => ({
  severity,
  message: `problem at ${line}`,
  line,
  column: null,
  source: "test",
  stale,
});

describe("diagnostic locations", () => {
  const outline = buildSequencerStepOutline(YAML);

  it("finds the innermost step containing a line", () => {
    expect(stepPathAtLine(outline, 8).map((n) => n.kind)).toEqual(["repeat", "call"]);
    expect(stepPathAtLine(outline, 5).map((n) => n.kind)).toEqual(["repeat"]);
    expect(stepPathAtLine(outline, 1)).toEqual([]);
  });

  it("attaches diagnostics to their step and rolls them up to parents", () => {
    const { byStepId, insideById } = mapDiagnosticsToSteps(outline, [
      diag(8, "warning"),
      diag(9, "error"),
      diag(10, "warning"),
    ]);
    const [, repeat, last] = outline;
    const call = repeat.children[0];
    expect(byStepId.get(call.id)?.map((d) => d.line)).toEqual([8, 9]);
    expect(byStepId.get(repeat.id)).toBeUndefined();
    expect(insideById.get(repeat.id)).toEqual({ count: 2, severity: "error" });
    expect(insideById.get(last.id)).toEqual({ count: 1, severity: "warning" });
  });

  it("leaves stale and line-less diagnostics out", () => {
    const { byStepId, insideById } = mapDiagnosticsToSteps(outline, [
      diag(8, "error", true),
      diag(null),
    ]);
    expect(byStepId.size).toBe(0);
    expect(insideById.size).toBe(0);
  });
});
