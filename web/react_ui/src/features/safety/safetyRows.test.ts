import { describe, expect, it } from "vitest";

import type {
  FollowerRuleStatus,
  InterlockInterceptorStatus,
  ProcessStatus,
  WatchdogStatus,
} from "../../types";
import { selectVisibleResources } from "../devices/health";
import {
  buildInterlockRuleRows,
  buildWatchdogRuleRows,
  interlockRuleAdapter,
  interlockRuleHealth,
  watchdogRuleHealth,
  watchdogRuleNeedsAttention,
  type WatchdogRule,
} from "./safetyRows";

const running = (id: string): ProcessStatus => ({ process_id: id, state: "RUNNING" });
const stopped = (id: string): ProcessStatus => ({ process_id: id, state: "STOPPED" });

const interceptor = (
  enabled: boolean,
  rules: Array<{ id: string; enabled: boolean; transform?: boolean }>
): InterlockInterceptorStatus => ({
  interceptor_id: "demo_interlocks",
  enabled,
  rule_count: rules.length,
  routes: [],
  rules: rules.map((r) => ({
    rule_id: r.id,
    name: r.id,
    enabled: r.enabled,
    match: { device_id: "tec", action: "set_temperature" },
    telemetry: [{ as: "t", device_id: "tec", signal: "temperature", max_age_s: 5, required: true }],
    condition: { lt: ["${t.value}", 25] },
    has_allow_transform: Boolean(r.transform),
  })),
});

const follower = (overrides: Partial<FollowerRuleStatus>): FollowerRuleStatus => ({
  rule_id: "nltl_guard",
  name: "nltl_guard",
  enabled: true,
  device_id: "synth",
  trigger_action: "set_frequency",
  trigger_param: "frequency",
  min_freq_hz: 1e9,
  max_freq_hz: 2e9,
  max_step_hz: 5e6,
  current_freq_signal: "frequency",
  csv_path: "",
  effects: [],
  ...overrides,
});

describe("buildInterlockRuleRows", () => {
  it("flattens follower guards and ruleset rules with kinds", () => {
    const rows = buildInterlockRuleRows(
      [running("interlock")],
      {
        interlock: [
          interceptor(true, [
            { id: "a", enabled: true },
            { id: "b", enabled: false, transform: true },
          ]),
        ],
      },
      {
        interlock: [
          follower({}),
          follower({ rule_id: "range_only", name: "range_only", max_step_hz: null }),
        ],
      }
    );
    expect(rows.map((r) => [r.name, r.kindLabel, r.active])).toEqual([
      ["nltl_guard", "range + step guard", true],
      ["range_only", "range guard", true],
      ["a", "condition", true],
      ["b", "transform", false],
    ]);
    expect(rows[2].key).toBe("interlock:demo_interlocks:a");
    expect(rows[0].key).toBe("interlock:follower:nltl_guard");
    expect(rows[2].inputCount).toBe(1);
  });

  it("marks enabled rules of an unreachable process as failed", () => {
    const rows = buildInterlockRuleRows(
      [stopped("interlock")],
      { interlock: [interceptor(true, [{ id: "a", enabled: true }, { id: "b", enabled: false }])] },
      {}
    );
    expect(rows.map(interlockRuleHealth)).toEqual(["failed", "neutral"]);
  });

  it("treats rules in a disabled ruleset as neutral, not failed", () => {
    const rows = buildInterlockRuleRows(
      [stopped("interlock")],
      { interlock: [interceptor(false, [{ id: "a", enabled: true }])] },
      {}
    );
    expect(interlockRuleHealth(rows[0])).toBe("neutral");
  });

  it("searches match targets and telemetry inputs", () => {
    const rows = buildInterlockRuleRows(
      [running("interlock")],
      { interlock: [interceptor(true, [{ id: "a", enabled: true }])] },
      { interlock: [follower({})] }
    );
    const hit = (q: string) =>
      selectVisibleResources(rows, interlockRuleAdapter, {
        query: q,
        issuesOnly: false,
        sort: "custom",
      }).map((r) => r.name);
    expect(hit("tec.temperature")).toEqual(["a"]);
    expect(hit("synth.set_frequency")).toEqual(["nltl_guard"]);
  });
});

const watchdog = (enabled: boolean, rules: Partial<WatchdogRule>[]): WatchdogStatus => ({
  watchdog_id: "demo_watchdog",
  enabled,
  rules: rules.map((r, i) => ({
    name: `rule_${i}`,
    severity: "warn",
    latched: false,
    last_evaluated_mono: 1,
    ...r,
  })),
});

describe("watchdog rule rows", () => {
  it("maps live state to health", () => {
    const rows = buildWatchdogRuleRows([running("watchdog")], {
      watchdog: [
        watchdog(true, [
          {},
          { unknown: true },
          { latched: true },
          // Triggered needs a live trip id; an alarm alone is still confirming.
          { alarm: true, active_trip_id: "trip-1" } as Partial<WatchdogRule>,
          { last_evaluated_mono: null },
        ]),
      ],
    });
    expect(rows.map(watchdogRuleHealth)).toEqual([
      "healthy",
      "stale",
      "degraded",
      "failed",
      "transition",
    ]);
    expect(rows.map(watchdogRuleNeedsAttention)).toEqual([false, false, true, true, false]);
  });

  it("is neutral when the ruleset is disabled and failed when the process is down", () => {
    expect(
      buildWatchdogRuleRows([running("w")], { w: [watchdog(false, [{ alarm: true }])] }).map(
        watchdogRuleHealth
      )
    ).toEqual(["neutral"]);
    expect(
      buildWatchdogRuleRows([stopped("w")], { w: [watchdog(true, [{}])] }).map(
        watchdogRuleHealth
      )
    ).toEqual(["failed"]);
  });
});
