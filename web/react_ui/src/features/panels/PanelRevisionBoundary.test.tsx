// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  PanelRevisionBoundary,
  panelInvalidationStore,
  usePanelRevision,
} from "./PanelInvalidationStore";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Bump the panel's revision synchronously (the store normally flushes on
// rAF). Each call is a second later, past any frame-rate ceiling.
let clockMs = 1_000_000;
function publish(panelId: string) {
  clockMs += 1000;
  act(() => {
    panelInvalidationStore.markPanelDirty(panelId);
    (panelInvalidationStore as unknown as { flush(now: number): void }).flush(clockMs);
  });
}

describe("PanelRevisionBoundary", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    panelInvalidationStore.setHidden(false);
    container = document.createElement("div");
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    panelInvalidationStore.removePanel("P");
  });

  function renderCard(followData: boolean) {
    const counts = { card: 0, body: 0 };
    function Card() {
      counts.card += 1;
      usePanelRevision("P", followData);
      return createElement(PanelRevisionBoundary, {
        panelId: "P",
        children: (revision: number) => {
          counts.body += 1;
          return createElement("span", null, String(revision));
        },
      });
    }
    act(() => root.render(createElement(Card)));
    return counts;
  }

  it("re-renders only the body on a data update", () => {
    const counts = renderCard(false);
    publish("P");
    publish("P");
    expect(counts).toEqual({ card: 1, body: 3 });
    expect(container.textContent).toBe(String(panelInvalidationStore.getRevision("P")));
  });

  it("lets the card follow data when enabled", () => {
    const counts = renderCard(true);
    publish("P");
    expect(counts.card).toBe(2);
  });
});
