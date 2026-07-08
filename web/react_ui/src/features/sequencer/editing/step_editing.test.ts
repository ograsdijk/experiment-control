import { describe, expect, it } from "vitest";
import {
  applyEditedAssignStep,
  applyEditedIfStep,
  applyEditedSetContextStep,
  applyEditedSetStep,
  applyEditedWaitUntilStep,
  applyEditedWhileStep,
} from "./step_control";
import { applyEditedCallStep } from "./step_call";
import { applyEditedAdaptiveStep } from "./step_adaptive";
import {
  insertStepAtTopLevel,
  insertStepBelow,
  insertStepInside,
} from "./tree_ops";
import {
  buildSequencerStepOutline,
  flattenSequencerStepOutline,
} from "../outline";
import type { SequencerStepOutlineNode } from "../types";

function getNodeByKind(
  yamlText: string,
  kind: string,
  index = 0
): SequencerStepOutlineNode {
  const nodes = flattenSequencerStepOutline(buildSequencerStepOutline(yamlText)).filter(
    (node) => node.kind === kind
  );
  const node = nodes[index];
  if (!node) {
    throw new Error(`missing ${kind} node at index ${index}`);
  }
  return node;
}

describe("sequencer editing regressions", () => {
  it("creates steps section when inserting top-level into a metadata-only sequence", () => {
    const yaml = ["version: 1", "vars: {}", ""].join("\n");
    const next = insertStepAtTopLevel(yaml, "sleep");

    expect(next).toContain("steps:");
    expect(next).toContain("  - sleep: 0.1");
    expect(getNodeByKind(next, "sleep").kind).toBe("sleep");
  });

  it("normalizes inline steps and inserts before following top-level section", () => {
    const yaml = ["version: 1", "steps: []", "context_columns: {}", ""].join("\n");
    const next = insertStepAtTopLevel(yaml, "call");

    expect(next).toContain("steps:");
    expect(next).toContain("  - call:");
    expect(next).toContain("context_columns: {}");
    expect(next.indexOf("  - call:")).toBeLessThan(next.indexOf("context_columns: {}"));
  });

  it("appends top-level steps at the end of the existing steps block", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - sleep: 0.1",
      "  - call:",
      "      device: foo",
      "      action: bar",
      "      params: {}",
      "",
    ].join("\n");
    const next = insertStepAtTopLevel(yaml, "repeat");
    const nodes = flattenSequencerStepOutline(buildSequencerStepOutline(next));

    expect(nodes.map((node) => node.kind)).toEqual(["sleep", "call", "repeat", "sleep"]);
  });

  it("supports inserting adaptive at top-level and below existing steps", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - sleep: 0.1",
      "",
    ].join("\n");
    const withTopLevelAdaptive = insertStepAtTopLevel(yaml, "adaptive");
    const sleepNode = getNodeByKind(withTopLevelAdaptive, "sleep");
    const withAdaptiveBelowSleep = insertStepBelow(withTopLevelAdaptive, sleepNode, "adaptive");
    const kinds = flattenSequencerStepOutline(
      buildSequencerStepOutline(withAdaptiveBelowSleep)
    ).map((node) => node.kind);

    expect(kinds.filter((kind) => kind === "adaptive")).toHaveLength(2);
  });

  it("inserts child steps into nested body containers", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - repeat:",
      "      times: 2",
      "      do:",
      "        - sleep: 0.1",
      "",
    ].join("\n");
    const repeat = getNodeByKind(yaml, "repeat");
    const next = insertStepInside(yaml, repeat, "call", "do");
    const nodes = flattenSequencerStepOutline(buildSequencerStepOutline(next));

    expect(nodes.map((node) => node.kind)).toEqual(["repeat", "sleep", "call"]);
  });

  it("inserts child steps into try finally containers", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - try:",
      "      do:",
      "        - sleep: 0.1",
      "      finally:",
      "        - call: {device: yag, action: stop}",
      "",
    ].join("\n");
    const tryStep = getNodeByKind(yaml, "try");
    const next = insertStepInside(yaml, tryStep, "sleep", "finally");
    const nodes = flattenSequencerStepOutline(buildSequencerStepOutline(next));
    const finallyChildren = nodes.filter((node) => node.branchLabel === "finally");

    expect(finallyChildren.map((node) => node.kind)).toEqual(["call", "sleep"]);
  });

  it("edits set step values without dropping sibling steps", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - set:",
      "      device: psu",
      "      name: voltage_v",
      "      value: 0.0",
      "  - sleep: 0.2",
      "",
    ].join("\n");
    const setNode = getNodeByKind(yaml, "set");
    const next = applyEditedSetStep(yaml, setNode, "psu", "voltage_v", "5.0");

    expect(next).toContain("      value: 5.0");
    expect(next).toContain("  - sleep: 0.2");
  });

  it("writes call params with nested keys as nested YAML mappings", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - call:",
      "      device: laser",
      "      action: configure",
      "      params: {}",
      "",
    ].join("\n");
    const callNode = getNodeByKind(yaml, "call");
    const next = applyEditedCallStep(yaml, callNode, "device", "laser", "", "configure", [
      { name: "mode", value: '"cw"' },
      { name: "scan.x.min", value: "0" },
      { name: "scan.x.max", value: "10" },
    ]);
    const reparsed = getNodeByKind(next, "call").callDetail;

    expect(next).toContain("        scan:");
    expect(next).toContain("          x:");
    expect(next).toContain("            min: 0");
    expect(next).toContain("            max: 10");
    expect(reparsed?.params).toContainEqual({ name: "scan.x.min", value: "0" });
    expect(reparsed?.params).toContainEqual({ name: "scan.x.max", value: "10" });
  });

  it("renders assign as an empty mapping when all entries are removed", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - assign:",
      "      foo: 1",
      "  - sleep: 0.1",
      "",
    ].join("\n");
    const assignNode = getNodeByKind(yaml, "assign");
    const next = applyEditedAssignStep(yaml, assignNode, []);
    const reparsedAssign = getNodeByKind(next, "assign");

    expect(next).toContain("  - assign: {}");
    expect(reparsedAssign.assignDetail?.entries).toEqual([]);
    expect(next).toContain("  - sleep: 0.1");
  });

  it("writes set_context streams and fields in structured form", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - set_context:",
      "      streams: []",
      "      fields: {}",
      "",
    ].join("\n");
    const node = getNodeByKind(yaml, "set_context");
    const next = applyEditedSetContextStep(
      yaml,
      node,
      [{ device: "trace1", stream: "trace" }],
      [
        { name: "freq_hz", value: "${freq_hz}" },
        { name: "hv_v", value: "${hv_v}" },
      ]
    );
    const reparsed = getNodeByKind(next, "set_context").setContextDetail;

    expect(next).toContain("        - device: trace1");
    expect(next).toContain("          stream: trace");
    expect(reparsed?.streams).toEqual([{ device: "trace1", stream: "trace" }]);
    expect(reparsed?.fields).toEqual([
      { name: "freq_hz", value: "${freq_hz}" },
      { name: "hv_v", value: "${hv_v}" },
    ]);
  });

  it("updates wait_until sample and condition entries", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - wait_until:",
      "      timeout_s: 10",
      "      every_s: 0.2",
      "      sample: {}",
      "      condition: {}",
      "",
    ].join("\n");
    const node = getNodeByKind(yaml, "wait_until");
    const next = applyEditedWaitUntilStep(
      yaml,
      node,
      "5.0",
      "0.1",
      [
        { name: "telemetry.device", value: "dummy1" },
        { name: "telemetry.signal", value: "temperature" },
      ],
      [{ name: "abs_lt", value: "[${sample_reduced - target}, 0.2]" }]
    );
    const reparsed = getNodeByKind(next, "wait_until").waitUntilDetail;

    expect(reparsed?.timeoutS).toBe("5.0");
    expect(reparsed?.everyS).toBe("0.1");
    expect(reparsed?.sample).toEqual([
      { name: "telemetry.device", value: "dummy1" },
      { name: "telemetry.signal", value: "temperature" },
    ]);
    expect(reparsed?.condition).toEqual([
      { name: "abs_lt", value: "[${sample_reduced - target}, 0.2]" },
    ]);
  });

  it("parses block-style logical conditions into editable entries", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - if:",
      "      condition:",
      "        and:",
      '          - gt: ["${x}", 0]',
      '          - lt: ["${x}", 10]',
      "      then:",
      "        - sleep: 0.1",
      "      else: []",
      "",
    ].join("\n");
    const node = getNodeByKind(yaml, "if");
    expect(node.ifDetail?.condition).toEqual([
      { name: "and", value: "[{gt: [${x}, 0]}, {lt: [${x}, 10]}]" },
    ]);
  });

  it("keeps if then/else bodies when condition is edited", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - if:",
      "      condition:",
      '        gt: ["${x}", 0]',
      "      then:",
      "        - call:",
      "            device: foo",
      "            action: run",
      "            params: {}",
      "      else:",
      "        - sleep: 0.2",
      "",
    ].join("\n");
    const node = getNodeByKind(yaml, "if");
    const next = applyEditedIfStep(yaml, node, [{ name: "lt", value: "[${x}, 10]" }]);
    const reparsed = getNodeByKind(next, "if");

    expect(reparsed.ifDetail?.condition).toEqual([{ name: "lt", value: "[${x}, 10]" }]);
    expect(next).toContain("      then:");
    expect(next).toContain("      else:");
    expect(next).toContain("            action: run");
    expect(next).toContain("        - sleep: 0.2");
  });

  it("keeps while body when condition is edited", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - while:",
      "      condition:",
      '        lt: ["${i}", 10]',
      "      do:",
      "        - assign:",
      "            i: ${i + 1}",
      "",
    ].join("\n");
    const node = getNodeByKind(yaml, "while");
    const next = applyEditedWhileStep(yaml, node, [{ name: "lt", value: "[${i}, 100]" }]);
    const reparsed = getNodeByKind(next, "while");

    expect(reparsed.whileDetail?.condition).toEqual([{ name: "lt", value: "[${i}, 100]" }]);
    expect(next).toContain("      do:");
    expect(next).toContain("        - assign:");
  });

  it("edits adaptive advanced config sections and keeps body", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - adaptive:",
      "      id: sweep",
      "      controller:",
      "        kind: adaptive.adaptive_grid_1d",
      "      space:",
      "        x:",
      "          type: float",
      "          min: 0",
      "          max: 1",
      "      bind:",
      "        value: x",
      "      observe:",
      "        metrics:",
      "          score:",
      "            kind: analysis_output",
      "            config:",
      "              workspace_id: ws",
      "              output_id: y",
      "        aggregate:",
      "          score: [mean]",
      "        score: ${metrics.score}",
      "      stopping:",
      "        max_trials: 10",
      "      do:",
      "        - sleep: 0.1",
      "",
    ].join("\n");
    const adaptiveNode = getNodeByKind(yaml, "adaptive");
    const next = applyEditedAdaptiveStep(
      yaml,
      adaptiveNode,
      "sweep",
      "adaptive.adaptive_grid_1d",
      "0.01",
      [
        { name: "loss.kind", value: '"curvature"' },
        { name: "loss.power", value: "2.0" },
      ],
      [{ name: "x", entries: [{ name: "type", value: "float" }, { name: "min", value: "0" }, { name: "max", value: "1" }] }],
      [{ name: "value", value: "x" }],
      [
        {
          name: "score",
          sourceKind: "analysis_output",
          config: [
            { name: "workspace_id", value: "ws" },
            { name: "output_id", value: "y" },
          ],
        },
      ],
      [{ name: "score", value: "[mean]" }],
      "3",
      "${metrics.score}",
      "25",
      [{ name: "converged.std_below", value: "0.01" }]
    );
    const reparsed = getNodeByKind(next, "adaptive").adaptiveDetail;

    expect(next).toContain("        loss:");
    expect(next).toContain("          kind: \"curvature\"");
    expect(next).toContain("          power: 2.0");
    expect(next).toContain("        converged:");
    expect(next).toContain("          std_below: 0.01");
    expect(next).toContain("      do:");
    expect(next).toContain("        - sleep: 0.1");
    expect(reparsed?.controllerConfig).toContainEqual({
      name: "loss.kind",
      value: '"curvature"',
    });
    expect(reparsed?.stopping).toContainEqual({
      name: "converged.std_below",
      value: "0.01",
    });
  });
});
