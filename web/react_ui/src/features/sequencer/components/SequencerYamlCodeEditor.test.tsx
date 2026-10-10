// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { SequencerYamlCodeEditor } from "./SequencerYamlCodeEditor";
import type { ActiveStepRange } from "../active_step_editor";
import type { SequencerDiagnostic } from "../types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const YAML = ["version: 1", "steps:", "  - call: {device: fs740, action: flush}", ""].join("\n");

const diag: SequencerDiagnostic = {
  severity: "error",
  message: "unknown device 'fs740'",
  line: 3,
  column: null,
  source: "sequencer.preflight",
};

async function render(
  diagnostics: SequencerDiagnostic[],
  readOnly = false,
  activeStep: ActiveStepRange | null = null,
  onUserScroll?: () => void
) {
  await act(async () => {
    root.render(
      createElement(SequencerYamlCodeEditor, {
        value: YAML,
        onChange: () => undefined,
        colorScheme: "dark",
        diagnostics,
        readOnly,
        activeStep,
        onUserScroll,
      })
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
  });
}

describe("SequencerYamlCodeEditor diagnostics", () => {
  it("underlines diagnostic lines in edit and read-only modes", async () => {
    await render([diag]);
    expect(container.querySelectorAll(".cm-lintRange-error").length).toBeGreaterThan(0);
    await render([diag], true);
    expect(container.querySelectorAll(".cm-lintRange-error").length).toBeGreaterThan(0);
  });

  it("underlines warnings as warnings", async () => {
    await render([{ ...diag, severity: "warning" }]);
    expect(container.querySelectorAll(".cm-lintRange-warning").length).toBeGreaterThan(0);
    expect(container.querySelectorAll(".cm-lintRange-error").length).toBe(0);
  });

  it("does not draw stale diagnostics", async () => {
    await render([{ ...diag, stale: true }]);
    expect(container.querySelectorAll(".cm-lintRange-error").length).toBe(0);
  });
});

describe("SequencerYamlCodeEditor active step", () => {
  const range: ActiveStepRange = {
    highlight: { from: 3, to: 3 },
    bars: [{ from: 2, to: 3 }],
  };

  it("highlights the running lines and marks the gutter", async () => {
    await render([], false, range);
    expect(container.querySelectorAll(".cm-ec-active-line").length).toBe(1);
    expect(container.textContent).toContain("▶");
    expect(container.querySelectorAll(".cm-ec-active-bar").length).toBe(2);
  });

  it("clears the highlight when the range goes away", async () => {
    await render([], false, range);
    await render([], false, null);
    expect(container.querySelectorAll(".cm-ec-active-line").length).toBe(0);
    expect(container.textContent).not.toContain("▶");
  });

  it("coexists with lint marks", async () => {
    await render([diag], false, range);
    expect(container.querySelectorAll(".cm-ec-active-line").length).toBe(1);
    expect(container.querySelectorAll(".cm-lintRange-error").length).toBeGreaterThan(0);
  });

  it("reports wheel scrolling as the user's", async () => {
    let calls = 0;
    await render([], false, range, () => {
      calls += 1;
    });
    const scroller = container.querySelector(".cm-scroller")!;
    scroller.dispatchEvent(new Event("wheel"));
    expect(calls).toBe(1);
  });

  it("does not report pointer or cursor-key use inside the content", async () => {
    let calls = 0;
    await render([], false, range, () => {
      calls += 1;
    });
    const content = container.querySelector(".cm-content")!;
    content.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    content.dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true }));
    expect(calls).toBe(0);
    content.dispatchEvent(new KeyboardEvent("keydown", { key: "PageDown", bubbles: true }));
    expect(calls).toBe(1);
  });
});
