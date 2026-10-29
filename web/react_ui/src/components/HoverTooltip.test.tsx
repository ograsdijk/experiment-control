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

  it("keeps the target's DOM node while opening and closing", () => {
    // Remounting the target on open let the browser send the mouseout to the
    // detached node, so onMouseLeave never fired and the tooltip stuck.
    const onMouseEnter = vi.fn();
    const onMouseLeave = vi.fn();
    const childRef = vi.fn();
    act(() =>
      root.render(
        createElement(
          MantineProvider,
          null,
          createElement(HoverTooltip, {
            label: "Restart driver",
            children: createElement(
              "button",
              { onMouseEnter, onMouseLeave, ref: childRef },
              "R"
            ),
          })
        )
      )
    );
    const original = container.querySelector("button")!;
    expect(childRef).toHaveBeenLastCalledWith(original);

    act(() => {
      original.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    });
    expect(document.body.textContent).toContain("Restart driver");
    expect(container.querySelector("button")).toBe(original);

    act(() => {
      original.dispatchEvent(
        new MouseEvent("mouseout", { bubbles: true, relatedTarget: document.body })
      );
    });
    expect(document.body.textContent).not.toContain("Restart driver");
    expect(container.querySelector("button")).toBe(original);
    expect(onMouseEnter).toHaveBeenCalledOnce();
    expect(onMouseLeave).toHaveBeenCalledOnce();
  });

  it("describes the target by the open tooltip and restores its own aria", () => {
    act(() =>
      root.render(
        createElement(
          MantineProvider,
          null,
          createElement(HoverTooltip, {
            label: "Send command",
            children: createElement("button", { "aria-describedby": "hint" }, "S"),
          })
        )
      )
    );
    const button = container.querySelector("button")!;
    act(() => {
      button.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    });
    const ids = button.getAttribute("aria-describedby")!.split(" ");
    expect(ids[0]).toBe("hint");
    expect(document.getElementById(ids[1])?.textContent).toContain("Send command");

    act(() => {
      button.dispatchEvent(
        new MouseEvent("mouseout", { bubbles: true, relatedTarget: document.body })
      );
    });
    expect(button.getAttribute("aria-describedby")).toBe("hint");
  });

  it("forwards Tooltip-level handlers to the target alongside the child's", () => {
    const tooltipClick = vi.fn();
    const childClick = vi.fn();
    act(() =>
      root.render(
        createElement(
          MantineProvider,
          null,
          createElement(HoverTooltip, {
            label: "Restart driver",
            onClick: tooltipClick,
            children: createElement("button", { onClick: childClick }, "R"),
          })
        )
      )
    );
    act(() => container.querySelector("button")!.click());
    expect(tooltipClick).toHaveBeenCalledOnce();
    expect(childClick).toHaveBeenCalledOnce();
  });

  it("closes on a mouseover elsewhere when the target sent no mouseout", () => {
    // Older browsers send no mouse events from a disabled button.
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
              children: createElement("button", null, createElement("svg")),
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
    act(() => {
      container.querySelector("svg")!.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    });
    expect(document.body.textContent).toContain("Restart driver");

    act(() => {
      container
        .querySelector("#elsewhere")!
        .dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    });
    expect(document.body.textContent).not.toContain("Restart driver");
  });

  it("closes when the pointer leaves the window", () => {
    act(() =>
      root.render(
        createElement(
          MantineProvider,
          null,
          createElement(HoverTooltip, {
            label: "Restart driver",
            children: createElement("button", null, "R"),
          })
        )
      )
    );
    const button = container.querySelector("button")!;
    act(() => {
      button.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    });
    expect(document.body.textContent).toContain("Restart driver");

    act(() => {
      button.dispatchEvent(new MouseEvent("mouseout", { bubbles: true, relatedTarget: null }));
    });
    expect(document.body.textContent).not.toContain("Restart driver");
  });
});
