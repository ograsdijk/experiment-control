// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MantineProvider } from "@mantine/core";
import { SeqTextInput } from "./SeqTextInput";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  // jsdom has no matchMedia; Mantine's color-scheme hook needs it.
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
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(value: string, onChange: (value: string) => void) {
  act(() => {
    root.render(
      createElement(
        MantineProvider,
        null,
        createElement(SeqTextInput, { value, onChange, "aria-label": "field" })
      )
    );
  });
  return container.querySelector("input") as HTMLInputElement;
}

function type(input: HTMLInputElement, text: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function key(input: HTMLInputElement, name: string) {
  act(() => {
    input.dispatchEvent(new KeyboardEvent("keydown", { key: name, bubbles: true }));
  });
}

describe("SeqTextInput", () => {
  it("shows a quoted value bare and writes nothing while typing", () => {
    const onChange = vi.fn();
    const input = render('"${ch}"', onChange);
    expect(input.value).toBe("${ch}");
    act(() => input.focus());
    type(input, "${ch");
    type(input, "${ch + 1}");
    expect(onChange).not.toHaveBeenCalled();
    act(() => input.blur());
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith("${ch + 1}");
  });

  it("commits on Enter and discards on Escape", () => {
    const onChange = vi.fn();
    const input = render("5.0", onChange);
    act(() => input.focus());
    type(input, "6.0");
    key(input, "Enter");
    expect(onChange).toHaveBeenCalledWith("6.0");

    onChange.mockClear();
    type(input, "-");
    key(input, "Escape");
    expect(input.value).toBe("5.0");
    act(() => input.blur());
    expect(onChange).not.toHaveBeenCalled();
  });

  it("does not write when the text is unchanged", () => {
    const onChange = vi.fn();
    const input = render('"abc"', onChange);
    act(() => input.focus());
    type(input, "abc");
    act(() => input.blur());
    expect(onChange).not.toHaveBeenCalled();
  });
});
