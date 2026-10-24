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
  /** The run overrides have errors: the plan uses the last valid ones. */
  overridesInvalid: boolean;
};

/**
 * `sequencer.plan` of the loaded sequence. Fetched when it is (re)loaded, a
 * run starts or ends, or (idle) the run overrides change; during a run every
 * PLAN_LIVE_REFRESH_MS while steps keep finishing, for the counts so far and
 * the measured step times. While the overrides have errors it keeps asking
 * with the last valid ones.
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
  overridesValid = true,
}: {
  callProcessFn: CallProcessFn;
  processId: string | null;
  enabled: boolean;
  loadedRevision: number | null;
  runId: number | null;
  runtimeState: string;
  completedSteps: number | null;
  overrides: Record<string, unknown>;
  overridesValid?: boolean;
}): SequencerPlanState {
  const [state, setState] = useState<{
    key: string | null;
    plan: SequencerPlan | null;
    error: string | null;
  }>({
    key: null,
    plan: null,
    error: null,
  });
  const requestIdRef = useRef(0);
  const live = LIVE_STATES.has(runtimeState);
  const validOverridesKeyRef = useRef("{}");
  if (overridesValid) {
    validOverridesKeyRef.current = JSON.stringify(overrides);
  }
  const overridesKey = live ? "{}" : validOverridesKeyRef.current;
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
            // A failure keeps the last plan only while it is for this scope.
            setState((prev) =>
              plan
                ? { key: scopeKey, plan, error: null }
                : {
                    key: scopeKey,
                    plan: prev.key === scopeKey ? prev.plan : null,
                    error:
                      resp.error?.code === "not_loaded"
                        ? null
                        : resp.error?.message ?? resp.error?.code ?? "plan unavailable",
                  }
            );
          })
          .catch(() => {
            if (requestId === requestIdRef.current) {
              setState((prev) => ({
                key: scopeKey,
                plan: prev.key === scopeKey ? prev.plan : null,
                error: "the sequencer can't be reached",
              }));
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

  const overridesInvalid = !live && !overridesValid;
  if (!enabled || state.key !== scopeKey) {
    return { plan: null, error: null, overridesInvalid };
  }
  return { plan: state.plan, error: state.error, overridesInvalid };
}
