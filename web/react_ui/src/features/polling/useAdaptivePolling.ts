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
  /**
   * Abort a poll that has not completed after this long. Defaults to
   * `defaultPollTimeoutMs(intervalMs)`. Without it a hung request would hold
   * the in-flight guard forever and silently stop this poll.
   */
  timeoutMs?: number;
};

const MIN_POLL_TIMEOUT_MS = 10000;

export function defaultPollTimeoutMs(intervalMs: number): number {
  return Math.max(intervalMs * 3, MIN_POLL_TIMEOUT_MS);
}

/**
 * Settle with `work`, or reject with an AbortError as soon as `signal`
 * aborts. Many poll callbacks never thread the signal into their fetches, so
 * aborting alone would not release the in-flight guard on a hung request.
 */
export function abortable<T>(work: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) {
    void work.catch(() => undefined);
    return Promise.reject(new DOMException("Poll aborted", "AbortError"));
  }
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(new DOMException("Poll aborted", "AbortError"));
    signal.addEventListener("abort", onAbort, { once: true });
    work.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      }
    );
  });
}

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
  timeoutMs,
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
      const requestController = new AbortController();
      controller = requestController;
      const timeoutTimer = setTimeout(
        () => requestController.abort(),
        timeoutMs ?? defaultPollTimeoutMs(intervalMs)
      );
      perfCountScoped("poll.requests", endpoint);
      try {
        const next = await abortable(
          pollRef.current(requestController.signal),
          requestController.signal
        );
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
        clearTimeout(timeoutTimer);
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
    timeoutMs,
  ]);
}
