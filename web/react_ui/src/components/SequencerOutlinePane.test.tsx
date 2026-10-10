// @vitest-environment jsdom

import { MantineProvider } from "@mantine/core";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { resolveActiveStep } from "../features/sequencer/active_step";
import { buildSequencerStepOutline } from "../features/sequencer/outline";
import { SequencerOutlinePane } from "./SequencerOutlinePane";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const YAML = [
  "version: 1", // 1
  "steps:", // 2
  "  - repeat:", // 3
  "      times: 2", // 4
  "      do:", // 5
  "        - sleep: 1", // 6
  "",
].join("\n");

const activeStep = resolveActiveStep(buildSequencerStepOutline(YAML), 6);

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (window as unknown as { matchMedia: unknown }).matchMedia ??= () => ({
    matches: false,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
  });
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(follow: boolean, withActive = true) {
  await act(async () => {
    root.render(
      createElement(
        MantineProvider,
        null,
        createElement(SequencerOutlinePane, {
          yamlText: YAML,
          onYamlTextChange: () => undefined,
          streamCatalog: [],
          capabilitiesByDevice: {},
          streamWorkspaces: {},
          latestSignalsByDevice: {},
          colorScheme: "light",
          activeStep: withActive ? activeStep : null,
          follow,
        })
      )
    );
  });
}

const rows = () => container.querySelectorAll('[aria-label="Collapse step"], [aria-label="Expand step"]');

describe("SequencerOutlinePane active step", () => {
  it("keeps a collapsed parent collapsed without Follow, but tints it", async () => {
    await render(false, false);
    await act(async () => {
      (container.querySelector('[aria-label="Collapse step"]') as HTMLElement).click();
    });
    expect(container.querySelector('[data-active-step="true"]')).toBeNull();
    await render(false);
    expect(rows()[0].getAttribute("aria-label")).toBe("Expand step");
    expect(container.querySelector('[data-active-step="true"]')).toBeNull();
    expect(container.textContent).toContain("running inside");
  });

  it("expands the collapsed parent when Follow is on", async () => {
    await render(false, false);
    await act(async () => {
      (container.querySelector('[aria-label="Collapse step"]') as HTMLElement).click();
    });
    await render(true);
    expect(rows()[0].getAttribute("aria-label")).toBe("Collapse step");
    expect(container.querySelector('[data-active-step="true"]')).not.toBeNull();
  });
});
