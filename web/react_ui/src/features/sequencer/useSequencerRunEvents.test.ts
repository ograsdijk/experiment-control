// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiResponse } from "../../api";
import type { SequencerRunEvent } from "./types";
import { RUN_EVENTS_RETRY_MS, useSequencerRunEvents } from "./useSequencerRunEvents";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
let latest: SequencerRunEvent[] = [];

function Probe(props: {
  seq: number | null;
  call: (id: string, action: string, params: Record<string, unknown>) => Promise<ApiResponse<unknown>>;
}) {
  latest = useSequencerRunEvents("sequencer", props.seq, props.call);
  return null;
}

const EVENTS = {
  run_id: 1,
  seq: 3,
  dropped: 0,
  events: [
    {
      severity: "error",
      kind: "step_failed",
      message: "boom",
      source: null,
      step: null,
      elapsed_s: 1,
      last_elapsed_s: 1,
      count: 1,
    },
  ],
};

beforeEach(() => {
  vi.useFakeTimers();
  container = document.createElement("div");
  root = createRoot(container);
  latest = [];
});

afterEach(() => {
  act(() => root.unmount());
  vi.useRealTimers();
});

describe("useSequencerRunEvents", () => {
  it("retries a failed fetch without waiting for the next event", async () => {
    const call = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, error: "rpc timeout" })
      .mockResolvedValue({ ok: true, result: EVENTS });
    await act(async () => {
      root.render(createElement(Probe, { seq: 3, call }));
    });
    expect(call).toHaveBeenCalledTimes(1);
    expect(latest).toEqual([]);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUN_EVENTS_RETRY_MS);
    });
    expect(call).toHaveBeenCalledTimes(2);
    expect(latest.map((event) => event.message)).toEqual(["boom"]);

    // Same seq: no further fetches.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUN_EVENTS_RETRY_MS * 3);
    });
    expect(call).toHaveBeenCalledTimes(2);
  });
});
