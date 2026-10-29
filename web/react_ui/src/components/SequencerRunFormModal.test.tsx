// @vitest-environment jsdom

import { MantineProvider } from "@mantine/core";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { parseRunForm } from "../features/sequencer/run_form";
import { SequencerRunFormModal } from "./SequencerRunFormModal";

// Mantine's Select takes ~30 s to render under jsdom (its floating combobox);
// a native select keeps the test fast and still shows the chosen label.
vi.mock("@mantine/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@mantine/core")>();
  const { createElement: h } = await import("react");
  type Item = { value: string; label: string };
  type Group = { group: string; items: Item[] };
  return {
    ...actual,
    Select: (props: {
      label?: string;
      data: (Item | Group)[];
      value: string | null;
      onChange: (value: string | null) => void;
    }) => {
      const items = props.data.flatMap((item) => ("items" in item ? item.items : [item]));
      return h(
        "label",
        null,
        props.label,
        h(
          "select",
          { value: props.value ?? "", onChange: (e: { target: { value: string } }) => props.onChange(e.target.value) },
          items.map((item) => h("option", { key: item.value, value: item.value }, item.label))
        )
      );
    },
  };
});

vi.mock("../api", () => ({
  callDevice: vi.fn(async () => ({ ok: true, result: { profiles: [{ name: "yag_16hz" }] } })),
  fetchCapabilities: vi.fn(async () => []),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const TARGET =
  "{id: rc, device_label: RC SynthHD, param_label: CH1 frequency, kind: synthhd_frequency, " +
  "device: syn, channel: 1, lock: pll, settle_s: 2, unit: Hz, display_unit: GHz, " +
  "display_scale: 1.0e+9, offset_unit: MHz, offset_scale: 1.0e+6, min: null, max: null, " +
  "default_span: 2.0e+6, current: {device: syn, signal: ch1_frequency_hz}}";

const YAML = `
version: 1
vars:
  targets: [${TARGET}]
  target: ${TARGET}
  relative: true
  lo: -1.0e+6
  hi: 1.0e+6
  points: 9
  passes: 3
  bursts: 3
  shots: 13
  neon: true
  pb_profile: ""
form:
  title: Quick parameter scan
  presets: [{label: Laser only, set: {neon: false}}]
  summary: {points: points, passes: passes, bursts: bursts, shots: shots}
  sections:
    - title: Scan target
      fields:
        - {type: target, var: target, catalog: targets}
        - {type: range, target: target, relative: relative, from: lo, to: hi, points: points}
    - title: Repetition
      fields:
        - {type: integer, var: passes, label: Passes, min: 1}
    - title: Setup
      fields:
        - {type: checkbox, var: neon, label: Neon flow}
        - type: select
          var: pb_profile
          label: Required profile
          empty_label: Any running profile
          options_call: {device: pulseblaster, action: list_profiles, list: profiles, value: name}
steps:
  - sleep: 0
`;

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

async function render(
  onStart: (overrides: Record<string, unknown>) => Promise<unknown> | void,
  onClose: () => void = () => undefined
) {
  await act(async () => {
    root.render(
      createElement(
        MantineProvider,
        null,
        createElement(SequencerRunFormModal, {
          opened: true,
          onClose,
          parsed: parseRunForm(YAML)!,
          latestSignalsByDevice: { syn: { ch1_frequency_hz: { value: 13.3345e9 } } },
          busy: false,
          onStart,
        })
      )
    );
  });
}

function button(label: string): HTMLButtonElement {
  const found = [...document.querySelectorAll("button")].find(
    (el) => el.textContent?.trim() === label
  );
  if (!found) {
    throw new Error(`no button ${label}`);
  }
  return found as HTMLButtonElement;
}

describe("SequencerRunFormModal", () => {
  it("shows the live value, the range and the shot count", async () => {
    await render(() => undefined);
    const text = document.body.textContent ?? "";
    expect(text).toContain("13.3345 GHz");
    expect(text).toContain("13.3335 → 13.3355 GHz");
    expect(text).toContain((9 * 3 * 3 * 13).toLocaleString());
  });

  it("starts with every form var, presets applied", async () => {
    const onStart = vi.fn();
    await render(onStart);
    await act(async () => button("Laser only").click());
    await act(async () => button("Start scan").click());
    expect(onStart).toHaveBeenCalledTimes(1);
    const overrides = onStart.mock.calls[0][0];
    expect(overrides).toMatchObject({
      relative: true,
      lo: -1.0e6,
      hi: 1.0e6,
      points: 9,
      passes: 3,
      neon: false,
      pb_profile: "",
    });
    expect((overrides.target as { id: string }).id).toBe("rc");
  });

  it("closes only when the start was accepted", async () => {
    const onClose = vi.fn();
    await render(async () => false, onClose);
    await act(async () => button("Start scan").click());
    expect(onClose).not.toHaveBeenCalled();
    await render(async () => true, onClose);
    await act(async () => button("Start scan").click());
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
