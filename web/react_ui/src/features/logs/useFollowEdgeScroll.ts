import { useEffect, useLayoutEffect, useRef, type MutableRefObject } from "react";

export type ScrollMetrics = Pick<
  HTMLDivElement,
  "clientHeight" | "scrollHeight" | "scrollTop"
>;

export function distanceToFollowEdge(
  metrics: ScrollMetrics,
  newestFirst: boolean
): number {
  return newestFirst
    ? metrics.scrollTop
    : metrics.scrollHeight - (metrics.scrollTop + metrics.clientHeight);
}

export function isNearFollowEdge(
  metrics: ScrollMetrics,
  newestFirst: boolean,
  thresholdPx = 24
): boolean {
  return distanceToFollowEdge(metrics, newestFirst) <= thresholdPx;
}

type Anchor = { key: string; offset: number };

type Options = {
  viewportRef: MutableRefObject<HTMLDivElement | null>;
  opened: boolean;
  enabled: boolean;
  newestFirst: boolean;
  itemKeys: readonly string[];
  resetKey?: string;
  thresholdPx?: number;
};

function visibleAnchor(host: HTMLDivElement): Anchor | null {
  const viewportTop = host.getBoundingClientRect().top;
  const rows = host.querySelectorAll<HTMLElement>("[data-virtual-row-key]");
  for (const row of rows) {
    const rect = row.getBoundingClientRect();
    if (rect.bottom > viewportTop) {
      return {
        key: row.dataset.virtualRowKey ?? "",
        offset: rect.top - viewportTop,
      };
    }
  }
  return null;
}

function restoreAnchor(host: HTMLDivElement, anchor: Anchor): boolean {
  const viewportTop = host.getBoundingClientRect().top;
  const rows = host.querySelectorAll<HTMLElement>("[data-virtual-row-key]");
  for (const row of rows) {
    if (row.dataset.virtualRowKey !== anchor.key) {
      continue;
    }
    host.scrollTop += row.getBoundingClientRect().top - viewportTop - anchor.offset;
    return true;
  }
  return false;
}

/** Follow new rows only while the user remains at the active list edge. */
export function useFollowEdgeScroll({
  viewportRef,
  opened,
  enabled,
  newestFirst,
  itemKeys,
  resetKey = "",
  thresholdPx = 24,
}: Options): void {
  const nearEdgeRef = useRef(true);
  const anchorRef = useRef<Anchor | null>(null);
  const previousKeysRef = useRef<readonly string[]>([]);
  const previousResetKeyRef = useRef(resetKey);

  useEffect(() => {
    if (!opened) {
      nearEdgeRef.current = true;
      anchorRef.current = null;
      return;
    }
    const host = viewportRef.current;
    if (!host) {
      return;
    }
    const updatePosition = () => {
      nearEdgeRef.current = isNearFollowEdge(host, newestFirst, thresholdPx);
      // Keep an anchor even at the follow edge. If auto-scroll is subsequently
      // disabled, new newest-first rows must preserve the current entry.
      anchorRef.current = visibleAnchor(host);
    };
    updatePosition();
    host.addEventListener("scroll", updatePosition, { passive: true });
    return () => host.removeEventListener("scroll", updatePosition);
  }, [viewportRef, opened, newestFirst, resetKey, thresholdPx]);

  useLayoutEffect(() => {
    const previousKeys = previousKeysRef.current;
    const resetChanged = previousResetKeyRef.current !== resetKey;
    previousKeysRef.current = itemKeys;
    previousResetKeyRef.current = resetKey;
    if (!opened) {
      return;
    }
    const host = viewportRef.current;
    if (!host) {
      return;
    }
    if (resetChanged) {
      nearEdgeRef.current = true;
      anchorRef.current = null;
    }
    const anchor = anchorRef.current;
    const anchorStillExists = anchor
      ? itemKeys.includes(anchor.key) && previousKeys.includes(anchor.key)
      : false;
    // Capture this before passive effects inspect the not-yet-moved viewport.
    // Otherwise opening/sorting an oldest-first list can clear the reset intent
    // before the animation frame applies it.
    const shouldFollow = enabled && nearEdgeRef.current;
    const frame = window.requestAnimationFrame(() => {
      if (shouldFollow) {
        host.scrollTop = newestFirst ? 0 : host.scrollHeight;
      } else if (anchor && anchorStillExists) {
        restoreAnchor(host, anchor);
      }
      nearEdgeRef.current = isNearFollowEdge(host, newestFirst, thresholdPx);
      anchorRef.current = visibleAnchor(host);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [viewportRef, opened, enabled, newestFirst, itemKeys, resetKey]);
}
