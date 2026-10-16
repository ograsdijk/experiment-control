import { describe, expect, it } from "vitest";
import { buildSequencerStepOutline } from "./outline";
import { buildRuntimeOrigins } from "./runtime_origins";

describe("buildRuntimeOrigins", () => {
  const yamlText = [
    "steps:",
    "  - for:",
    "      bind:",
    "        value: freq_hz",
    "        index: freq_step_idx",
    "      in:",
    "        gen:",
    "          linspace:",
    "            start: ${sweep_start_hz}",
    "            stop: ${settle_s}",
    "            num: ${n_steps}",
    "      do:",
    "        - assign: {shots: 1}",
    "        - call:",
    "            device: scope",
    "            action: read",
    "          save_as: resp",
  ].join("\n");
  const origins = buildRuntimeOrigins(buildSequencerStepOutline(yamlText));

  it("finds loop variables, assign targets and save_as names", () => {
    expect(origins.freq_hz).toMatchObject({ label: "loop", description: "loop variable of for at L2", line: 2 });
    expect(origins.freq_hz.over).toBe("linspace(start: ${sweep_start_hz}, stop: ${settle_s}, num: ${n_steps})");
    expect(origins.freq_step_idx).toMatchObject({ label: "index", over: null });
    expect(origins.shots).toMatchObject({ label: "assign", description: "set by assign at L13" });
    expect(origins.resp).toMatchObject({ label: "save_as" });
  });

  it("know the names steps set implicitly", () => {
    const origins = buildRuntimeOrigins(
      buildSequencerStepOutline(
        [
          "steps:",
          "  - call: {device: scope, action: read}",
          "    extract: {kind: scalar}",
          "  - call: {device: scope, action: read}",
          "    assign:",
          "      locked: {kind: key, ref: locked}",
          "  - wait_until:",
          "      condition: {always: true}",
        ].join("\n")
      )
    );
    expect(Object.keys(origins).sort()).toEqual(["locked", "sample", "samples", "value"]);
    expect(origins.value.label).toBe("extract");
    expect(origins.locked.label).toBe("assign");
  });
});
