import { describe, expect, it } from "vitest";
import { parse } from "yaml";
import { buildSequencerOutlineMetadata } from "../outline";
import { applyEditedVars } from "./top_level";

describe("sequencer top-level metadata editing", () => {
  it("preserves an indented flow list when an unrelated variable is edited", () => {
    const yaml = [
      "version: 1",
      "vars:",
      "  scalar: 1",
      "  freq_grid_hz:",
      "    [-20.0e+6, 0.0, 20.0e+6] # keep the grid",
      "steps: []",
      "",
    ].join("\n");
    const metadata = buildSequencerOutlineMetadata(yaml);
    const entries = metadata.vars.map((entry) =>
      entry.name === "scalar" ? { ...entry, value: "2" } : entry
    );

    const next = applyEditedVars(yaml, entries);

    expect(parse(next).vars).toEqual({
      scalar: 2,
      freq_grid_hz: [-20.0e6, 0, 20.0e6],
    });
    expect(next).toContain("# keep the grid");
  });
});
