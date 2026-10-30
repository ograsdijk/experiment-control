// @vitest-environment jsdom

import { MantineProvider } from "@mantine/core";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { parseRunForm } from "../features/sequencer/run_form";
import { SequencerRunFormModal } from "./SequencerRunFormModal";

vi.mock("../api", () => ({
  callDevice: vi.fn(async () => ({ ok: true, result: { profiles: [{ name: "profile_a" }] } })),
  fetchCapabilities: vi.fn(async () => []),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const TARGET =
  "{id: src, device_label: Source, param_label: CH1 frequency, kind: source_frequency, " +
  "device: source, channel: 1, lock_label: settled, move_label: ramps in 1 MHz steps, " +
  "unit: Hz, display_unit: GHz, " +
  "display_scale: 1.0e+9, offset_unit: MHz, offset_scale: 1.0e+6, min: null, max: null, " +
  "default_span: 2.0e+6, current: {device: source, signal: ch1_frequency_hz}}";

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
  heater: true
  profile: ""
form:
  title: Quick parameter scan
  presets: [{label: Cold, set: {heater: false}}]
  summary:
    - {label: Values, product: [points]}
    - {label: Total shots, product: [points, passes, bursts, shots]}
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
        - {type: checkbox, var: heater, label: Heater}
        - type: select
          var: profile
          label: Required profile
          empty_label: Any profile
          options_call: {device: timer, action: list_profiles, list: profiles, value: name}
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
          latestSignalsByDevice: { source: { ch1_frequency_hz: { value: 13.3345e9 } } },
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
    expect(text).toContain("waits for settled");
    expect(text).toContain("Moves: ramps in 1 MHz steps");
  });

  it("starts with every form var, presets applied", async () => {
    const onStart = vi.fn();
    await render(onStart);
    await act(async () => button("Cold").click());
    await act(async () => button("Start scan").click());
    expect(onStart).toHaveBeenCalledTimes(1);
    const overrides = onStart.mock.calls[0][0];
    expect(overrides).toMatchObject({
      relative: true,
      lo: -1.0e6,
      hi: 1.0e6,
      points: 9,
      passes: 3,
      heater: false,
      profile: "",
    });
    expect((overrides.target as { id: string }).id).toBe("src");
  });

  it("lets the target set the park channel and keeps it among the chips", async () => {
    const parsed = parseRunForm(`
version: 1
vars:
  targets:
    - {id: a, device_label: Laser, param_label: A, kind: k, device: d, wm: 5, current: null}
    - {id: b, device_label: Laser, param_label: B, kind: k, device: d, wm: null, current: null}
  target: {id: a, device_label: Laser, param_label: A, kind: k, device: d, wm: 5, current: null}
  cycle: [1]
  park: 16
  channels: [{value: 1, label: One}, {value: 5, label: Five}, {value: 16, label: Sixteen}]
form:
  sections:
    - wide: true
      fields:
        - {type: target, var: target, catalog: targets, sets: {park: wm}}
    - fields:
        - {type: chips, var: cycle, options: channels, includes: park}
    - fields:
        - {type: select, var: park, options: channels, label: Park}
steps:
  - sleep: 0
`)!;
    const onStart = vi.fn();
    await act(async () => {
      root.render(
        createElement(
          MantineProvider,
          { env: "test" },
          createElement(SequencerRunFormModal, {
            opened: true,
            onClose: () => undefined,
            parsed,
            latestSignalsByDevice: {},
            busy: false,
            onStart,
          })
        )
      );
    });
    const text = document.body.textContent ?? "";
    expect(text).toContain("Set by the scan target");
    // Chip values are the options' JSON.
    const five = document.querySelector<HTMLInputElement>('input[type=checkbox][value="5"]');
    expect(five?.checked).toBe(true);
    expect(five?.disabled).toBe(true);
    await act(async () => button("Start scan").click());
    expect(onStart.mock.calls[0][0]).toMatchObject({ park: 5, cycle: [1, 5] });
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
