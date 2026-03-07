import { describe, expect, it } from "vitest";
import {
  conditionAstToEntries,
  defaultConditionAst,
  parseConditionEntries,
  validateConditionAst,
} from "./condition_ast";

describe("condition_ast", () => {
  it("parses an empty condition list", () => {
    expect(parseConditionEntries([])).toEqual({ kind: "empty" });
  });

  it("parses a supported compare operator", () => {
    const ast = parseConditionEntries([
      { name: "gt", value: "[${sample_reduced}, 0.0]" },
    ]);
    expect(ast).toEqual({
      kind: "compare",
      op: "gt",
      left: "${sample_reduced}",
      right: "0.0",
    });
  });

  it("parses abs_lt expression", () => {
    const ast = parseConditionEntries([
      { name: "abs_lt", value: "[${sample_reduced - target}, 0.1]" },
    ]);
    expect(ast).toEqual({
      kind: "compare",
      op: "abs_lt",
      left: "${sample_reduced - target}",
      right: "0.1",
    });
  });

  it("falls back to raw when multiple entries are present", () => {
    const ast = parseConditionEntries([
      { name: "gt", value: "[${x}, 0]" },
      { name: "lt", value: "[${x}, 10]" },
    ]);
    expect(ast.kind).toBe("raw");
  });

  it("parses top-level and with nested compares", () => {
    const ast = parseConditionEntries([
      {
        name: "and",
        value: "[{gt: [${x}, 0]}, {lt: [${x}, 10]}]",
      },
    ]);
    expect(ast).toEqual({
      kind: "and",
      items: [
        { kind: "compare", op: "gt", left: "${x}", right: "0" },
        { kind: "compare", op: "lt", left: "${x}", right: "10" },
      ],
    });
  });

  it("parses top-level not with nested compare", () => {
    const ast = parseConditionEntries([
      {
        name: "not",
        value: "{ge: [${err}, 1.5]}",
      },
    ]);
    expect(ast).toEqual({
      kind: "not",
      item: { kind: "compare", op: "ge", left: "${err}", right: "1.5" },
    });
  });

  it("serializes compare AST to entries", () => {
    const entries = conditionAstToEntries({
      kind: "compare",
      op: "le",
      left: "${x}",
      right: "10",
    });
    expect(entries).toEqual([{ name: "le", value: "[${x}, 10]" }]);
  });

  it("serializes and AST to entries", () => {
    const entries = conditionAstToEntries({
      kind: "and",
      items: [
        { kind: "compare", op: "gt", left: "${x}", right: "0" },
        { kind: "compare", op: "lt", left: "${x}", right: "10" },
      ],
    });
    expect(entries).toEqual([
      { name: "and", value: "[{gt: [${x}, 0]}, {lt: [${x}, 10]}]" },
    ]);
  });

  it("serializes not AST to entries", () => {
    const entries = conditionAstToEntries({
      kind: "not",
      item: { kind: "compare", op: "lt", left: "${err}", right: "1.0" },
    });
    expect(entries).toEqual([{ name: "not", value: "{lt: [${err}, 1.0]}" }]);
  });

  it("round-trips simple compare AST through entry codec", () => {
    const start = defaultConditionAst("ge");
    const entries = conditionAstToEntries(start);
    const parsed = parseConditionEntries(entries);
    expect(parsed).toEqual(start);
  });

  it("round-trips nested logical AST through entry codec", () => {
    const start = {
      kind: "or" as const,
      items: [
        {
          kind: "and" as const,
          items: [
            { kind: "compare" as const, op: "gt" as const, left: "${x}", right: "0" },
            { kind: "compare" as const, op: "lt" as const, left: "${x}", right: "10" },
          ],
        },
        {
          kind: "not" as const,
          item: { kind: "compare" as const, op: "eq" as const, left: "${state}", right: '"locked"' },
        },
      ],
    };
    const entries = conditionAstToEntries(start);
    const parsed = parseConditionEntries(entries);
    expect(parsed).toEqual(start);
  });

  it("falls back to raw when operator is unsupported", () => {
    const ast = parseConditionEntries([{ name: "foo", value: "bar" }]);
    expect(ast.kind).toBe("raw");
  });

  it("falls back to raw when compare args are not a two-item list", () => {
    const ast = parseConditionEntries([{ name: "gt", value: "${x} > 0" }]);
    expect(ast.kind).toBe("raw");
  });

  it("validates compare arguments", () => {
    const issues = validateConditionAst({
      kind: "compare",
      op: "gt",
      left: "",
      right: "0",
    });
    expect(issues.some((issue) => issue.severity === "error")).toBe(true);
  });

  it("warns for and/or with a single clause", () => {
    const issues = validateConditionAst({
      kind: "and",
      items: [{ kind: "compare", op: "gt", left: "${x}", right: "0" }],
    });
    expect(issues.some((issue) => issue.severity === "warning")).toBe(true);
  });
});
