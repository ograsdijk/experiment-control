import { useEffect, useState } from "react";
import type { ApiResponse } from "../../api";
import type { SequencerRunEvent } from "./types";
import { normalizeSequencerRunEvents } from "./utils";

/** Delay before refetching run events after a failed fetch. */
export const RUN_EVENTS_RETRY_MS = 2000;

type CallProcess = (
  processId: string,
  action: string,
  params: Record<string, unknown>
) => Promise<ApiResponse<unknown>>;

/**
 * Events of the current/last run (`sequencer.run_events`), refetched when the
 * status summary's `seq` changes. A failed fetch is retried after
 * `RUN_EVENTS_RETRY_MS`: the seq it waits for won't change again until the
 * next event.
 */
export function useSequencerRunEvents(
  processId: string | null,
  seq: number | null,
  callProcessFn: CallProcess
): SequencerRunEvent[] {
  const [state, setState] = useState<{
    processId: string | null;
    seq: number | null;
    events: SequencerRunEvent[];
  }>({ processId: null, seq: null, events: [] });
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!processId || seq === null) {
      return;
    }
    if (state.processId === processId && state.seq === seq) {
      return;
    }
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    void (async () => {
      const resp = await callProcessFn(processId, "sequencer.run_events", {});
      if (cancelled) {
        return;
      }
      if (!resp.ok) {
        retryTimer = setTimeout(() => setRetry((count) => count + 1), RUN_EVENTS_RETRY_MS);
        return;
      }
      setState({ processId, seq, events: normalizeSequencerRunEvents(resp.result) });
    })();
    return () => {
      cancelled = true;
      if (retryTimer !== null) {
        clearTimeout(retryTimer);
      }
    };
  }, [callProcessFn, processId, retry, seq, state.processId, state.seq]);
  return state.processId === processId ? state.events : [];
}
