import { describe, expect, it } from "vitest";
import type { WatchdogStatus } from "../types";
import {
  beamlineTurboProtectionSummary,
  confirmationProgress,
  summarizeWatchdogRules,
  watchdogRuleLiveState,
} from "./WatchdogsPanel";
import type { DetailedWatchdogRule } from "../features/watchdogs/watchdogStatusApi";

function watchdog(
  rule: Partial<DetailedWatchdogRule>,
  enabled = true
): WatchdogStatus {
  return {
    watchdog_id: "vacuum_protection",
    enabled,
    rules: [
      {
        name: "beam_loss_shutdown",
        severity: "critical",
        latched: false,
        ...rule,
      } as DetailedWatchdogRule,
    ],
  };
}

function turboRule(
  name: "rc_pressure_turbos_off" | "eql_pressure_turbos_off" | "det_pressure_turbos_off",
  rule: Partial<DetailedWatchdogRule>
): DetailedWatchdogRule {
  return {
    name,
    severity: "critical",
    latched: false,
    last_evaluated_mono: 10,
    arm: { condition: { lt: ["${p.value}", 5e-3] } },
    armed: false,
    ...rule,
  } as DetailedWatchdogRule;
}

describe("watchdog status presentation", () => {
  it("shows an actual active trip independently from a latch", () => {
    const rule = watchdog({
      alarm: true,
      latched: true,
      active_trip_id: "trip-1",
    }).rules[0];

    expect(watchdogRuleLiveState(rule)).toEqual({
      label: "TRIGGERED",
      color: "red",
    });
    expect(summarizeWatchdogRules(watchdog(rule))).toEqual({
      label: "1 triggered",
      color: "red",
    });
  });

  it("does not call a pre-confirmation alarm triggered", () => {
    const rule = watchdog({
      alarm: true,
      unknown: false,
      last_evaluated_mono: 10,
      arm: { condition: {} },
      armed: true,
      active_trip_id: null,
    }).rules[0];

    expect(watchdogRuleLiveState(rule)).toEqual({
      label: "ARMED · CONFIRMING",
      color: "orange",
    });
    expect(summarizeWatchdogRules(watchdog(rule))).toEqual({
      label: "1 confirming",
      color: "orange",
    });
  });

  it("shows a high condition before arming as disarmed", () => {
    const rule = watchdog({
      alarm: true,
      unknown: false,
      last_evaluated_mono: 10,
      arm: { condition: {} },
      armed: false,
    }).rules[0];

    expect(watchdogRuleLiveState(rule)).toEqual({
      label: "DISARMED",
      color: "gray",
    });
  });

  it("shows an armed rule as armed and safe", () => {
    const rule = watchdog({
      alarm: false,
      unknown: false,
      last_evaluated_mono: 10,
      arm: { condition: {} },
      armed: true,
    }).rules[0];

    expect(watchdogRuleLiveState(rule)).toEqual({
      label: "ARMED · SAFE",
      color: "teal",
    });
  });

  it("makes fail-safe unknown trips explicit", () => {
    const rule = watchdog({
      alarm: true,
      unknown: true,
      active_trip_id: "trip-unknown",
    }).rules[0];

    expect(watchdogRuleLiveState(rule)).toEqual({
      label: "TRIGGERED · INPUT UNAVAILABLE",
      color: "red",
    });
  });

  it("labels unavailable telemetry as degraded protection", () => {
    const rule = watchdog({
      alarm: false,
      unknown: true,
      last_evaluated_mono: 10,
    }).rules[0];

    expect(watchdogRuleLiveState(rule)).toEqual({
      label: "UNKNOWN",
      color: "yellow",
    });
    expect(summarizeWatchdogRules(watchdog(rule))).toEqual({
      label: "Protection degraded · 1 unavailable",
      color: "yellow",
    });
  });

  it("reports confirmation progress", () => {
    const rule = watchdog({
      confirmation: {
        consecutive_samples: 3,
        sample_aliases: ["p"],
        progress: { p: { count: 2, evidence: [] } },
      },
    }).rules[0];

    expect(confirmationProgress(rule)).toEqual({ count: 2, target: 3 });
  });

  it("keeps beamline protection active when one Hornet is unavailable", () => {
    const rules = [
      turboRule("rc_pressure_turbos_off", { unknown: true }),
      turboRule("eql_pressure_turbos_off", { unknown: false, armed: true }),
      turboRule("det_pressure_turbos_off", { unknown: false, armed: true }),
    ];

    expect(beamlineTurboProtectionSummary(rules)).toEqual({
      label: "Beamline protection degraded · 2/3 sensors armed",
      color: "yellow",
    });
  });

  it("shows the beamline protection as disabled when the watchdog is disabled", () => {
    const rules = [
      turboRule("rc_pressure_turbos_off", { armed: true }),
      turboRule("eql_pressure_turbos_off", { armed: true }),
      turboRule("det_pressure_turbos_off", { armed: true }),
    ];

    expect(beamlineTurboProtectionSummary(rules, false)).toEqual({
      label: "Beamline protection disabled",
      color: "gray",
    });
  });
});
