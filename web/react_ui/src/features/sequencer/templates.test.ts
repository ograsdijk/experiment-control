import { describe, expect, it } from "vitest";
import { decodedTemplates, extractTemplateExpressions, extractVarsBlock, splitTemplates } from "./templates";

describe("splitTemplates", () => {
  it("splits text and extracts distinct expressions", () => {
    expect(splitTemplates("sleep ${a} then ${b + 1}")).toEqual([
      { kind: "text", text: "sleep " },
      { kind: "template", text: "${a}", inner: "a" },
      { kind: "text", text: " then " },
      { kind: "template", text: "${b + 1}", inner: "b + 1" },
    ]);
    expect(extractTemplateExpressions("${a} ${a} ${b}")).toEqual(["${a}", "${b}"]);
  });
});

describe("extractVarsBlock", () => {
  it("keeps only the top-level vars block", () => {
    const text = ["version: 1", "vars:", "  a: 1", "  # note", "", "  b: ${a}", "steps:", "  - sleep: ${a}"].join("\n");
    expect(extractVarsBlock(text)).toBe(["vars:", "  a: 1", "  # note", "", "  b: ${a}", ""].join("\n"));
    expect(extractVarsBlock("vars: {a: 1}\nsteps: []\n")).toBe("vars: {a: 1}\n");
    expect(extractVarsBlock("steps: []\n")).toBe("vars: {}\n");
  });

  it("ends at a document marker and sends everything when the vars use an alias", () => {
    expect(extractVarsBlock("vars:\n  a: 1\n---\nvars:\n  b: 2\n")).toBe("vars:\n  a: 1\n");
    const withAlias = "defaults: &d {gain: 2}\nvars:\n  cfg: *d\nsteps: []\n";
    expect(extractVarsBlock(withAlias)).toBe(withAlias);
    // `*` in an expression is not an alias.
    expect(extractVarsBlock("vars:\n  a: ${b * 2}\nsteps: []\n")).toBe("vars:\n  a: ${b * 2}\n");
  });
});

describe("decodedTemplates", () => {
  it("maps templates as written to what the runtime sees", () => {
    const text = [
      "steps:",
      '  - set: {device: d, name: n, value: "${mode == \\"fast\\"}"}',
      "  - sleep: ${a +",
      "      b}",
      "  - sleep: ${plain}",
    ].join("\n");
    expect(decodedTemplates(text)).toEqual({
      '${mode == \\"fast\\"}': '${mode == "fast"}',
      "${a +\n      b}": "${a + b}",
    });
  });
});
