import { describe, expect, it } from "vitest";
import { applyEditedVars } from "./editing";
import { buildSequencerOutlineMetadata } from "./outline";
import { yamlSourceToDisplay } from "./value_text";

describe("yamlSourceToDisplay", () => {
  it("shows quoted strings bare when the bare text reads back the same", () => {
    expect(yamlSourceToDisplay('"${ch}"')).toBe("${ch}");
    expect(yamlSourceToDisplay("'abc'")).toBe("abc");
    expect(yamlSourceToDisplay('"hello world"')).toBe("hello world");
  });

  it("keeps quotes that carry meaning", () => {
    expect(yamlSourceToDisplay('"5"')).toBe('"5"'); // string, not number
    expect(yamlSourceToDisplay('"true"')).toBe('"true"');
    expect(yamlSourceToDisplay('"#3"')).toBe('"#3"'); // bare would be a comment
    expect(yamlSourceToDisplay('"a: b"')).toBe('"a: b"'); // bare would be a map
    expect(yamlSourceToDisplay('" padded "')).toBe('" padded "');
  });

  it("shows an empty string as an empty field and leaves other source alone", () => {
    expect(yamlSourceToDisplay('""')).toBe("");
    expect(yamlSourceToDisplay(null)).toBe("");
    expect(yamlSourceToDisplay("5.0")).toBe("5.0");
    expect(yamlSourceToDisplay("[1, 2]")).toBe("[1, 2]");
    expect(yamlSourceToDisplay("${x}")).toBe("${x}");
  });
});

describe("committing typed values", () => {
  const base = ["version: 1", "vars:", "  dwell_s: 5.0", "steps: []", ""].join("\n");
  const commit = (typed: string) => {
    const yaml = applyEditedVars(base, [{ name: "dwell_s", value: typed }]);
    const shown = yamlSourceToDisplay(buildSequencerOutlineMetadata(yaml).vars[0]?.value);
    return { yaml, shown };
  };

  // Every keystroke used to be written, read back with its quotes and
  // re-quoted, so values grew a new layer of quotes per character typed.
  it.each([
    ["hello world", "hello world"],
    ["${dwell_s * 2}", "${dwell_s * 2}"],
    ["-5", "-5"],
    ["1e-3", "1e-3"],
    ["[1, 2]", "[1, 2]"],
    ["", ""],
    ['"5"', '"5"'],
  ])("typing %j shows %j after the commit", (typed, shown) => {
    expect(commit(typed).shown).toBe(shown);
  });

  it("is stable when the shown value is committed again", () => {
    for (const typed of ["hello world", '"5"', "#3", "${x}", "C:\\data"]) {
      const first = commit(typed);
      const again = applyEditedVars(first.yaml, [{ name: "dwell_s", value: first.shown }]);
      expect(again).toBe(first.yaml);
    }
  });
});
