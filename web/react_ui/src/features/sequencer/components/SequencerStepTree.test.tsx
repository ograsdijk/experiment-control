// @vitest-environment jsdom

import { MantineProvider } from "@mantine/core";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildSequencerStepOutline } from "../outline";
import { SequencerStepTree } from "./SequencerStepTree";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const YAML = [
  "version: 1",
  "steps:",
  "  - repeat:",
  "      times: 2",
  "      do:",
  "        - sleep: 1",
  "  - sleep: 2",
  "",
].join("\n");

let container: HTMLDivElement;
let root: Root;
const originalObserver = globalThis.IntersectionObserver;

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
  globalThis.IntersectionObserver = originalObserver;
});

async function render() {
  const noop = () => undefined;
  await act(async () => {
    root.render(
      createElement(
        MantineProvider,
        null,
        createElement(SequencerStepTree, {
          outline: buildSequencerStepOutline(YAML),
          selectedId: null,
          onSelect: noop,
          collapsedById: {},
          onToggleCollapse: noop,
          onDuplicate: noop,
          onDelete: noop,
          onToggleEnabled: noop,
          onInsertBelow: noop,
          onInsertChild: noop,
          siblingInfoById: {},
          onMoveUp: noop,
          onMoveDown: noop,
          onInsertTopLevel: noop,
        })
      )
    );
  });
}

const buttons = (label: string) => container.querySelectorAll(`[aria-label="${label}"]`);

describe("SequencerStepTree row buttons", () => {
  // Opening a Mantine menu does not finish under jsdom here (the stock
  // Quick add menu neither), so this checks the menus are not built up
  // front; opening one on the first click is checked in a browser.
  it("builds no row menu before its button is clicked", async () => {
    globalThis.IntersectionObserver = undefined as unknown as typeof IntersectionObserver;
    await render();
    expect(buttons("Move step up")).toHaveLength(3);
    const insertBelow = [...buttons("Insert step below")];
    expect(insertBelow).toHaveLength(3);
    // A built menu marks its button with aria-expanded; the rows' are plain.
    expect(insertBelow.every((button) => button.getAttribute("aria-haspopup") === "menu")).toBe(true);
    expect(insertBelow.some((button) => button.hasAttribute("aria-expanded"))).toBe(false);
    expect(buttons("Insert child step")).toHaveLength(1);
    expect(buttons("Insert child step")[0].hasAttribute("aria-expanded")).toBe(false);
  });

  it("builds a row's buttons only while it is near the list's view", async () => {
    const callbacks = new Map<Element, IntersectionObserverCallback>();
    const roots: Array<Element | Document | null | undefined> = [];
    globalThis.IntersectionObserver = vi.fn(function (
      this: IntersectionObserver,
      callback: IntersectionObserverCallback,
      options?: IntersectionObserverInit
    ) {
      roots.push(options?.root);
      return {
        observe: (element: Element) => callbacks.set(element, callback),
        disconnect: () => undefined,
      } as unknown as IntersectionObserver;
    }) as unknown as typeof IntersectionObserver;
    await render();

    // Not reported near yet: every row holds its place without buttons.
    expect(callbacks.size).toBe(3);
    expect(buttons("Move step up")).toHaveLength(0);
    const viewport = container.querySelector(".mantine-ScrollArea-viewport");
    expect(viewport).not.toBeNull();
    expect(roots.every((observed) => observed === viewport)).toBe(true);

    const [first] = callbacks.keys();
    const report = (isIntersecting: boolean) =>
      act(async () => {
        callbacks.get(first)?.(
          [{ isIntersecting, target: first } as unknown as IntersectionObserverEntry],
          {} as IntersectionObserver
        );
      });
    await report(true);
    expect(buttons("Move step up")).toHaveLength(1);
    await report(false);
    expect(buttons("Move step up")).toHaveLength(0);
  });
});
