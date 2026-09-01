import { describe, expect, it } from "vitest";
import {
  normalizeWatchdogStatusDetailed,
  watchdogRuleDetails,
} from "./watchdogStatusApi";

describe("detailed watchdog status normalization", () => {
  it("preserves arm and confirmation state without converting null timestamps to zero", () => {
    const watchdog = normalizeWatchdogStatusDetailed({
      watchdog_id: "vacuum-cryo_watchdog",
      enabled: true,
      rules: [
        {
          name: "eql_pressure_turbos_off",
          severity: "critical",
          latched: false,
          alarm: false,
          unknown: false,
          armed: true,
          arm: {
            condition: { lt: ["${p.value}", 5e-3] },
            disarm_condition: { always: false },
            disarm_on_trigger: false,
          },
          confirmation: {
            consecutive_samples: 3,
            sample_aliases: ["p"],
            progress: {
              p: { count: 2, evidence: [{ value: 2e-2 }] },
            },
          },
          last_evaluated_mono: null,
          last_evaluated_age_s: null,
          stable_since_mono: null,
          stable_since_age_s: null,
          last_trigger_mono: null,
          last_trigger_age_s: null,
          telemetry: [],
          actions: [],
        },
      ],
    });

    expect(watchdog).not.toBeNull();
    const rule = watchdog!.rules[0];
    const details = watchdogRuleDetails(rule);
    expect(details.armed).toBe(true);
    expect(details.arm?.disarm_on_trigger).toBe(false);
    expect(details.confirmation?.consecutive_samples).toBe(3);
    expect(details.confirmation?.progress.p.count).toBe(2);
    expect(rule.last_evaluated_mono).toBeNull();
    expect(rule.last_evaluated_age_s).toBeNull();
    expect(rule.stable_since_mono).toBeNull();
    expect(rule.last_trigger_mono).toBeNull();
  });

  it("keeps optional telemetry bindings optional", () => {
    const watchdog = normalizeWatchdogStatusDetailed({
      watchdog_id: "vacuum-cryo_watchdog",
      enabled: true,
      rules: [
        {
          name: "eql_pressure_turbos_off",
          severity: "critical",
          latched: false,
          telemetry: [
            {
              as: "spb_on",
              device_id: "hipace_spb",
              signal: "pumpg_statn",
              max_age_s: 5,
              required: false,
            },
          ],
          actions: [],
        },
      ],
    });

    expect(watchdog?.rules[0].telemetry?.[0].required).toBe(false);
  });
});
