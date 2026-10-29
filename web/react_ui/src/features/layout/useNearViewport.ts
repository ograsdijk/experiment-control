import { useEffect, useLayoutEffect, useState, type RefObject } from "react";

/** `rootMargin`'s px values as [top, right, bottom, left] (CSS shorthand; other units count as 0). */
function marginPx(rootMargin: string): [number, number, number, number] {
  const values = rootMargin
    .trim()
    .split(/\s+/)
    .map((part) => (part.endsWith("px") ? Number.parseFloat(part) || 0 : 0));
  const [top = 0, right = top, bottom = top, left = right] = values;
  return [top, right, bottom, left];
}

/** What the observer's first report will say, measured now. */
function isNear(node: Element, root: Element | null, rootMargin: string): boolean {
  const rect = node.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) {
    return false; // Not laid out (display: none).
  }
  const area = root
    ? root.getBoundingClientRect()
    : { top: 0, left: 0, bottom: window.innerHeight, right: window.innerWidth };
  const [top, right, bottom, left] = marginPx(rootMargin);
  return (
    rect.bottom >= area.top - top &&
    rect.top <= area.bottom + bottom &&
    rect.right >= area.left - left &&
    rect.left <= area.right + right
  );
}

/**
 * Whether the node is within `rootMargin` of the browser window, or of
 * `rootRef`'s element when given: a scroll container, whose own edges the
 * margin then extends (against the window, a node clipped by a scroll
 * container only counts once it is scrolled into view).
 *
 * The observer reports after the first paint, so a node mounted in view
 * shows its far state for a frame. `measureBeforePaint` measures it once
 * before then instead, for nodes that remount often while in view.
 */
export function useNearViewport(
  nodeRef: RefObject<HTMLElement | null>,
  rootMargin = "300px 0px",
  rootRef?: RefObject<Element | null>,
  measureBeforePaint = false
): boolean {
  const [nearViewport, setNearViewport] = useState(
    () => typeof IntersectionObserver === "undefined"
  );

  useLayoutEffect(() => {
    const node = nodeRef.current;
    if (!measureBeforePaint || !node || typeof IntersectionObserver === "undefined") {
      return;
    }
    setNearViewport(isNear(node, rootRef?.current ?? null, rootMargin));
  }, [measureBeforePaint, nodeRef, rootMargin, rootRef]);

  useEffect(() => {
    const node = nodeRef.current;
    if (!node || typeof IntersectionObserver === "undefined") {
      setNearViewport(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => setNearViewport(Boolean(entry?.isIntersecting)),
      { root: rootRef?.current ?? null, rootMargin }
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [nodeRef, rootMargin, rootRef]);

  return nearViewport;
}
