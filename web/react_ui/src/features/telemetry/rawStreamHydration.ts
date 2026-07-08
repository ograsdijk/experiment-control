import {
  isStreamTracePanel,
} from "../stream/panel_helpers";
import type {
  PlotPanelState,
  RawStreamSubscription,
} from "../stream/types";
import {
  normalizeTraceAverageMode,
  normalizeTraceDecimator,
  normalizeTraceMaxFps,
  normalizeTraceMaxPoints,
  normalizeTraceRollingWindow,
} from "../stream/utils";

export const RAW_STREAM_HYDRATION_INVALIDATE_EVENT =
  "experiment-control:raw-stream-hydration-invalidate";

export type RawStreamHydrationInvalidationDetail = {
  keys: string[];
};

export function rawStreamSubscriptionKey(
  subscription: RawStreamSubscription
): string {
  return `${subscription.deviceId}|${subscription.stream}|${subscription.channelIndex}|${subscription.traceDecimator}|${subscription.traceMaxPoints}|${subscription.traceMaxFps.toFixed(3)}|${subscription.rollingWindow}|${subscription.averageMode}`;
}

export function rawStreamSubscriptionKeysForPanel(
  panel: PlotPanelState
): string[] {
  if (
    !isStreamTracePanel(panel) ||
    panel.sourceMode !== "raw" ||
    panel.stream === null
  ) {
    return [];
  }
  const traceDecimator = normalizeTraceDecimator(panel.traceDecimator);
  const traceMaxPoints = normalizeTraceMaxPoints(panel.traceMaxPoints);
  const traceMaxFps = normalizeTraceMaxFps(panel.traceMaxFps);
  const rollingWindow = normalizeTraceRollingWindow(panel.rollingWindow);
  const averageMode = normalizeTraceAverageMode(panel.averageMode);
  const extraChannels =
    panel.kind === "stream_raw" ? panel.extraChannelIndices ?? [] : [];
  const channels = [
    ...new Set(
      [panel.channelIndex, ...extraChannels].map((value) =>
        Math.max(0, Math.trunc(value))
      )
    ),
  ];
  return channels.map((channelIndex) =>
    rawStreamSubscriptionKey({
      deviceId: panel.stream!.deviceId,
      stream: panel.stream!.stream,
      channelIndex,
      traceDecimator,
      traceMaxPoints,
      traceMaxFps,
      rollingWindow,
      averageMode,
    })
  );
}

export function dispatchRawStreamHydrationInvalidation(keys: string[]) {
  if (typeof window === "undefined") {
    return;
  }
  const cleanKeys = [...new Set(keys.filter((key) => key.trim().length > 0))];
  if (cleanKeys.length <= 0) {
    return;
  }
  window.dispatchEvent(
    new CustomEvent<RawStreamHydrationInvalidationDetail>(
      RAW_STREAM_HYDRATION_INVALIDATE_EVENT,
      { detail: { keys: cleanKeys } }
    )
  );
}

export function rawStreamHydrationInvalidationKeys(event: Event): string[] {
  const detail = (event as CustomEvent<RawStreamHydrationInvalidationDetail>)
    .detail;
  if (!detail || !Array.isArray(detail.keys)) {
    return [];
  }
  return [...new Set(detail.keys.filter((key) => typeof key === "string"))];
}

export function prepareRawStreamHydration(
  subscriptions: RawStreamSubscription[],
  hydratedKeys: Set<string>,
  invalidatedKeys: Set<string>
): {
  activeKeys: Set<string>;
  nextHydratedKeys: Set<string>;
  pending: RawStreamSubscription[];
} {
  const activeKeys = new Set(subscriptions.map(rawStreamSubscriptionKey));
  const nextHydratedKeys = new Set(
    [...hydratedKeys].filter(
      (key) => activeKeys.has(key) && !invalidatedKeys.has(key)
    )
  );
  const seenPending = new Set<string>();
  const pending = subscriptions.filter((subscription) => {
    const key = rawStreamSubscriptionKey(subscription);
    if (nextHydratedKeys.has(key) || seenPending.has(key)) {
      return false;
    }
    seenPending.add(key);
    return true;
  });
  return { activeKeys, nextHydratedKeys, pending };
}
