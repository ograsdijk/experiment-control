import { describe, expect, it } from "vitest";
import { buildSequencerStepOutline } from "./outline";
import { summaryUnit } from "./components/SequencerStepTree";

function summaries(yamlText: string): Array<string | null> {
  return buildSequencerStepOutline(yamlText).map((node) => node.summary);
}

describe("step summaries", () => {
  it("shows the first call params and how many more there are", () => {
    const yamlText = [
      "steps:",
      "  - call:",
      "      device: freq1",
      "      action: set_frequency_hz",
      "      params:",
      "        frequency_hz: ${freq_hz}",
      "  - call:",
      "      device: scope",
      "      action: configure",
      "      params:",
      "        a: 1",
      "        b: ${b_v}",
      "        c: 3",
      "        d: 4",
      "  - call:",
      "      device: yag",
      "      action: fire",
      "      params: {}",
    ].join("\n");
    expect(summaries(yamlText)).toEqual([
      "freq1.set_frequency_hz  frequency_hz: ${freq_hz}",
      "scope.configure  a: 1, b: ${b_v} +2 more",
      "yag.fire",
    ]);
  });

  it("shows a generator's settings", () => {
    const yamlText = [
      "steps:",
      "  - for:",
      "      bind: freq_hz",
      "      in:",
      "        gen:",
      "          linspace:",
      "            start: ${sweep_start_hz}",
      "            stop: ${sweep_stop_hz}",
      "            num: ${n_steps}",
      "      do:",
      "        - sleep: 0.1",
    ].join("\n");
    expect(summaries(yamlText)[0]).toBe(
      "value -> freq_hz over linspace(start: ${sweep_start_hz}, stop: ${sweep_stop_hz}, num: ${n_steps})"
    );
  });

  it("doesn't cut a long template or count empty params", () => {
    const yamlText = [
      "steps:",
      "  - call:",
      "      device: synth",
      "      action: sweep",
      "      params:",
      "        empty:",
      "        start_hz: ${freq_center_hz - 0.5 * sweep_step_hz * (n_steps - 1)}",
    ].join("\n");
    expect(summaries(yamlText)).toEqual([
      "synth.sweep  start_hz: ${freq_center_hz - 0.5 * sweep_step_hz * (n_steps - 1)}",
    ]);
  });

  it("leaves the unit of a templated wait_until timeout to the value chip", () => {
    const yamlText = [
      "steps:",
      "  - wait_until:",
      "      condition: {always: true}",
      "      timeout_s: ${t_s}",
      "  - wait_until:",
      "      condition: {always: true}",
      "      timeout_s: 5",
    ].join("\n");
    expect(summaries(yamlText)).toEqual(["timeout ${t_s}", "timeout 5s"]);
  });

  it("puts seconds only on a sleep duration or wait_until timeout", () => {
    const nodes = buildSequencerStepOutline(
      [
        "steps:",
        "  - sleep: ${settle_s}",
        "  - wait_until:",
        "      condition: {always: true}",
        "      timeout_s: ${t_s}",
        "  - wait_until:",
        "      condition: {gt: ['${power_dbm}', '${threshold}']}",
      ].join("\n")
    );
    expect(nodes.map(summaryUnit)).toEqual(["s", "s", undefined]);
  });
});
