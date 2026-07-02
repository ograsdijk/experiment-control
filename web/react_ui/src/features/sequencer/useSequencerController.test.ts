import { describe, expect, it } from "vitest";
import {
  buildSequencerLoadRequest,
  buildSequencerStartParams,
  normalizeSequencerLibraryPayload,
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
        true
      )
    ).toBe("spb_microwave_scan");
  });

  it("falls back to the backend active id when requested", () => {
    expect(
      resolveSequencerSelectedSequenceId(
        null,
        "spa_ch2",
        [{ id: "spb_microwave_scan" }, { id: "spa_ch2" }],
        true
      )
    ).toBe("spa_ch2");
  });

  it("prefers the first entry for display when not in library mode", () => {
    expect(
      resolveSequencerSelectedSequenceId(
        null,
        "spa_ch2",
        [{ id: "spb_microwave_scan" }, { id: "spa_ch2" }],
        false
      )
    ).toBe("spb_microwave_scan");
  });

  it("preserves the current selection even in editor mode", () => {
    expect(
      resolveSequencerSelectedSequenceId(
        "spa_ch2",
        "spb_microwave_scan",
        [{ id: "spb_microwave_scan" }, { id: "spa_ch2" }],
        false
      )
    ).toBe("spa_ch2");
  });

  it("returns null when there are no entries", () => {
    expect(resolveSequencerSelectedSequenceId(null, "a", [], true)).toBeNull();
  });

  it("parses nested sequence library payloads", () => {
    const payload = normalizeSequencerLibraryPayload({
      result: {
        configured: true,
        entries: [
          {
            id: "spb_microwave_scan",
            label: "SPB microwave frequency scan",
            description: "test",
            path: "/abs/path/spb_microwave_scan.yaml",
            source: "autoload",
            vars: ["freq_start_ghz"],
          },
        ],
        active_sequence_id: null,
        last_error: null,
      },
    });

    expect(payload?.entries).toHaveLength(1);
    expect(payload?.entries[0].id).toBe("spb_microwave_scan");
  });

  it("parses direct sequence library payloads", () => {
    const payload = normalizeSequencerLibraryPayload({
      configured: true,
      entries: [{ id: "a", path: "/x/a.yaml", source: "autoload", vars: [] }],
      active_sequence_id: null,
      last_error: null,
    });

    expect(payload?.entries).toHaveLength(1);
    expect(payload?.entries[0].id).toBe("a");
  });
});
