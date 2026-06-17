import { describe, expect, it } from "vitest";
import { applyEditedForStep } from "./step_for";
import { applyEditedCallStep } from "./step_call";
import { applyEditedSetContextStep, applyEditedSetStep } from "./step_control";
import {
  buildSequencerStepOutline,
  flattenSequencerStepOutline,
} from "../outline";
import type {
  SequencerOutlineMetadataEntry,
  SequencerStepOutlineNode,
} from "../types";

function nodeByKind(yamlText: string, kind: string, index = 0): SequencerStepOutlineNode {
  const node = flattenSequencerStepOutline(buildSequencerStepOutline(yamlText)).filter(
    (item) => item.kind === kind
  )[index];
  if (!node) {
    throw new Error(`missing ${kind} node at index ${index}`);
  }
  return node;
}

function asMap(entries: ReadonlyArray<SequencerOutlineMetadataEntry>): Record<string, string | null> {
  return Object.fromEntries(entries.map((entry) => [entry.name, entry.value]));
}

function setVal(
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>,
  name: string,
  value: string
): SequencerOutlineMetadataEntry[] {
  return entries.map((entry) => (entry.name === name ? { ...entry, value } : entry));
}

// These shapes come straight from the real state-preparation-b-detection
// `spb_raster_scan.yaml`, which the old regex editor mis-read and corrupted.
describe("for-loop round-trip with inline flow maps (spb_raster_scan shapes)", () => {
  it("reads an inline bind map and direct expression; editing never emits bind: {}", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - for:",
      "      bind: {value: ch}",
      '      in: "${wm_channels}"',
      "      do:",
      "        - sleep: 0.5",
      "",
    ].join("\n");

    const node = nodeByKind(yaml, "for");
    expect(node.forDetail?.bind).toEqual([{ name: "value", value: "ch" }]);
    expect(node.forDetail?.sourceMode).toBe("direct");
    expect(node.forDetail?.directValue).toBe('"${wm_channels}"');
    // Summary now includes the actual expression, not just "over expression".
    expect(node.summary).toContain("${wm_channels}");

    const next = applyEditedForStep(
      yaml,
      node,
      [{ name: "value", value: "ch2" }],
      "direct",
      null,
      '"${wm_channels}"',
      [],
      []
    );
    expect(next).not.toContain("bind: {}");
    expect(next).toContain('in: "${wm_channels}"');
    expect(next).toContain("- sleep: 0.5");
    const reparsed = nodeByKind(next, "for").forDetail;
    expect(reparsed?.bind).toEqual([{ name: "value", value: "ch2" }]);
    expect(reparsed?.directValue).toBe('"${wm_channels}"');
  });

  it("reads an inline linspace generator into start/stop/num fields", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - for:",
      "      bind: {value: f}",
      "      in:",
      "        gen:",
      '          linspace: {start: "${a}", stop: "${b}", num: 5}',
      "      do:",
      "        - sleep: 0.1",
      "",
    ].join("\n");

    const detail = nodeByKind(yaml, "for").forDetail;
    expect(detail?.generatorKind).toBe("linspace");
    expect(detail?.iterableConfig).toEqual([
      { name: "start", value: '"${a}"' },
      { name: "stop", value: '"${b}"' },
      { name: "num", value: "5" },
    ]);
  });

  it("reads scan2d shorthand + sample, and editing a field preserves sample", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - for:",
      "      bind: {x: x, y: y, index: spot_index}",
      "      in:",
      "        gen:",
      "          scan2d:",
      "            center:",
      '              x: "${cx}"',
      '              y: "${cy}"',
      "            size:",
      '              width: "${w}"',
      '              height: "${h}"',
      '            pitch: "${p}"',
      '          sample: {count: "${m}", replace: true}',
      "      do:",
      "        - sleep: 0.1",
      "",
    ].join("\n");

    const node = nodeByKind(yaml, "for");
    const detail = node.forDetail;
    expect(detail?.generatorKind).toBe("scan2d");
    const cfg = asMap(detail?.iterableConfig ?? []);
    // size.{width,height} and scalar pitch normalized to the form's flat keys.
    expect(cfg["center.x"]).toBe('"${cx}"');
    expect(cfg["width"]).toBe('"${w}"');
    expect(cfg["height"]).toBe('"${h}"');
    expect(cfg["pitch.x"]).toBe('"${p}"');
    expect(cfg["pitch.y"]).toBe('"${p}"');
    // sample is folded into modifiers (not dropped).
    const mods = asMap(detail?.generatorModifiers ?? []);
    expect(mods["sample.count"]).toBe('"${m}"');
    expect(mods["sample.replace"]).toBe("true");

    // Edit one center value; sample and bind must survive the round-trip.
    const next = applyEditedForStep(
      yaml,
      node,
      detail!.bind,
      "generator",
      "scan2d",
      "",
      detail!.generatorModifiers,
      setVal(detail!.iterableConfig, "center.x", '"${NEWX}"')
    );
    expect(next).toContain("sample:");
    const reparsed = nodeByKind(next, "for").forDetail;
    const reMods = asMap(reparsed?.generatorModifiers ?? []);
    expect(reMods["sample.count"]).toBe('"${m}"');
    expect(reMods["sample.replace"]).toBe("true");
    expect(reparsed?.bind).toEqual([
      { name: "x", value: "x" },
      { name: "y", value: "y" },
      { name: "index", value: "spot_index" },
    ]);
    expect(asMap(reparsed?.iterableConfig ?? [])["center.x"]).toBe('"${NEWX}"');
    expect(next).toContain("- sleep: 0.1");
  });

  it("reads a deeply nested step whose body is followed by an outer-scope comment", () => {
    // Regression: the snippet for a nested step used to sweep in trailing
    // outer-indent comments, which normalizeSnippet then over-stripped into
    // garbage, making the real YAML parser reject the whole step.
    const yaml = [
      "version: 1",
      "steps:",
      "  - for:",
      "      bind: {value: f}",
      "      in:",
      "        gen:",
      "          linspace: {start: 0, stop: 1, num: 3}",
      "      do:",
      "        - for:",
      "            bind: {x: x, y: y}",
      "            in:",
      "              gen:",
      "                scan2d:",
      "                  center: {x: 0, y: 0}",
      "                  size: 10",
      "                  steps: {x: 3, y: 3}",
      "                sample: {count: 4, replace: true}",
      "            do:",
      "              - sleep: 0.1",
      "  # trailing outer comment (used to be swept into the nested step)",
      "  - sleep: 0.2",
      "",
    ].join("\n");

    const inner = flattenSequencerStepOutline(buildSequencerStepOutline(yaml)).filter(
      (n) => n.kind === "for" && n.forDetail?.generatorKind === "scan2d"
    )[0];
    expect(inner).toBeDefined();
    expect(inner.forDetail?.bind).toEqual([
      { name: "x", value: "x" },
      { name: "y", value: "y" },
    ]);
    expect(asMap(inner.forDetail?.generatorModifiers ?? [])["sample.count"]).toBe("4");

    const next = applyEditedForStep(
      yaml,
      inner,
      inner.forDetail!.bind,
      "generator",
      "scan2d",
      "",
      inner.forDetail!.generatorModifiers,
      setVal(inner.forDetail!.iterableConfig, "center.x", "1")
    );
    expect(next).toContain("sample:");
    expect(next).toContain("- sleep: 0.1"); // nested body intact
    expect(next).toContain("- sleep: 0.2"); // sibling after the comment intact
    expect(next).toContain("# trailing outer comment"); // comment not mangled/lost
  });

  it("reads an inline call params flow map", () => {
    const yaml = [
      "version: 1",
      "steps:",
      '  - call: {device: bristol_wavemeter, action: move_to_channel, params: {channel: "${ch}"}}',
      "",
    ].join("\n");

    const detail = nodeByKind(yaml, "call").callDetail;
    expect(detail?.device).toBe("bristol_wavemeter");
    expect(detail?.action).toBe("move_to_channel");
    expect(detail?.params).toEqual([{ name: "channel", value: '"${ch}"' }]);

    const next = applyEditedCallStep(yaml, nodeByKind(yaml, "call"), "bristol_wavemeter", "move_to_channel", [
      { name: "channel", value: '"${ch2}"' },
    ]);
    expect(nodeByKind(next, "call").callDetail?.params).toEqual([
      { name: "channel", value: '"${ch2}"' },
    ]);
  });

  it("preserves a non-scalar (inline list) set value instead of dropping it", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - set: {device: psu, name: ramp, value: [0, 200, 400]}",
      "",
    ].join("\n");

    const detail = nodeByKind(yaml, "set").setDetail;
    expect(detail?.value).toBe("[0, 200, 400]");

    // The list value must survive the round-trip (it used to be dropped to "").
    // Flow spacing may be normalized by the serializer, so compare whitespace-insensitively.
    const next = applyEditedSetStep(yaml, nodeByKind(yaml, "set"), "psu", "ramp", "[0, 200, 400]");
    const reValue = nodeByKind(next, "set").setDetail?.value ?? "";
    expect(reValue.replace(/\s+/g, "")).toBe("[0,200,400]");
  });

  it("preserves a block (multi-line) list set value instead of dropping it", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - set:",
      "      device: psu",
      "      name: ramp",
      "      value:",
      "        - 0",
      "        - 200",
      "        - 400",
      "",
    ].join("\n");

    // A block collection used to read as null -> blank field -> the value was
    // destroyed on save. It is now flow-normalized to one round-trippable line.
    const detail = nodeByKind(yaml, "set").setDetail;
    expect(detail?.value?.replace(/\s+/g, "")).toBe("[0,200,400]");

    const next = applyEditedSetStep(
      yaml,
      nodeByKind(yaml, "set"),
      "psu",
      "ramp",
      detail!.value ?? ""
    );
    const reValue = nodeByKind(next, "set").setDetail?.value ?? "";
    expect(reValue.replace(/\s+/g, "")).toBe("[0,200,400]");
  });

  it("preserves a block (multi-line) map set value instead of dropping it", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - set:",
      "      device: psu",
      "      name: cfg",
      "      value:",
      "        gain: 2",
      '        offset: "${o}"',
      "",
    ].join("\n");

    const detail = nodeByKind(yaml, "set").setDetail;
    expect(detail?.value?.replace(/\s+/g, "")).toBe('{gain:2,offset:"${o}"}');

    const next = applyEditedSetStep(
      yaml,
      nodeByKind(yaml, "set"),
      "psu",
      "cfg",
      detail!.value ?? ""
    );
    const re = nodeByKind(next, "set").setDetail?.value ?? "";
    expect(re).toContain("gain");
    expect(re).toContain("offset");
  });

  it("reads inline set_context streams and round-trips them", () => {
    const yaml = [
      "version: 1",
      "steps:",
      "  - set_context:",
      "      streams:",
      "        - {device: pxie5171, stream: waveforms}",
      "      fields:",
      '        freq_hz: "${f}"',
      "",
    ].join("\n");

    const detail = nodeByKind(yaml, "set_context").setContextDetail;
    expect(detail?.streams).toEqual([{ device: "pxie5171", stream: "waveforms" }]);
    expect(detail?.fields).toEqual([{ name: "freq_hz", value: '"${f}"' }]);

    const next = applyEditedSetContextStep(
      yaml,
      nodeByKind(yaml, "set_context"),
      detail!.streams,
      detail!.fields
    );
    const reparsed = nodeByKind(next, "set_context").setContextDetail;
    expect(reparsed?.streams).toEqual([{ device: "pxie5171", stream: "waveforms" }]);
    expect(reparsed?.fields).toEqual([{ name: "freq_hz", value: '"${f}"' }]);
  });
});
