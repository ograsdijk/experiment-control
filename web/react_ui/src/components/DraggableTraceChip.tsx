import { type CSSProperties, type ReactNode } from "react";
import { useDraggable } from "@dnd-kit/core";

import type { TraceKey } from "../types";

/**
 * A draggable chip that represents a single telemetry trace.
 *
 * Renders any children inside a span that registers a `useDraggable`
 * handle with payload `{ kind: "trace", deviceId, signal, originPanelId }`.
 * The drop-side (PanelsGrid's plot panels) inspects the payload to
 * know which trace was dragged.
 */
export type DraggableTraceChipProps = {
  panelId: string;
  trace: TraceKey;
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
};

export function DraggableTraceChip({
  panelId,
  trace,
  children,
  className,
  style,
}: DraggableTraceChipProps) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `trace:${panelId}:${trace.deviceId}:${trace.signal}`,
    data: {
      kind: "trace",
      deviceId: trace.deviceId,
      signal: trace.signal,
      originPanelId: panelId,
    },
  });
  return (
    <span
      ref={setNodeRef}
      className={className}
      style={{
        ...style,
        cursor: "grab",
        opacity: isDragging ? 0.55 : 1,
      }}
      {...attributes}
      {...listeners}
    >
      {children}
    </span>
  );
}
