import { describe, expect, it } from "vitest";
import {
  buildSequencerLoadRequest,
  buildSequencerStartParams,
  resolveSequencerSelectedSequenceId,
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

  it("keeps an existing valid selection ahead of backend active ids", () => {
    expect(
      resolveSequencerSelectedSequenceId(
        "spb_microwave_scan",
        "spa_ch2",
        [{ id: "spb_microwave_scan" }, { id: "spa_ch2" }],
        "library"
      )
    ).toBe("spb_microwave_scan");
  });

  it("falls back to the backend active id when no valid selection exists", () => {
    expect(
      resolveSequencerSelectedSequenceId(
        null,
        "spa_ch2",
        [{ id: "spb_microwave_scan" }, { id: "spa_ch2" }],
        "library"
      )
    ).toBe("spa_ch2");
  });

  it("does not force an active id while in editor mode", () => {
    expect(
      resolveSequencerSelectedSequenceId(
        null,
        "spa_ch2",
        [{ id: "spb_microwave_scan" }, { id: "spa_ch2" }],
        "editor"
      )
    ).toBeNull();
  });
});
