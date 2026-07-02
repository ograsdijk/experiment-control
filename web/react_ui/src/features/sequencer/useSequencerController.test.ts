import { describe, expect, it } from "vitest";
import {
  buildSequencerLoadRequest,
  buildSequencerStartParams,
} from "./useSequencerController";

describe("sequencer controller request builders", () => {
  it("loads editor YAML even when a library entry is highlighted", () => {
    expect(
      buildSequencerLoadRequest("editor", "spb_microwave_scan", "steps: []")
    ).toEqual({
      action: "sequencer.load",
      params: { text: "steps: []" },
      source: "sequencer-load",
    });
  });

  it("loads the selected library entry only in library mode", () => {
    expect(
      buildSequencerLoadRequest("library", "spb_microwave_scan", "steps: []")
    ).toEqual({
      action: "sequencer.library.load",
      params: { sequence_id: "spb_microwave_scan" },
      source: "sequencer-library-load",
    });
  });

  it("omits sequence_id for editor-loaded starts", () => {
    expect(
      buildSequencerStartParams(
        "editor",
        true,
        "spb_microwave_scan",
        undefined,
        "once",
        1,
        {}
      )
    ).toEqual({});
  });

  it("includes sequence_id for library-loaded starts", () => {
    expect(
      buildSequencerStartParams(
        "library",
        true,
        "spb_microwave_scan",
        undefined,
        "once",
        1,
        {}
      )
    ).toEqual({
      sequence_id: "spb_microwave_scan",
    });
  });
});
