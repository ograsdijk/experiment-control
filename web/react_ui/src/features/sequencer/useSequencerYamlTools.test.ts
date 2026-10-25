// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SequencerYamlTools } from "./useSequencerYamlTools";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type HookModule = typeof import("./useSequencerYamlTools");

let container: HTMLDivElement;
let root: Root;
let latest: SequencerYamlTools | null = null;
let hookModule: HookModule;

function Probe(props: { wanted: boolean }) {
  latest = hookModule.useSequencerYamlTools(props.wanted);
  return null;
}

async function render(wanted: boolean) {
  await act(async () => {
    root.render(createElement(Probe, { wanted }));
  });
  // Let the dynamic import settle.
  await act(async () => {
    await hookModule.loadSequencerYamlTools().catch(() => undefined);
  });
}

beforeEach(async () => {
  // A fresh module per test: the loaded helpers are cached module-wide.
  vi.resetModules();
  hookModule = await import("./useSequencerYamlTools");
  container = document.createElement("div");
  root = createRoot(container);
  latest = null;
});

afterEach(() => {
  act(() => root.unmount());
});

describe("useSequencerYamlTools", () => {
  it("does not load the helpers until they are wanted", async () => {
    await act(async () => {
      root.render(createElement(Probe, { wanted: false }));
    });
    expect(latest).toBeNull();
  });

  it("loads the helpers when wanted and keeps them afterwards", async () => {
    await render(true);
    expect(latest?.buildLocalConditionDiagnostics("")).toEqual([]);
    await render(false);
    expect(latest).not.toBeNull();
  });

  it("starts with the helpers once another caller has loaded them", async () => {
    await hookModule.loadSequencerYamlTools();
    await act(async () => {
      root.render(createElement(Probe, { wanted: false }));
    });
    expect(latest).not.toBeNull();
  });
});
