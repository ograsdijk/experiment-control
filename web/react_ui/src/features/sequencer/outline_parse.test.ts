import { describe, expect, it } from "vitest";
import { parseSequencerOutline } from "./outline_parse";
import { parseInWorker } from "./parse_worker_client";

const YAML = [
  "version: 1",
  "context_columns:",
  "  level: float64",
  "vars:",
  "  n: 3",
  "  device: dev_a",
  "steps:",
  "  - set:",
  "      device: ${device}",
  "      field: power",
  "      value: 1.5",
  "  - for:",
  "      bind: { value: level, index: i }",
  "      in: { gen: { linspace: { start: 0, stop: 1, num: \"${n}\" } } }",
  "      do:",
  "        - set_context: { fields: { level: \"${level}\" } }",
  "        - call: { device: dev_a, action: read }",
  "  - if:",
  "      condition:",
  "        all:",
  "          - { signal: dev_a.power, op: \">\", value: 1 }",
  "      then:",
  "        - sleep: 0.5",
  "  - wait_until:",
  "      condition:",
  "        any: []",
  "      timeout_s: 5",
  "  - repeat:",
  "      times: 2",
  "      do:",
  "        - sleep: \"${n}\"",
  "",
].join("\n");

describe("parseSequencerOutline", () => {
  it("survives the copy from the parse worker unchanged", () => {
    const parsed = parseSequencerOutline(YAML);
    expect(parsed.outline.length).toBeGreaterThan(0);
    expect(parsed.metadata.vars.map((entry) => entry.name)).toEqual(["n", "device"]);
    // postMessage uses the structured clone algorithm: functions or class
    // instances in the result would throw or come back changed.
    expect(structuredClone(parsed)).toEqual(parsed);
  });

  it("reports text it cannot parse instead of throwing", () => {
    const parsed = parseSequencerOutline("steps: [\n");
    expect(parsed.text).toBe("steps: [\n");
    expect(parsed.outline).toEqual([]);
  });
});

describe("parseInWorker", () => {
  it("parses in the page when workers are unavailable", async () => {
    expect(typeof Worker).toBe("undefined");
    await expect(parseInWorker("outline", YAML)).resolves.toEqual(parseSequencerOutline(YAML));
    const decoded = await parseInWorker("templates", 'steps:\n  - sleep: "${a + \\"b\\"}"\n');
    expect(decoded).toEqual({ '${a + \\"b\\"}': '${a + "b"}' });
  });
});
