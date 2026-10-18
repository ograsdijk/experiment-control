import { useLayoutEffect, useRef, useState } from "react";

/** Run chips keep at least this much room before nav labels collapse. */
const MIN_RUN_ZONE_PX = 240;

/** Width an element wants: ellipsized labels/names report their full text width. */
function naturalWidth(el: Element): number {
  const box = el as HTMLElement;
  let extra = 0;
  for (const label of box.querySelectorAll<HTMLElement>(".mantine-Button-label, .brand")) {
    extra += Math.max(0, label.scrollWidth - label.clientWidth);
  }
  return box.offsetWidth + extra;
}

function zoneNaturalWidth(zone: HTMLElement): number {
  const children = Array.from(zone.children);
  if (children.length === 0) {
    return 0;
  }
  const gap = parseFloat(getComputedStyle(zone).columnGap) || 0;
  return (
    children.reduce((sum, child) => sum + naturalWidth(child), 0) +
    gap * (children.length - 1)
  );
}

/**
 * Decides whether the header nav buttons should drop their text labels.
 * Labels collapse only when identity + labelled nav + the run chips (at
 * least MIN_RUN_ZONE_PX of them) no longer fit on the single header row,
 * so a sparse header keeps its labels on narrow screens and a busy one
 * collapses on wide ones.
 */
export function useHeaderCompact(contentKey: unknown) {
  const rowRef = useRef<HTMLDivElement | null>(null);
  const identityRef = useRef<HTMLDivElement | null>(null);
  const runRef = useRef<HTMLDivElement | null>(null);
  const navRef = useRef<HTMLDivElement | null>(null);
  const labelledNavWidth = useRef(0);
  const [compact, setCompact] = useState(false);
  const compactRef = useRef(compact);
  compactRef.current = compact;

  useLayoutEffect(() => {
    const row = rowRef.current;
    const identity = identityRef.current;
    const run = runRef.current;
    const nav = navRef.current;
    if (!row || !identity || !run || !nav) {
      return;
    }
    const measure = () => {
      if (!compactRef.current) {
        labelledNavWidth.current = nav.scrollWidth;
      }
      const style = getComputedStyle(row);
      const available =
        row.clientWidth -
        (parseFloat(style.paddingLeft) || 0) -
        (parseFloat(style.paddingRight) || 0);
      const gap = parseFloat(style.columnGap) || 0;
      const runNeed = Math.min(zoneNaturalWidth(run), MIN_RUN_ZONE_PX);
      // The instance name outranks nav labels: measure it untruncated.
      const need =
        naturalWidth(identity) + runNeed + labelledNavWidth.current + gap * 2;
      setCompact(need > available);
    };
    measure();
    if (typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver(measure);
    observer.observe(row);
    return () => observer.disconnect();
  }, [contentKey]);

  return { compact, rowRef, identityRef, runRef, navRef };
}
