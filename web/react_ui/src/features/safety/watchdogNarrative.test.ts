import { describe, expect, it } from "vitest";

import type { ProcessStatus, WatchdogStatus } from "../../types";
import { buildWatchdogRuleRows, type WatchdogRule } from "./safetyRows";
import {
  formatReading,
  lastActionOutcomes,
  parseActionError,
  simpleComparison,
  watchdogInputs,
  watchdogStatusLine,
} from "./watchdogNarrative";

const BLOCKED_ERROR = {
  ok: false,
  error: {
    kind: "command_interceptor",
    code: "INTERCEPTOR_REJECTED",
    message: "neon_flow commands locked out for maintenance.",
    process_id: "interlock",
    interceptor_id: "demo_interlocks",
    rule: "neon_flow_lockout",
  },
};

function rule(overrides: Record<string, unknown>): WatchdogRule {
  return {
    name: "neon_flow_voltage_spike",
    severity: "critical",
    latched: false,
    last_evaluated_mono: 10,
    on_unknown: "ignore",
    condition: { gt: ["${v.value}", 3.15] },
    telemetry: [{ as: "v", device_id: "neon_flow", signal: "voltage", max_age_s: 3, required: true }],
    snapshot: {
      v: { device: "neon_flow", signal: "voltage", value: 3.263538451479701, age_s: 0.7, ok: true, quality: "OK" },
    },
    ...overrides,
  } as WatchdogRule;
}

function lineFor(r: WatchdogRule, processState = "RUNNING", enabled = true) {
  const process: ProcessStatus = { process_id: "watchdog", state: processState };
  const status: WatchdogStatus = { watchdog_id: "demo", enabled, rules: [r] };
  return watchdogStatusLine(buildWatchdogRuleRows([process], { watchdog: [status] })[0]);
}

describe("formatReading", () => {
  it.each([
    [3.263538451479701, "3.264"],
    [20, "20"],
    [0.000012, "1.200e-5"],
    [12345.6, "1.235e+4"],
    [1234.5678, "1235"],
  ])("%s -> %s", (value, expected) => {
    expect(formatReading(value)).toBe(expected);
  });
});

describe("inputs and comparison", () => {
  it("merges telemetry config with the live snapshot", () => {
    expect(watchdogInputs(rule({}))).toEqual([
      {
        alias: "v",
        label: "neon_flow.voltage",
        value: 3.263538451479701,
        ageS: 0.7,
        maxAgeS: 3,
        quality: "OK",
        ok: true,
      },
    ]);
  });

  it("summarises a single comparison and skips compound conditions", () => {
    expect(simpleComparison(rule({}))).toMatchObject({
      label: "neon_flow.voltage",
      symbol: ">",
      threshold: 3.15,
    });
    expect(simpleComparison(rule({ condition: { and: [] } }))).toBeNull();
  });
});

describe("action errors", () => {
  it("recognises an interlock rejection", () => {
    expect(parseActionError(BLOCKED_ERROR)).toEqual({
      message: "neon_flow commands locked out for maintenance.",
      blockedBy: { processId: "interlock", interceptorId: "demo_interlocks", rule: "neon_flow_lockout" },
    });
    expect(parseActionError("timeout")).toEqual({ message: "timeout", blockedBy: null });
  });

  it("lists per-action outcomes", () => {
    const r = rule({
      last_action_chain: {
        state: "completed",
        success: false,
        action_count: 1,
        succeeded_actions: 0,
        failed_actions: 1,
        trip_id: "t",
        duration_ms: 3,
        actions: [
          {
            action_index: 0,
            command: { device_id: "neon_flow", action: "set_temperature", params: {} },
            ok: false,
            attempts: 1,
            error: BLOCKED_ERROR,
          },
        ],
      },
    });
    expect(lastActionOutcomes(r)).toEqual([
      {
        target: "neon_flow.set_temperature",
        ok: false,
        attempts: 1,
        message: "neon_flow commands locked out for maintenance.",
        blockedBy: { processId: "interlock", interceptorId: "demo_interlocks", rule: "neon_flow_lockout" },
      },
    ]);
  });
});

describe("watchdogStatusLine", () => {
  it("explains a trip blocked by an interlock", () => {
    const line = lineFor(
      rule({
        alarm: true,
        active_trip_id: "t",
        last_trigger_age_s: 36,
        last_action_chain: {
          state: "completed",
          success: false,
          action_count: 1,
          succeeded_actions: 0,
          failed_actions: 1,
          trip_id: "t",
          duration_ms: 3,
          actions: [
            {
              action_index: 0,
              command: { device_id: "neon_flow", action: "set_temperature", params: {} },
              ok: false,
              attempts: 1,
              error: BLOCKED_ERROR,
            },
          ],
        },
      })
    );
    expect(line.tone).toBe("red");
    expect(line.text).toBe("Tripped 36 s ago: neon_flow.voltage 3.264 > 3.15.");
    expect(line.actionText).toContain('blocked by interlock rule "neon_flow_lockout"');
    expect(line.blockedBy?.rule).toBe("neon_flow_lockout");
  });

  it("names the missing input and the on_unknown consequence", () => {
    const line = lineFor(
      rule({
        unknown: true,
        snapshot: { v: { device: "neon_flow", signal: "voltage", value: null, ok: false } },
      })
    );
    expect(line.tone).toBe("yellow");
    expect(line.text).toBe(
      "Not evaluating: neon_flow.voltage has no fresh data. On unknown = ignore, so this rule cannot trip until the data returns."
    );
  });

  it("states the margin when safe", () => {
    expect(
      lineFor(
        rule({ snapshot: { v: { device: "neon_flow", signal: "voltage", value: 3.08, ok: true } } })
      ).text
    ).toBe("Safe: neon_flow.voltage 3.08, trips when > 3.15.");
  });

  it("covers latched, disabled and stopped-process cases", () => {
    expect(lineFor(rule({ latched: true })).text).toBe(
      "Recovered but latched: clear the latch to re-arm."
    );
    expect(lineFor(rule({}), "RUNNING", false).tone).toBe("gray");
    expect(lineFor(rule({}), "STOPPED").text).toBe(
      "watchdog is STOPPED: this rule is not evaluated."
    );
  });
});
