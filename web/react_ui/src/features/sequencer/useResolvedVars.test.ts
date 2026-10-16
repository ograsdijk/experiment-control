// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiResponse } from "../../api";
import type { ResolvedVars } from "./resolved_values";
import { RESOLVE_VARS_DEBOUNCE_MS, useResolvedVars } from "./useResolvedVars";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
let latest: ResolvedVars | null = null;

type Call = (id: string, action: string, params: Record<string, unknown>) => Promise<ApiResponse<unknown>>;

function Probe(props: { call: Call; enabled: boolean; text: string; processId?: string }) {
  latest = useResolvedVars({
    callProcessFn: props.call,
    processId: props.processId ?? "sequencer",
    enabled: props.enabled,
    yamlText: props.text,
    overrides: {},
  });
  return null;
}

const OK: ApiResponse<unknown> = {
  ok: true,
  result: { vars: { a: 1 }, derived: [], overridden: [], errors: {}, expressions: {} },
} as ApiResponse<unknown>;
const INVALID: ApiResponse<unknown> = {
  ok: false,
  error: { code: "invalid_yaml", message: "bad" },
} as ApiResponse<unknown>;

async function render(props: Parameters<typeof Probe>[0]) {
  await act(async () => {
    root.render(createElement(Probe, props));
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(RESOLVE_VARS_DEBOUNCE_MS + 1);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  container = document.createElement("div");
  root = createRoot(container);
  latest = null;
});

afterEach(() => {
  act(() => root.unmount());
  vi.useRealTimers();
});

describe("useResolvedVars", () => {
  it("sends the editor text and its expressions", async () => {
    const call = vi.fn<Call>().mockResolvedValue(OK);
    await render({ call, enabled: true, text: "vars: {a: 1}\nsteps:\n  - sleep: ${a}\n" });
    expect(call).toHaveBeenCalledWith("sequencer", "sequencer.resolve_vars", {
      vars_override: {},
      expressions: ["${a}"],
      text: "vars: {a: 1}\nsteps:\n  - sleep: ${a}\n",
    });
    expect(latest?.vars).toEqual({ a: 1 });
    expect(latest?.stale).toBeFalsy();
  });

  it("keeps the last values marked stale while the YAML doesn't parse", async () => {
    const call = vi.fn<Call>().mockResolvedValueOnce(OK).mockResolvedValueOnce(INVALID);
    await render({ call, enabled: true, text: "vars: {a: 1}" });
    await render({ call, enabled: true, text: "vars: {a: [" });
    expect(latest?.vars).toEqual({ a: 1 });
    expect(latest?.stale).toBe(true);
  });

  it("drops the values when the modal closes", async () => {
    const call = vi.fn<Call>().mockResolvedValue(OK);
    await render({ call, enabled: true, text: "vars: {a: 1}" });
    await render({ call, enabled: false, text: "vars: {a: 1}" });
    expect(latest).toBeNull();
    // Reopened with YAML that doesn't parse: nothing old comes back.
    call.mockResolvedValue(INVALID);
    await render({ call, enabled: true, text: "vars: {b: [" });
    expect(latest).toBeNull();
  });
});
