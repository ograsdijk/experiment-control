import type { Attributes } from "@dnd-kit/core";
import type { CSSProperties, ReactNode } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

export type SortableItemRenderArgs = {
  setNodeRef: (node: HTMLElement | null) => void;
  attributes: Attributes;
  listeners: Record<string, (event: unknown) => void> | undefined;
  style: CSSProperties;
  isDragging: boolean;
  isOver: boolean;
};

type SortableItemProps = {
  id: string;
  data: Record<string, unknown>;
  children: (args: SortableItemRenderArgs) => ReactNode;
};

export function SortableItem({ id, data, children }: SortableItemProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging, isOver } =
    useSortable({
      id,
      data,
    });
  const style: CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.55 : 1,
    zIndex: isDragging ? 20 : undefined,
  };
  return children({
    setNodeRef,
    attributes,
    listeners,
    style,
    isDragging,
    isOver,
  });
}
