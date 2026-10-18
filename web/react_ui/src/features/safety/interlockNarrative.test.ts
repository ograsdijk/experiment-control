import { describe, expect, it } from "vitest";

import type { FollowerRuleStatus, InterlockInterceptorStatus, ProcessStatus } from "../../types";
import {
  coerceCheckParam,
  commandTargetNoun,
  conditionParamNames,
  followerGuardSentence,
  interlockStatusLine,
  parseCheckReply,
} from "./interlockNarrative";
import { buildInterlockRuleRows } from "./safetyRows";

const interceptor: InterlockInterceptorStatus = {
  interceptor_id: "demo_interlocks",
  enabled: true,
  rule_count: 1,
  routes: [],
  rules: [
    {
      rule_id: "limit",
      name: "limit_temperature_setpoint",
      enabled: true,
      match: { device_id: "*", action: "set_temperature" },
      telemetry: [{ as: "t", device_id: "tec", signal: "temperature", max_age_s: 5, required: true }],
      condition: { and: [{ ge: ["${params.temperature}", 10] }, { le: ["${params.temperature}", 30] }] },
      on_block: { code: "CONDITION_FAILED", message: "Temperature setpoint out of range (10-30 C)." },
      has_allow_transform: false,
    },
  ],
};

function row(state = "RUNNING", enabled = true, rulesetEnabled = true) {
  const process: ProcessStatus = { process_id: "interlock", state };
  return buildInterlockRuleRows(
    [process],
    {
      interlock: [
        {
          ...interceptor,
          enabled: rulesetEnabled,
          rules: [{ ...interceptor.rules[0], enabled }],
        },
      ],
    },
    {}
  )[0];
}

describe("commandTargetNoun", () => {
  it.each([
    ["*", "set_temperature", "set_temperature commands (any device)"],
    ["neon_flow", "*", "neon_flow commands"],
    ["tec", "set", "tec.set commands"],
    ["*", "*", "all commands"],
  ])("%s.%s", (device, action, expected) => {
    expect(commandTargetNoun(device, action)).toBe(expected);
  });
});

describe("interlockStatusLine", () => {
  it("describes an active rule with its block message and fail-closed note", () => {
    const line = interlockStatusLine(row());
    expect(line.tone).toBe("teal");
    expect(line.text).toBe(
      "Active: checks every set_temperature command (any device)."
    );
    expect(line.blockText).toBe("Temperature setpoint out of range (10-30 C).");
    expect(line.notes).toContain("If a required input has no fresh data, the command is blocked.");
  });

  it("says what disabled and not-enforced mean for commands", () => {
    expect(interlockStatusLine(row("RUNNING", false)).text).toBe(
      "Disabled: set_temperature commands (any device) pass without this check."
    );
    expect(interlockStatusLine(row("RUNNING", true, false)).text).toContain(
      "Ruleset demo_interlocks is disabled"
    );
    const stopped = interlockStatusLine(row("STOPPED"));
    expect(stopped.tone).toBe("red");
    expect(stopped.text).toBe(
      "Not enforced: interlock is STOPPED, so set_temperature commands (any device) are not checked by this rule."
    );
  });
});

describe("followerGuardSentence", () => {
  const follower: FollowerRuleStatus = {
    rule_id: "nltl",
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
  };
  const followerRow = buildInterlockRuleRows(
    [{ process_id: "follower", state: "RUNNING" }],
    {},
    { follower: [follower] }
  )[0];

  it("describes range and step limits with the live value", () => {
    const sentence = followerGuardSentence(followerRow, 1.4321e9);
    expect(sentence).toMatch(/^Rejects synth\.set_frequency \(frequency\) outside /);
    expect(sentence).toContain(", or steps larger than ");
    expect(sentence).toContain("from the current synth.frequency, now ");
  });

  it("returns null for ruleset rules", () => {
    expect(followerGuardSentence(row())).toBeNull();
  });
});

describe("conditionParamNames", () => {
  it("collects params referenced anywhere in the condition", () => {
    expect(
      conditionParamNames({
        and: [{ ge: ["${params.temperature}", 10] }, { lt: ["${params.ramp_rate}", "${t.value}"] }],
      })
    ).toEqual(["temperature", "ramp_rate"]);
    expect(conditionParamNames(true)).toEqual([]);
  });
});

describe("check a command", () => {
  it("parses blocked, allowed, transformed and failed replies", () => {
    expect(
      parseCheckReply({
        ok: true,
        allow: false,
        interceptor_id: "demo_interlocks",
        rule: "limit_temperature_setpoint",
        error: { code: "CONDITION_FAILED", message: "out of range" },
      })
    ).toEqual({
      kind: "blocked",
      rule: "limit_temperature_setpoint",
      interceptorId: "demo_interlocks",
      message: "out of range",
      code: "CONDITION_FAILED",
    });
    expect(parseCheckReply({ ok: true, allow: true })).toEqual({ kind: "allowed" });
    expect(
      parseCheckReply({
        ok: true,
        allow: true,
        note: "transformed",
        rule: "clamp",
        command: { params: { temperature: 30 } },
      })
    ).toEqual({ kind: "transformed", rule: "clamp", params: { temperature: 30 } });
    expect(parseCheckReply({ ok: false, error: { code: "invalid_command" } })).toEqual({
      kind: "error",
      message: "invalid_command",
    });
  });

  it("coerces form values", () => {
    expect(coerceCheckParam("35")).toBe(35);
    expect(coerceCheckParam("true")).toBe(true);
    expect(coerceCheckParam("[1,2]")).toEqual([1, 2]);
    expect(coerceCheckParam("ramp")).toBe("ramp");
    expect(coerceCheckParam("")).toBeNull();
  });
});
