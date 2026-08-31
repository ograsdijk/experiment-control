import { describe, expect, it } from "vitest";

import type { LogEntry } from "../../types";
import { logsReconnectDelayMs } from "./useLogsStream";
import {
  acceptNewLogEntries,
  formatLogTime,
  formatWallTimeSeconds,
  logEntryKey,
  watchdogToastForLogEntry,
} from "./utils";

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

describe("watchdog toast formatting", () => {
  it("formats triggered watchdog logs as warning toasts", () => {
    expect(
      watchdogToastForLogEntry({
        topic: "manager.watchdog.triggered",
        severity: "warning",
        message: "Vacuum pressure is high",
      })
    ).toEqual({
      color: "yellow",
      title: "Watchdog Triggered",
      message: "Vacuum pressure is high",
    });
  });

  it("formats watchdog failures as error toasts", () => {
    expect(
      watchdogToastForLogEntry({
        topic: "manager.watchdog.action_failed",
        severity: "error",
        message: "Action failed",
      })
    ).toEqual({
      color: "red",
      title: "Watchdog Action Failed",
      message: "Action failed",
    });
  });

  it("ignores non-watchdog logs", () => {
    expect(
      watchdogToastForLogEntry({
        topic: "manager.command",
        severity: "error",
        message: "Command failed",
      })
    ).toBeNull();
  });

  it("ignores routine watchdog events", () => {
    expect(
      watchdogToastForLogEntry({ topic: "manager.watchdog.action_started" })
    ).toBeNull();
    expect(
      watchdogToastForLogEntry({ topic: "manager.watchdog.rules_loaded" })
    ).toBeNull();
  });

  it("formats cleared watchdog latch logs as informational toasts", () => {
    expect(
      watchdogToastForLogEntry({
        topic: "manager.watchdog.latch_cleared",
        severity: "info",
        message: "Watchdog vacuum:pressure_high latch cleared",
      })
    ).toEqual({
      color: "blue",
      title: "Watchdog Latch Cleared",
      message: "Watchdog vacuum:pressure_high latch cleared",
    });
  });
});

describe("log entry deduplication", () => {
  it("uses content identity independent of display position", () => {
    const entry: LogEntry = {
      topic: "manager.log",
      message: "stable",
      ts: { t_mono: 42 },
    };

    expect(logEntryKey(entry)).toBe(logEntryKey({ ...entry }));
  });

  it("returns only entries not already seen", () => {
    const first = {
      topic: "manager.watchdog.triggered",
      message: "Pressure high",
      ts: { t_mono: 42 },
    };
    const second = {
      topic: "manager.watchdog.latch_cleared",
      message: "Pressure normal",
      ts: { t_mono: 43 },
    };
    const seen = new Set<string>();

    expect(acceptNewLogEntries([first, first, second], seen)).toEqual([
      first,
      second,
    ]);
    expect(acceptNewLogEntries([first, second], seen)).toEqual([]);
  });
});

describe("logs WebSocket reconnect backoff", () => {
  it("backs off exponentially with a thirty second cap", () => {
    expect([0, 1, 2, 3, 10].map(logsReconnectDelayMs)).toEqual([
      1000, 2000, 4000, 8000, 30000,
    ]);
  });
});
