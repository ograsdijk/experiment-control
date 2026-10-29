// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ParsedSequencerOutline } from "./outline_parse";
import { useSequencerOutline } from "./useSequencerOutline";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ONE_STEP = "version: 1\nsteps:\n  - sleep: 1\n";
const TWO_STEPS = "version: 1\nsteps:\n  - sleep: 1\n  - sleep: 2\n";

let container: HTMLDivElement;
let root: Root;
let latest: [ParsedSequencerOutline, (text: string) => void] | null = null;

function Probe({ text }: { text: string }) {
  latest = useSequencerOutline(text);
  return null;
}

beforeEach(() => {
  container = document.createElement("div");
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
});

describe("useSequencerOutline", () => {
  it("parses an edit's text at once with parseNow", async () => {
    await act(async () => root.render(createElement(Probe, { text: ONE_STEP })));
    // The parse runs in the page here (no Worker in jsdom): wait for it.
    for (let tries = 0; tries < 100 && latest?.[0].text !== ONE_STEP; tries += 1) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 20));
      });
    }
    expect(latest?.[0].outline).toHaveLength(1);

    act(() => {
      latest?.[1](TWO_STEPS);
      root.render(createElement(Probe, { text: TWO_STEPS }));
    });
    // In the same update, without waiting for the worker.
    expect(latest?.[0].text).toBe(TWO_STEPS);
    expect(latest?.[0].outline).toHaveLength(2);
  });
});
