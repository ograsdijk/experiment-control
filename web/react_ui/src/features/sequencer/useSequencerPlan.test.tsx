// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiResponse } from "../../api";
import { PLAN_DEBOUNCE_MS, useSequencerPlan, type SequencerPlanState } from "./useSequencerPlan";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type HookArgs = Parameters<typeof useSequencerPlan>[0];

const PLAN_RESULT = { live: false, certainty: "exact", steps: [{ path: "steps[0]", line: 3, kind: "call", count: 1 }] };

let container: HTMLDivElement;
let root: Root;
let latest: SequencerPlanState;

function Probe(props: HookArgs) {
  latest = useSequencerPlan(props);
  return null;
}

function args(callProcessFn: HookArgs["callProcessFn"], extra: Partial<HookArgs> = {}): HookArgs {
  return {
    callProcessFn,
    processId: "seq",
    enabled: true,
    loadedRevision: 1,
    runId: null,
    runtimeState: "IDLE",
    completedSteps: null,
    overrides: {},
    ...extra,
  };
}

async function render(props: HookArgs) {
  act(() => root.render(createElement(Probe, props)));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(PLAN_DEBOUNCE_MS);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  container = document.createElement("div");
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  vi.useRealTimers();
});

describe("useSequencerPlan", () => {
  it("shows a failed request for a newly loaded sequence", async () => {
    const call = vi
      .fn<HookArgs["callProcessFn"]>()
      .mockResolvedValueOnce({ ok: true, result: PLAN_RESULT } as ApiResponse<unknown>)
      .mockResolvedValueOnce({ ok: false, error: { code: "timeout", message: "timed out" } } as ApiResponse<unknown>);
    await render(args(call));
    expect(latest.plan).not.toBeNull();

    await render(args(call, { loadedRevision: 2 }));
    expect(latest.plan).toBeNull();
    expect(latest.error).toBe("timed out");
  });

  it("asks with the last valid overrides while the overrides have errors", async () => {
    const call = vi.fn<HookArgs["callProcessFn"]>().mockResolvedValue({ ok: true, result: PLAN_RESULT } as ApiResponse<unknown>);
    await render(args(call, { overrides: { n: 3 } }));
    expect(call).toHaveBeenLastCalledWith("seq", "sequencer.plan", { vars_override: { n: 3 } });

    await render(args(call, { overrides: { n: 3, bad: 1 }, overridesValid: false }));
    expect(call).toHaveBeenCalledTimes(1);
    expect(latest.overridesInvalid).toBe(true);
  });

  it("does not ask while disabled (Timeline tab hidden)", async () => {
    const call = vi.fn<HookArgs["callProcessFn"]>();
    await render(args(call, { enabled: false }));
    expect(call).not.toHaveBeenCalled();
  });
});
