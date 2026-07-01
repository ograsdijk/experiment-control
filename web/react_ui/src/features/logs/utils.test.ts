import { describe, expect, it } from "vitest";

import type { LogEntry } from "../../types";
import { formatLogTime, formatWallTimeSeconds } from "./utils";

describe("wall-clock formatting includes the date (TUI parity)", () => {
  // Construct via local-time components so the assertion is timezone-agnostic
  // (both the formatter and this test use the local zone).
  const local = new Date(2026, 6, 1, 9, 5, 3); // 2026-07-01 09:05:03 local
  const epochSeconds = local.getTime() / 1000;
  const expected = "2026-07-01 09:05:03";

  it("formatWallTimeSeconds (command history) includes YYYY-MM-DD", () => {
    expect(formatWallTimeSeconds(epochSeconds)).toBe(expected);
  });

  it("formatLogTime (logs) includes YYYY-MM-DD", () => {
    const entry = { ts: { t_wall: epochSeconds } } as LogEntry;
    expect(formatLogTime(entry)).toBe(expected);
  });

  it("invalid inputs fall back without throwing", () => {
    expect(formatWallTimeSeconds(Number.NaN)).toContain("--:--:--");
    expect(formatLogTime({} as LogEntry)).toContain("--:--:--");
  });
});
