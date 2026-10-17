import { describe, expect, it } from "vitest";
import { entryPathSegment, splitEntryPath } from "./entry_path";

describe("entry paths", () => {
  it("quotes only keys that would not read back as themselves", () => {
    expect(entryPathSegment("freq_hz")).toBe("freq_hz");
    expect(entryPathSegment("a.b")).toBe('"a.b"');
    expect(entryPathSegment('"x"')).toBe('"\\"x\\""');
  });

  it("splits on dots outside quoted segments", () => {
    expect(splitEntryPath("freq_hz.call.device")).toEqual(["freq_hz", "call", "device"]);
    expect(splitEntryPath('cfg."x.y".z')).toEqual(["cfg", "x.y", "z"]);
    expect(splitEntryPath(' a . b ..')).toEqual(["a", "b"]);
    expect(splitEntryPath('"a\\".b"')).toEqual(['a".b']);
  });

  it("reads back every key it writes", () => {
    for (const key of ["plain", "a.b", '"quoted"', 'x"y', "a\\.b"]) {
      expect(splitEntryPath(`p.${entryPathSegment(key)}`)).toEqual(["p", key]);
    }
  });
});
