import { useEffect, useState, type RefObject } from "react";

export function useNearViewport(
  nodeRef: RefObject<HTMLElement | null>,
  rootMargin = "300px 0px"
): boolean {
  const [nearViewport, setNearViewport] = useState(
    () => typeof IntersectionObserver === "undefined"
  );

  useEffect(() => {
    const node = nodeRef.current;
    if (!node || typeof IntersectionObserver === "undefined") {
      setNearViewport(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => setNearViewport(Boolean(entry?.isIntersecting)),
      { rootMargin }
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [nodeRef, rootMargin]);

  return nearViewport;
}
