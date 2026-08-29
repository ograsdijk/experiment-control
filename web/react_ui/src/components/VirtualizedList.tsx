import { ScrollArea } from "@mantine/core";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  useCallback,
  useState,
  type CSSProperties,
  type MutableRefObject,
  type ReactNode,
} from "react";

type Props<T> = {
  items: readonly T[];
  getItemKey: (item: T) => string | number;
  renderItem: (item: T) => ReactNode;
  estimateSize: number;
  height: CSSProperties["height"];
  viewportRef: MutableRefObject<HTMLDivElement | null>;
  empty?: ReactNode;
  gap?: number;
  overscan?: number;
};

/** Render only the variable-height rows around the active viewport. */
export function VirtualizedList<T>({
  items,
  getItemKey,
  renderItem,
  estimateSize,
  height,
  viewportRef,
  empty = null,
  gap = 6,
  overscan = 8,
}: Props<T>) {
  const [scrollElement, setScrollElement] = useState<HTMLDivElement | null>(null);
  const setViewport = useCallback(
    (node: HTMLDivElement | null) => {
      viewportRef.current = node;
      setScrollElement(node);
    },
    [viewportRef]
  );
  const indexedItemKey = useCallback(
    (index: number) => getItemKey(items[index]),
    [getItemKey, items]
  );
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollElement,
    estimateSize: () => estimateSize,
    getItemKey: indexedItemKey,
    gap,
    overscan,
  });
  const virtualRows = virtualizer.getVirtualItems();

  return (
    <ScrollArea h={height} viewportRef={setViewport}>
      {items.length === 0 ? (
        empty
      ) : (
        <div
          style={{
            height: virtualizer.getTotalSize(),
            position: "relative",
            width: "100%",
          }}
        >
          {virtualRows.map((virtualRow) => {
            const item = items[virtualRow.index];
            return (
              <div
                key={virtualRow.key}
                ref={virtualizer.measureElement}
                data-index={virtualRow.index}
                data-virtual-row-key={String(getItemKey(item))}
                style={{
                  left: 0,
                  position: "absolute",
                  top: 0,
                  transform: `translateY(${virtualRow.start}px)`,
                  width: "100%",
                }}
              >
                {renderItem(item)}
              </div>
            );
          })}
        </div>
      )}
    </ScrollArea>
  );
}
