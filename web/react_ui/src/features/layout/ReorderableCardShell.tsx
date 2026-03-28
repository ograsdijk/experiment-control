import { Card } from "@mantine/core";
import type { CSSProperties, ReactNode } from "react";
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
}: ReorderableCardShellProps) {
  return (
    <SortableItem id={id} data={data}>
      {({ setNodeRef, attributes, listeners, style: sortableStyle }) => (
        <Card
          ref={setNodeRef}
          className={className}
          radius="lg"
          p="md"
          data-panel-card-id={dataPanelCardId}
          data-device-card-id={dataDeviceCardId}
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
