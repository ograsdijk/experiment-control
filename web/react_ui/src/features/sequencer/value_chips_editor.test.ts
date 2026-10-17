// @vitest-environment jsdom

import { EditorState, Text } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { afterEach, describe, expect, it } from "vitest";
import { normalizeResolvedVars, type ValueContext } from "./resolved_values";
import { setValueContext, valueChipsExtension, varDefinitionLines } from "./value_chips_editor";

let view: EditorView | null = null;

// jsdom has no layout; CodeMirror measures text ranges.
const emptyRects = () => Object.assign([], { item: () => null }) as unknown as DOMRectList;
Range.prototype.getClientRects ??= emptyRects;
Range.prototype.getBoundingClientRect ??= () => new DOMRect();

afterEach(() => {
  view?.destroy();
  view = null;
});

function chipsFor(doc: string, ctx: ValueContext): string[] {
  view = new EditorView({
    state: EditorState.create({ doc, extensions: [valueChipsExtension()] }),
    parent: document.body,
  });
  view.dispatch({ effects: setValueContext.of(ctx) });
  return Array.from(view.dom.querySelectorAll(".cm-ec-value"), (el) => el.textContent ?? "");
}

describe("YAML value chips", () => {
  it("finds a template that spans lines", () => {
    const doc = ["vars:", "  a: 2", "steps:", "  - sleep: ${a *", "      2}"].join("\n");
    const resolved = normalizeResolvedVars({
      vars: { a: 2 },
      expressions: { "${a *\n      2}": { ok: true, value: 4 } },
    });
    expect(chipsFor(doc, { resolved, liveEnv: null, nextRunOverrides: null })).toEqual(["ƒ4"]);
  });

  it("puts the chip after the closing quote of a quoted template", () => {
    const doc = ["vars:", "  a: 2", "steps:", '  - sleep: "${a}"'].join("\n");
    const resolved = normalizeResolvedVars({ vars: { a: 2 } });
    expect(chipsFor(doc, { resolved, liveEnv: null, nextRunOverrides: null })).toEqual(["2"]);
    const line = view!.dom.querySelector(".cm-ec-value")!.closest(".cm-line")!;
    expect(line.textContent).toBe('  - sleep: "${a}"2');
  });

  it("shows a var definition chip instead of its template chip", () => {
    const doc = ["vars:", "  a: 2", "  b: ${a * 3}", "steps:", "  - sleep: ${b}"].join("\n");
    const resolved = normalizeResolvedVars({
      vars: { a: 2, b: 6 },
      derived: ["b"],
      formulas: { b: "${a * 3}" },
      expressions: { "${a * 3}": { ok: true, value: 6 } },
    });
    expect(chipsFor(doc, { resolved, liveEnv: null, nextRunOverrides: null })).toEqual(["ƒ6", "ƒ6"]);
  });
});

describe("varDefinitionLines", () => {
  it("finds the top-level vars entries", () => {
    const doc = Text.of([
      "version: 1",
      "vars:",
      "  a: 1",
      "  # note",
      "  b: ${a * 2}",
      "  gen_var:",
      "    gen: {values: [1]}",
      "steps:",
      "  - sleep: ${a}",
    ]);
    expect(Array.from(varDefinitionLines(doc).entries())).toEqual([
      [3, "a"],
      [5, "b"],
      [6, "gen_var"],
    ]);
  });

  it("agrees with the request on where the block ends", () => {
    const doc = Text.of(["vars:", "  a: 1", "---", "  b: 2", '"steps":', "  c: 3"]);
    expect(Array.from(varDefinitionLines(doc).entries())).toEqual([[2, "a"]]);
  });
});
