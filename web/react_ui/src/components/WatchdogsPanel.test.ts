import { describe, expect, it } from "vitest";
import type { WatchdogStatus } from "../types";
import {
  summarizeWatchdogRules,
  watchdogRuleLiveState,
} from "./WatchdogsPanel";

function watchdog(
  rule: Partial<WatchdogStatus["rules"][number]>
): WatchdogStatus {
  return {
    watchdog_id: "vacuum_protection",
    enabled: true,
    rules: [
      {
        name: "beam_loss_shutdown",
        severity: "critical",
        latched: false,
        ...rule,
      },
    ],
  };
}

describe("watchdog status presentation", () => {
  it("shows the live trigger independently from a latch", () => {
    const rule = watchdog({ alarm: true, latched: true }).rules[0];

    expect(watchdogRuleLiveState(rule)).toEqual({
      label: "Triggered now",
      color: "red",
    });
    expect(summarizeWatchdogRules(watchdog(rule))).toEqual({
      label: "1 triggered",
      color: "red",
    });
  });

  it("shows a handled condition as safe while retaining latch attention", () => {
    const rule = watchdog({
      alarm: false,
      unknown: false,
      latched: true,
      last_evaluated_mono: 10,
    }).rules[0];

    expect(watchdogRuleLiveState(rule)).toEqual({
      label: "Safe now",
      color: "teal",
    });
    expect(summarizeWatchdogRules(watchdog(rule))).toEqual({
      label: "1 latched",
      color: "orange",
    });
  });

  it("makes fail-safe unknown triggers explicit", () => {
    const rule = watchdog({ alarm: true, unknown: true }).rules[0];

    expect(watchdogRuleLiveState(rule)).toEqual({
      label: "Triggered: input unavailable",
      color: "red",
    });
  });
});
