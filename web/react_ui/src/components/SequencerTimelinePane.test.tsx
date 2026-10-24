// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MantineProvider } from "@mantine/core";
import { buildSequencerStepOutline } from "../features/sequencer/outline";
import { normalizeSequencerLoops, normalizeSequencerPlan } from "../features/sequencer/plan";
import { formatSeconds, SequencerTimelinePane } from "./SequencerTimelinePane";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const YAML = `version: 1
steps:
  - call: {device: fs740, action: flush}
  - for:
      bind: {value: f}
      in: {gen: {linspace: {start: 0, stop: 1, num: 4}}}
      do:
        - call: {device: synth, action: set}
        - sleep: 0.5
`;

function planEntry(path: string, line: number, kind: string, count: number, extra: Record<string, unknown> = {}) {
  return {
    path,
    line,
    use_id: null,
    kind,
    summary: kind,
    disabled: false,
    count,
    certainty: "exact",
    note: null,
    runs_done: 0,
    mean_s: null,
    ...extra,
  };
}

const PLAN = normalizeSequencerPlan({
  live: false,
  total_runs: 10,
  certainty: "exact",
  sleep_s: 2,
  steps: [
    planEntry("steps[0]", 3, "call", 1),
    planEntry("steps[1]", 4, "for", 1, { iterations: 4 }),
    planEntry("steps[1].for.do[0]", 8, "call", 4),
    planEntry("steps[1].for.do[1]", 9, "sleep", 4, { seconds: 0.5 }),
  ],
});

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  if (!window.matchMedia) {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  }
  if (!("ResizeObserver" in window)) {
    // jsdom has none; Mantine's ScrollArea and the pane's width check use it.
    (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(props: Partial<Parameters<typeof SequencerTimelinePane>[0]> = {}) {
  const onOpenStep = vi.fn();
  act(() => {
    root.render(
      createElement(
        MantineProvider,
        null,
        createElement(SequencerTimelinePane, {
          outline: buildSequencerStepOutline(YAML),
          plan: PLAN,
          planError: null,
          loops: [],
          runtimeState: "IDLE",
          activeStep: null,
          textNotLoaded: false,
          onOpenStep,
          ...props,
        })
      )
    );
  });
  return onOpenStep;
}

describe("SequencerTimelinePane", () => {
  it("draws a band for the loop over its steps with plan counts", () => {
    const onOpenStep = render();
    const band = container.querySelector(".seq-tl-band");
    expect(band?.textContent).toContain("for");
    expect(band?.textContent).toContain("×4");
    expect(band?.querySelectorAll(".seq-tl-cell")).toHaveLength(4);
    const tiles = [...container.querySelectorAll(".seq-tl-tile")];
    expect(tiles.map((tile) => tile.querySelector(".seq-tl-count")?.textContent)).toEqual([
      "×1",
      "×4",
      "×4",
    ]);
    expect(tiles[2].querySelector(".seq-tl-dur")?.textContent).toBe("500 ms");
    expect(container.textContent).toContain("step runs");

    act(() => (tiles[1] as HTMLButtonElement).click());
    expect(onOpenStep).toHaveBeenCalledWith(8);
  });

  it("shows the run's position in the loop while running", () => {
    const livePlan = normalizeSequencerPlan({
      ...PLAN,
      live: true,
      steps: PLAN!.steps.map((step) => ({
        path: step.path,
        line: step.line,
        kind: step.kind,
        count: step.count,
        iterations: step.iterations,
        runs_done: step.kind === "for" ? 1 : 2,
        mean_s: 0.25,
      })),
    });
    render({
      plan: livePlan,
      runtimeState: "RUNNING",
      loops: normalizeSequencerLoops([
        { kind: "for", line: 4, iteration: 3, count: 4, vars: ["f"], values: [{ f: 0 }, { f: 0.333 }, { f: 0.667 }, { f: 1 }] },
      ]),
    });
    const band = container.querySelector(".seq-tl-band");
    expect(band?.textContent).toContain("3/4");
    expect(band?.querySelectorAll(".seq-tl-cell.done")).toHaveLength(2);
    expect(band?.querySelectorAll(".seq-tl-cell.cur")).toHaveLength(1);
    expect(container.textContent).toContain("f 3 of 4");
    expect(container.textContent).toContain("f 0.667");
    expect(container.querySelectorAll(".seq-tl-pass.done").length).toBeGreaterThan(0);
  });

  it("keeps a finished loop in the overview after its steps fold", () => {
    const livePlan = normalizeSequencerPlan({
      ...PLAN,
      live: true,
      steps: PLAN!.steps.map((step) => ({
        path: step.path,
        line: step.line,
        kind: step.kind,
        count: step.count,
        iterations: step.iterations,
        runs_done: step.count,
        mean_s: 0.25,
      })),
    });
    render({ plan: livePlan, runtimeState: "RUNNING" });
    expect(container.querySelector(".seq-tl-collapsed")).not.toBeNull();
    const loopSegment = container.querySelector(".seq-tl-seg-loop");
    expect(loopSegment).not.toBeNull();
    expect(loopSegment?.querySelectorAll(".seq-tl-pass.done")).toHaveLength(4);
  });

  it("says when the counts ignore invalid run overrides", () => {
    render({ overridesInvalid: true });
    expect(container.textContent).toContain("counts use the last valid overrides");
  });

  it("says when the editor is not the loaded sequence", () => {
    render({ textNotLoaded: true });
    expect(container.querySelector(".seq-tl-tile")).toBeNull();
    expect(container.textContent).toContain("shows the loaded sequence");
  });

  it("formats seconds", () => {
    expect(formatSeconds(0.041)).toBe("41 ms");
    expect(formatSeconds(2.5)).toBe("2.5 s");
    expect(formatSeconds(65)).toBe("1:05");
  });
});
