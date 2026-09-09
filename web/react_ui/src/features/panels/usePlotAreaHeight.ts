import { useEffect, useRef, useState } from "react";

/**
 * Plot height for a panel card.
 *
 * The grid is `align-items: start` and every card is content-sized, so
 * there is no outer height for the plot to fill — observing the card's
 * *height* to set the plot's height would feed back into itself. Width is
 * safe: a taller plot never changes how wide the card is, so the observer
 * settles after one pass.
 *
 * Cards therefore get a plot whose height tracks their width by aspect
 * ratio, clamped so a narrow single-column card stays readable and a very
 * wide one does not turn into a banner. An explicit `pinnedHeightPx` from
 * panel state wins outright.
 */
export const PLOT_ASPECT_RATIO = 0.52;
export const MIN_AUTO_PLOT_HEIGHT_PX = 220;
export const MAX_AUTO_PLOT_HEIGHT_PX = 460;

export function autoPlotHeight(widthPx: number): number {
  if (!Number.isFinite(widthPx) || widthPx <= 0) {
    return MIN_AUTO_PLOT_HEIGHT_PX;
  }
  return Math.round(
    Math.min(
      MAX_AUTO_PLOT_HEIGHT_PX,
      Math.max(MIN_AUTO_PLOT_HEIGHT_PX, widthPx * PLOT_ASPECT_RATIO)
    )
  );
}

export function usePlotAreaHeight(pinnedHeightPx?: number | null): {
  measureRef: (node: HTMLElement | null) => void;
  plotHeight: number;
} {
  const [width, setWidth] = useState(0);
  const nodeRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const node = nodeRef.current;
    // Pinned panels never need the observer; skip it rather than measure
    // a width nothing reads.
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width ?? 0;
      setWidth((prev) => (Math.abs(prev - next) < 1 ? prev : next));
    });
    observer.observe(node);
    setWidth(node.getBoundingClientRect().width);
    return () => observer.disconnect();
  }, []);

  const measureRef = (node: HTMLElement | null) => {
    nodeRef.current = node;
  };

  const plotHeight =
    typeof pinnedHeightPx === "number" && Number.isFinite(pinnedHeightPx)
      ? pinnedHeightPx
      : autoPlotHeight(width);

  return { measureRef, plotHeight };
}
