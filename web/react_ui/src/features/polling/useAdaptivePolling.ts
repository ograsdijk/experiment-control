import { useEffect, useRef } from "react";

import { perfCountScoped } from "../performance/perfInstrumentation";

export type AdaptivePollingOptions<T> = {
  enabled: boolean;
  intervalMs: number;
  poll: (signal: AbortSignal) => Promise<T>;
  onValue: (value: T) => void;
  equality?: (previous: T, next: T) => boolean;
  pauseWhenHidden?: boolean;
  refreshOnVisible?: boolean;
  pollImmediately?: boolean;
  endpoint?: string;
  restartKey?: unknown;
};

/** Completion-scheduled polling with visibility, equality, and abort handling. */
export function useAdaptivePolling<T>({
  enabled,
  intervalMs,
  poll,
  onValue,
  equality = Object.is,
  pauseWhenHidden = true,
  refreshOnVisible = true,
  pollImmediately = true,
  endpoint = "unknown",
  restartKey,
}: AdaptivePollingOptions<T>): void {
  const pollRef = useRef(poll);
  const onValueRef = useRef(onValue);
  const equalityRef = useRef(equality);
  pollRef.current = poll;
  onValueRef.current = onValue;
  equalityRef.current = equality;

  useEffect(() => {
    if (!enabled || typeof document === "undefined") {
      return;
    }
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;
    let inFlight = false;
    let refreshPending = false;
    let hasValue = false;
    let previousValue: T;

    const clearTimer = () => {
      if (timeout !== null) {
        clearTimeout(timeout);
        timeout = null;
      }
    };
    const hidden = () => pauseWhenHidden && document.hidden;
    const schedule = () => {
      clearTimer();
      if (!cancelled && !hidden() && intervalMs > 0) {
        timeout = setTimeout(run, intervalMs);
      }
    };
    const run = async () => {
      clearTimer();
      if (cancelled || hidden()) {
        return;
      }
      if (inFlight) {
        refreshPending = true;
        perfCountScoped("poll.overlap_attempts", endpoint);
        return;
      }
      refreshPending = false;
      inFlight = true;
      controller = new AbortController();
      perfCountScoped("poll.requests", endpoint);
      try {
        const next = await pollRef.current(controller.signal);
        if (!cancelled && (!hasValue || !equalityRef.current(previousValue, next))) {
          previousValue = next;
          hasValue = true;
          onValueRef.current(next);
        }
      } catch (error) {
        if (!cancelled && !(error instanceof DOMException && error.name === "AbortError")) {
          // Endpoint-specific refreshers retain their existing error presentation.
        }
      } finally {
        inFlight = false;
        controller = null;
        if (cancelled || hidden()) {
          return;
        }
        if (refreshPending) {
          refreshPending = false;
          void run();
        } else {
          schedule();
        }
      }
    };
    const onVisibilityChange = () => {
      if (hidden()) {
        clearTimer();
        return;
      }
      if (refreshOnVisible) {
        void run();
      } else {
        schedule();
      }
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    if (pollImmediately && !hidden()) {
      void run();
    } else {
      schedule();
    }
    return () => {
      cancelled = true;
      clearTimer();
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [
    enabled,
    endpoint,
    intervalMs,
    pauseWhenHidden,
    pollImmediately,
    refreshOnVisible,
    restartKey,
  ]);
}
