import { Card } from "@mantine/core";
import type { CSSProperties, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { SortableItem } from "./SortableItem";

type ReorderableCardShellProps = {
  id: string;
  data: Record<string, unknown>;
  className: string;
  children: ReactNode;
  dragHandleTitle: string;
  style?: CSSProperties;
  dataPanelCardId?: string;
  dataDeviceCardId?: string;
  observeRef?: (node: HTMLElement | null) => void;
  /**
   * Denser padding for cards whose content should dominate (plot cards).
   * Also thins the edge drag handles: they sit *inside* the padding, so
   * 8px strips under 12px padding would swallow clicks on anything the
   * content places near an edge.
   */
  dense?: boolean;
  onPointerDownCapture?: (event: ReactPointerEvent<HTMLDivElement>) => void;
  onPointerUp?: (event: ReactPointerEvent<HTMLDivElement>) => void;
};

export function ReorderableCardShell({
  id,
  data,
  className,
  children,
  dragHandleTitle,
  style,
  dataPanelCardId,
  dataDeviceCardId,
  observeRef,
  dense = false,
  onPointerDownCapture,
  onPointerUp,
}: ReorderableCardShellProps) {
  return (
    <SortableItem id={id} data={data}>
      {({ setNodeRef, attributes, listeners, style: sortableStyle }) => (
        <Card
          ref={(node) => {
            setNodeRef(node);
            observeRef?.(node);
          }}
          className={dense ? `${className} reorderable-card-dense` : className}
          radius="lg"
          p={dense ? "sm" : "md"}
          data-panel-card-id={dataPanelCardId}
          data-device-card-id={dataDeviceCardId}
          onPointerDownCapture={onPointerDownCapture}
          onPointerUp={onPointerUp}
          style={{
            ...style,
            ...sortableStyle,
          }}
        >
          <div
            className="panel-drag-handle panel-drag-handle-top"
            title={dragHandleTitle}
            {...attributes}
            {...listeners}
          />
          <div
            className="panel-drag-handle panel-drag-handle-right"
            title={dragHandleTitle}
            {...attributes}
            {...listeners}
          />
          <div
            className="panel-drag-handle panel-drag-handle-bottom"
            title={dragHandleTitle}
            {...attributes}
            {...listeners}
          />
          <div
            className="panel-drag-handle panel-drag-handle-left"
            title={dragHandleTitle}
            {...attributes}
            {...listeners}
          />
          {children}
        </Card>
      )}
    </SortableItem>
  );
}
