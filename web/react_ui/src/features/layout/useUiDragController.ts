import { useCallback, useState } from "react";
import {
  PointerSensor,
  TouchSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
} from "@dnd-kit/core";

import { sameStringArray } from "../common/compare";
import { reorderIdsSerpentine } from "./serpentine";
import { useDevicesContext } from "../devices/DevicesContext";
import { usePanels } from "../panels/PanelsContext";
import { isTelemetryPanel } from "../stream/panel_helpers";
import type { PlotPanelState } from "../stream/types";
import {
  parseSortablePrefixedId,
  type UiDragData,
} from "./drag_helpers";
import { useLayout } from "./LayoutContext";

/**
 * UI drag controller — owns the dnd-kit sensors, the four drag
 * lifecycle handlers, and the transient `activeUiDrag` state.
 *
 * Dispatches on `UiDragData.kind`:
 *
 * - **device** / **panel**: reorder the corresponding entity list
 *   serpentine-fashion using the grid-column count snapshot taken at
 *   drag-start.
 * - **command-deck-entry**: a no-op at drag-end — the reordering
 *   already happened live in drag-over via
 *   `reorderCommandDeckEntryWithinGroup`.
 * - **signal** / **trace**: drop onto a telemetry panel adds the
 *   trace; dropping a trace originating from another panel first
 *   removes it from the source panel.
 *
 * **Args**:
 *
 * - `resolveDeviceGridColumns()` / `resolvePanelGridColumns()` — App
 *   callbacks that snapshot the grid column count on drag-start so
 *   the serpentine reorder behaves correctly across grid widths.
 * - `addTraceToPanel` / `removeTraceFromPanel` — from
 *   `usePanelLifecycle`; used by the signal/trace drop.
 * - `reorderCommandDeckEntryWithinGroup` — from
 *   `useCommandDeckMutations`; called live in drag-over.
 *
 * Returns: `{ dndSensors, activeUiDrag, handleUiDragStart,
 * handleUiDragOver, handleUiDragEnd, handleUiDragCancel }`. Bind
 * these to the top-level `<DndContext>` in App.tsx; pass
 * `activeUiDrag` through to grid components that need to render a
 * drag-state hint (e.g. PanelCard's "Dragging" badge).
 */

export interface UiDragControllerArgs {
  resolveDeviceGridColumns: () => number;
  resolvePanelGridColumns: () => number;
  addTraceToPanel: (
    panelId: string,
    deviceId: string,
    signal: string
  ) => void;
  removeTraceFromPanel: (
    panelId: string,
    trace: { deviceId: string; signal: string }
  ) => void;
  reorderCommandDeckEntryWithinGroup: (
    entryId: string,
    targetEntryId: string
  ) => void;
}

export function useUiDragController(args: UiDragControllerArgs) {
  const {
    resolveDeviceGridColumns,
    resolvePanelGridColumns,
    addTraceToPanel,
    removeTraceFromPanel,
    reorderCommandDeckEntryWithinGroup,
  } = args;
  const { dragColumnsRef } = useLayout();
  const { orderedDevices, setDeviceOrder } = useDevicesContext();
  const { panelsRef, setPanels } = usePanels();

  const [activeUiDrag, setActiveUiDrag] = useState<UiDragData | null>(null);

  const dndSensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 6 },
    }),
    useSensor(TouchSensor, {
      activationConstraint: { delay: 140, tolerance: 8 },
    })
  );

  const handleUiDragStart = useCallback(
    (event: DragStartEvent) => {
      const payload = event.active.data.current as UiDragData | undefined;
      if (!payload) {
        setActiveUiDrag(null);
        return;
      }
      setActiveUiDrag(payload);
      if (payload.kind === "device") {
        dragColumnsRef.current.device = resolveDeviceGridColumns();
      } else if (payload.kind === "panel") {
        dragColumnsRef.current.panel = resolvePanelGridColumns();
      }
    },
    [dragColumnsRef, resolveDeviceGridColumns, resolvePanelGridColumns]
  );

  const handleUiDragOver = useCallback(
    (event: DragOverEvent) => {
      const activePayload = event.active.data.current as UiDragData | undefined;
      const overPayload = event.over?.data.current as UiDragData | undefined;
      if (!activePayload || activePayload.kind !== "command-deck-entry") {
        return;
      }
      const targetEntryId =
        overPayload?.kind === "command-deck-entry"
          ? overPayload.entryId
          : parseSortablePrefixedId(event.over?.id ?? "", "deck:");
      if (!targetEntryId || targetEntryId === activePayload.entryId) {
        return;
      }
      reorderCommandDeckEntryWithinGroup(activePayload.entryId, targetEntryId);
    },
    [reorderCommandDeckEntryWithinGroup]
  );

  const handleUiDragEnd = useCallback(
    (event: DragEndEvent) => {
      const activePayload = event.active.data.current as UiDragData | undefined;
      const overPayload = event.over?.data.current as UiDragData | undefined;
      if (!activePayload) {
        setActiveUiDrag(null);
        return;
      }

      if (activePayload.kind === "device") {
        const targetDeviceId =
          overPayload?.kind === "device"
            ? overPayload.deviceId
            : parseSortablePrefixedId(event.over?.id ?? "", "device:");
        if (targetDeviceId && targetDeviceId !== activePayload.deviceId) {
          const columns = Math.max(1, dragColumnsRef.current.device);
          setDeviceOrder((prev) => {
            const base = orderedDevices.map((device) => device.device_id);
            const next = reorderIdsSerpentine(
              base,
              activePayload.deviceId,
              targetDeviceId,
              columns
            );
            return sameStringArray(prev, next) ? prev : next;
          });
        }
        setActiveUiDrag(null);
        return;
      }

      if (activePayload.kind === "panel") {
        const targetPanelId =
          overPayload?.kind === "panel"
            ? overPayload.panelId
            : parseSortablePrefixedId(event.over?.id ?? "", "panel:");
        if (targetPanelId && targetPanelId !== activePayload.panelId) {
          const columns = Math.max(1, dragColumnsRef.current.panel);
          setPanels((prev) => {
            const ids = prev.map((panel) => panel.id);
            const reorderedIds = reorderIdsSerpentine(
              ids,
              activePayload.panelId,
              targetPanelId,
              columns
            );
            if (sameStringArray(ids, reorderedIds)) {
              return prev;
            }
            const byId = new Map(prev.map((panel) => [panel.id, panel]));
            return reorderedIds
              .map((panelId) => byId.get(panelId))
              .filter((panel): panel is PlotPanelState => Boolean(panel));
          });
        }
        setActiveUiDrag(null);
        return;
      }

      if (activePayload.kind === "command-deck-entry") {
        setActiveUiDrag(null);
        return;
      }

      if (activePayload.kind === "signal" || activePayload.kind === "trace") {
        const targetPanelId =
          overPayload?.kind === "panel"
            ? overPayload.panelId
            : parseSortablePrefixedId(event.over?.id ?? "", "panel:");
        if (!targetPanelId) {
          setActiveUiDrag(null);
          return;
        }
        const targetPanel = panelsRef.current.find(
          (panel) => panel.id === targetPanelId
        );
        if (!targetPanel || !isTelemetryPanel(targetPanel)) {
          setActiveUiDrag(null);
          return;
        }
        if (
          activePayload.kind === "trace" &&
          activePayload.originPanelId &&
          activePayload.originPanelId !== targetPanelId
        ) {
          removeTraceFromPanel(activePayload.originPanelId, {
            deviceId: activePayload.deviceId,
            signal: activePayload.signal,
          });
        }
        addTraceToPanel(
          targetPanelId,
          activePayload.deviceId,
          activePayload.signal
        );
      }
      setActiveUiDrag(null);
    },
    [
      addTraceToPanel,
      dragColumnsRef,
      orderedDevices,
      panelsRef,
      removeTraceFromPanel,
      setDeviceOrder,
      setPanels,
    ]
  );

  const handleUiDragCancel = useCallback(() => {
    setActiveUiDrag(null);
  }, []);

  return {
    dndSensors,
    activeUiDrag,
    handleUiDragStart,
    handleUiDragOver,
    handleUiDragEnd,
    handleUiDragCancel,
  };
}
