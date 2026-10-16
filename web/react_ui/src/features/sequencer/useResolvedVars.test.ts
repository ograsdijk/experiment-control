// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiResponse } from "../../api";
import type { ResolvedVars } from "./resolved_values";
import { RESOLVE_VARS_DEBOUNCE_MS, staleReason, useResolvedVars } from "./useResolvedVars";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
let latest: ResolvedVars | null = null;
/** Every value the hook returned, render by render. */
let history: Array<ResolvedVars | null> = [];

type Call = (id: string, action: string, params: Record<string, unknown>) => Promise<ApiResponse<unknown>>;

function Probe(props: { call: Call; enabled: boolean; text: string; processId?: string }) {
  latest = useResolvedVars({
    callProcessFn: props.call,
    processId: props.processId ?? "sequencer",
    enabled: props.enabled,
    yamlText: props.text,
    overrides: {},
  });
  history.push(latest);
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
  history = [];
});

afterEach(() => {
  act(() => root.unmount());
  vi.useRealTimers();
});

describe("useResolvedVars", () => {
  it("sends the vars block and the expressions as the runtime sees them", async () => {
    const call = vi.fn<Call>().mockResolvedValue({
      ok: true,
      result: { vars: { a: 1 }, expressions: { '${a == "x"}': { ok: true, value: false } } },
    } as ApiResponse<unknown>);
    const text = 'vars: {a: 1}\nsteps:\n  - sleep: ${a}\n  - set: {device: d, name: n, value: "${a == \\"x\\"}"}\n';
    await render({ call, enabled: true, text });
    expect(call).toHaveBeenCalledWith("sequencer", "sequencer.resolve_vars", {
      vars_override: {},
      expressions: ["${a}", '${a == "x"}'],
      text: "vars: {a: 1}\n",
    });
    // The result is found under the text as written in the editor too.
    expect(latest?.expressions['${a == \\"x\\"}']).toEqual({ ok: true, value: false });
    expect(latest?.vars).toEqual({ a: 1 });
    expect(latest?.staleReason).toBeFalsy();
  });

  it("keeps the last values marked stale while the YAML doesn't parse", async () => {
    const call = vi.fn<Call>().mockResolvedValueOnce(OK).mockResolvedValueOnce(INVALID);
    await render({ call, enabled: true, text: "vars: {a: 1}" });
    await render({ call, enabled: true, text: "vars: {a: [" });
    expect(latest?.vars).toEqual({ a: 1 });
    expect(latest?.staleReason).toBe("the YAML doesn't parse right now");
  });

  it("says why values are stale", () => {
    expect(staleReason(INVALID)).toBe("the YAML doesn't parse right now");
    expect(
      staleReason({ ok: false, error: { code: "not_implemented", message: "unknown action" } } as ApiResponse<unknown>)
    ).toBe("the sequencer couldn't resolve them (unknown action)");
    expect(staleReason(null)).toBe("the sequencer can't be reached");
  });

  it("drops a reply that arrives after the modal closed", async () => {
    let resolveCall: (resp: ApiResponse<unknown>) => void = () => undefined;
    const call = vi.fn<Call>().mockImplementation(
      () => new Promise<ApiResponse<unknown>>((resolve) => (resolveCall = resolve))
    );
    await render({ call, enabled: true, text: "vars: {a: 1}" });
    await render({ call, enabled: false, text: "vars: {a: 1}" });
    await act(async () => {
      resolveCall(OK);
    });
    expect(latest).toBeNull();
    // Reopened: nothing from the old request shows, not even for one render.
    history = [];
    call.mockResolvedValue(INVALID);
    await render({ call, enabled: true, text: "vars: {a: 1}" });
    expect(history.length).toBeGreaterThan(0);
    expect(history.every((value) => value === null)).toBe(true);
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
