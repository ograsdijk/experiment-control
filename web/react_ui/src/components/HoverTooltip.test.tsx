// @vitest-environment jsdom

import { MantineProvider } from "@mantine/core";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HoverTooltip } from "./HoverTooltip";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("HoverTooltip", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    window.matchMedia ??= ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("mounts the tooltip only while hovered and keeps the child's handlers", () => {
    const onMouseEnter = vi.fn();
    const onClick = vi.fn();
    act(() =>
      root.render(
        createElement(
          MantineProvider,
          null,
          createElement(HoverTooltip, {
            label: "Restart driver",
            withArrow: true,
            children: createElement("button", { onMouseEnter, onClick }, "R"),
          })
        )
      )
    );
    expect(document.querySelectorAll("[data-portal]")).toHaveLength(0);
    expect(document.body.textContent).not.toContain("Restart driver");

    const button = () => container.querySelector("button")!;
    act(() => {
      button().dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    });
    expect(onMouseEnter).toHaveBeenCalledOnce();
    expect(document.body.textContent).toContain("Restart driver");

    act(() => button().click());
    expect(onClick).toHaveBeenCalledOnce();

    act(() => {
      button().dispatchEvent(
        new MouseEvent("mouseout", { bubbles: true, relatedTarget: document.body })
      );
    });
    expect(document.querySelectorAll("[data-portal]")).toHaveLength(0);
  });

  it("closes when the pointer is elsewhere even if the target never saw a mouseout", () => {
    act(() =>
      root.render(
        createElement(
          MantineProvider,
          null,
          createElement(
            "div",
            null,
            createElement(HoverTooltip, {
              label: "Restart driver",
              children: createElement("button", null, "R"),
            }),
            createElement("span", { id: "elsewhere" }, "x")
          )
        )
      )
    );
    act(() => {
      container
        .querySelector("button")!
        .dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    });
    expect(document.body.textContent).toContain("Restart driver");

    // Opening remounts the button; the browser's mouseout can go to the old,
    // detached node, so only the next element's mouseover is seen.
    act(() => {
      container
        .querySelector("button")!
        .dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    });
    expect(document.body.textContent).toContain("Restart driver");
    act(() => {
      container
        .querySelector("#elsewhere")!
        .dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    });
    expect(document.body.textContent).not.toContain("Restart driver");
  });
});
