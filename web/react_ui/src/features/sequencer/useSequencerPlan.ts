import { useEffect, useRef, useState } from "react";
import type { ApiResponse } from "../../api";
import { normalizeSequencerPlan, type SequencerPlan } from "./plan";

/** Wait after the overrides change before asking again (as for resolve_vars). */
export const PLAN_DEBOUNCE_MS = 400;
/** During a run: refetch at most this often, and only after steps have finished. */
export const PLAN_LIVE_REFRESH_MS = 3000;

const LIVE_STATES = new Set(["RUNNING", "PAUSED", "STOP_REQUESTED"]);

type CallProcessFn = (
  processId: string,
  action: string,
  params: Record<string, unknown>
) => Promise<ApiResponse<unknown>>;

export type SequencerPlanState = {
  plan: SequencerPlan | null;
  /** Why the latest request failed, if it did (the last plan is kept). */
  error: string | null;
};

/**
 * `sequencer.plan` of the loaded sequence. Fetched when it is (re)loaded, a
 * run starts or ends, or (idle) the run overrides change; during a run every
 * PLAN_LIVE_REFRESH_MS while steps keep finishing, for the counts so far and
 * the measured step times.
 */
export function useSequencerPlan({
  callProcessFn,
  processId,
  enabled,
  loadedRevision,
  runId,
  runtimeState,
  completedSteps,
  overrides,
}: {
  callProcessFn: CallProcessFn;
  processId: string | null;
  enabled: boolean;
  loadedRevision: number | null;
  runId: number | null;
  runtimeState: string;
  completedSteps: number | null;
  overrides: Record<string, unknown>;
}): SequencerPlanState {
  const [state, setState] = useState<SequencerPlanState & { key: string | null }>({
    key: null,
    plan: null,
    error: null,
  });
  const requestIdRef = useRef(0);
  const live = LIVE_STATES.has(runtimeState);
  const overridesKey = live ? "{}" : JSON.stringify(overrides);
  const scopeKey = `${processId}|${loadedRevision}`;
  const [liveTick, setLiveTick] = useState(0);
  const completedRef = useRef<number | null>(null);
  completedRef.current = completedSteps;
  const fetchedAtCompletedRef = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled || !live) {
      return;
    }
    const timer = window.setInterval(() => {
      if (completedRef.current !== fetchedAtCompletedRef.current) {
        setLiveTick((tick) => tick + 1);
      }
    }, PLAN_LIVE_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [enabled, live]);

  useEffect(() => {
    if (!enabled || !processId) {
      return;
    }
    const requestId = ++requestIdRef.current;
    const timer = window.setTimeout(
      () => {
        fetchedAtCompletedRef.current = completedRef.current;
        callProcessFn(processId, "sequencer.plan", {
          vars_override: JSON.parse(overridesKey) as Record<string, unknown>,
        })
          .then((resp) => {
            if (requestId !== requestIdRef.current) {
              return;
            }
            const plan = resp.ok ? normalizeSequencerPlan(resp.result) : null;
            setState((prev) =>
              plan
                ? { key: scopeKey, plan, error: null }
                : {
                    key: prev.key,
                    plan: prev.plan,
                    error:
                      resp.error?.code === "not_loaded"
                        ? null
                        : resp.error?.message ?? resp.error?.code ?? "plan unavailable",
                  }
            );
          })
          .catch(() => {
            if (requestId === requestIdRef.current) {
              setState((prev) => ({ ...prev, error: "the sequencer can't be reached" }));
            }
          });
      },
      live ? 0 : PLAN_DEBOUNCE_MS
    );
    return () => {
      window.clearTimeout(timer);
      requestIdRef.current += 1;
    };
  }, [callProcessFn, processId, enabled, scopeKey, overridesKey, runId, live, liveTick]);

  if (!enabled || state.key !== scopeKey) {
    return { plan: null, error: null };
  }
  return { plan: state.plan, error: state.error };
}
