// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { SequencerYamlCodeEditor } from "./SequencerYamlCodeEditor";
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

async function render(diagnostics: SequencerDiagnostic[], readOnly = false) {
  await act(async () => {
    root.render(
      createElement(SequencerYamlCodeEditor, {
        value: YAML,
        onChange: () => undefined,
        colorScheme: "dark",
        diagnostics,
        readOnly,
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
