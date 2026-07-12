export const STREAM_ANALYSIS_HYDRATION_INVALIDATE_EVENT =
  "experiment-control:stream-analysis-hydration-invalidate";

export type StreamAnalysisRefreshRequest = {
  workspaceId: string;
  outputIds: string[];
  maxTracePoints?: number;
};

export type StreamAnalysisHydrationInvalidationDetail = {
  requests: StreamAnalysisRefreshRequest[];
};

export function normalizeStreamAnalysisRefreshRequests(
  requests: StreamAnalysisRefreshRequest[]
): StreamAnalysisRefreshRequest[] {
  const grouped = new Map<
    string,
    { outputIds: Set<string>; maxTracePoints?: number }
  >();
  for (const request of requests) {
    const workspaceId = String(request.workspaceId ?? "").trim();
    const outputIds = (request.outputIds ?? [])
      .map((outputId) => String(outputId ?? "").trim())
      .filter(Boolean);
    if (!workspaceId || outputIds.length === 0) continue;
    const current = grouped.get(workspaceId) ?? { outputIds: new Set<string>() };
    outputIds.forEach((outputId) => current.outputIds.add(outputId));
    if (Number.isFinite(request.maxTracePoints)) {
      current.maxTracePoints = Math.max(
        current.maxTracePoints ?? 0,
        Math.max(32, Math.trunc(Number(request.maxTracePoints)))
      );
    }
    grouped.set(workspaceId, current);
  }
  return [...grouped.entries()].map(
    ([workspaceId, { outputIds, maxTracePoints }]) => ({
      workspaceId,
      outputIds: [...outputIds],
      ...(maxTracePoints === undefined ? {} : { maxTracePoints }),
    })
  );
}

export function dispatchStreamAnalysisHydrationInvalidation(
  request: StreamAnalysisRefreshRequest
) {
  if (typeof window === "undefined") return;
  const requests = normalizeStreamAnalysisRefreshRequests([request]);
  if (requests.length === 0) return;
  window.dispatchEvent(
    new CustomEvent<StreamAnalysisHydrationInvalidationDetail>(
      STREAM_ANALYSIS_HYDRATION_INVALIDATE_EVENT,
      { detail: { requests } }
    )
  );
}

export function streamAnalysisHydrationInvalidationRequests(
  event: Event
): StreamAnalysisRefreshRequest[] {
  const detail = (
    event as CustomEvent<StreamAnalysisHydrationInvalidationDetail>
  ).detail;
  return detail && Array.isArray(detail.requests)
    ? normalizeStreamAnalysisRefreshRequests(detail.requests)
    : [];
}

export function isStreamAnalysisRefreshOutputRequested(
  request: StreamAnalysisRefreshRequest,
  workspaceId: string,
  outputId: string
): boolean {
  return (
    request.workspaceId === workspaceId && request.outputIds.includes(outputId)
  );
}
