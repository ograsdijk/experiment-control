import { describe, expect, it } from "vitest";
import {
  hasActiveWatchdogTrip,
  hasWatchdogActionFailure,
  isWatchdogRuleConfirming,
  normalizeWatchdogStatusDetailed,
  watchdogRuleDetails,
  type DetailedWatchdogRule,
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

  it("normalizes the persisted action-chain result", () => {
    const watchdog = normalizeWatchdogStatusDetailed({
      watchdog_id: "vacuum-cryo_watchdog",
      enabled: true,
      rules: [
        {
          name: "eql_pressure_turbos_off",
          severity: "critical",
          latched: false,
          last_action_chain: {
            trip_id: "trip-partial",
            state: "completed",
            success: false,
            action_count: 4,
            succeeded_actions: 3,
            failed_actions: 1,
            duration_ms: 15.2,
            actions: [
              {
                action_index: 1,
                command: { device_id: "hipace_eql", action: "stop" },
                ok: false,
                attempts: 1,
                error: "timeout",
              },
            ],
          },
          telemetry: [],
          actions: [],
        },
      ],
    });

    const chain = watchdogRuleDetails(watchdog!.rules[0]).last_action_chain;
    expect(chain?.state).toBe("completed");
    expect(chain?.success).toBe(false);
    expect(chain?.succeeded_actions).toBe(3);
    expect(chain?.failed_actions).toBe(1);
    expect(chain?.actions[0].command.device_id).toBe("hipace_eql");
    expect(chain?.actions[0].error).toBe("timeout");
    expect(hasWatchdogActionFailure(watchdog!.rules[0])).toBe(true);
  });


  it("distinguishes an active trip from a recovered retained trip id", () => {
    const active = {
      name: "pressure",
      alarm: true,
      unknown: false,
      active_trip_id: "trip-active",
    } as DetailedWatchdogRule;
    const recovered = {
      name: "pressure",
      alarm: false,
      unknown: false,
      active_trip_id: "trip-recovered",
    } as DetailedWatchdogRule;

    expect(hasActiveWatchdogTrip(active)).toBe(true);
    expect(hasActiveWatchdogTrip(recovered)).toBe(false);
  });

  it("only calls an armed gated alarm confirming", () => {
    const armed = {
      name: "pressure",
      alarm: true,
      unknown: false,
      last_evaluated_mono: 10,
      arm: { condition: {} },
      armed: true,
      active_trip_id: null,
    } as DetailedWatchdogRule;
    const disarmed = { ...armed, armed: false } as DetailedWatchdogRule;

    expect(isWatchdogRuleConfirming(armed)).toBe(true);
    expect(isWatchdogRuleConfirming(disarmed)).toBe(false);
  });
});
