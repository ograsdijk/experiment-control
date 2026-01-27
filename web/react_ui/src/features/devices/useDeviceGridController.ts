import { useCallback, useState, type Dispatch, type DragEvent, type SetStateAction } from "react";
import type { DeviceStatus } from "../../types";
import type { ReorderMode } from "../stream/types";
import { sameStringArray } from "../common/compare";
import {
  collectGridEntries,
  computeInsertIndexFromGrid,
  computeVerticalReorderMode,
} from "../layout/reorder";

type UseDeviceGridControllerArgs = {
  orderedDevices: DeviceStatus[];
  setDeviceOrder: Dispatch<SetStateAction<string[]>>;
  setTelemetryCollapsedByDevice: Dispatch<
    SetStateAction<Record<string, boolean>>
  >;
};

export function useDeviceGridController({
  orderedDevices,
  setDeviceOrder,
  setTelemetryCollapsedByDevice,
}: UseDeviceGridControllerArgs) {
  const [dragDeviceId, setDragDeviceId] = useState<string | null>(null);
  const [dragOverDeviceTarget, setDragOverDeviceTarget] = useState<{
    deviceId: string;
    mode: ReorderMode;
  } | null>(null);
  const [deviceInsertIndex, setDeviceInsertIndex] = useState<number | null>(null);

  const moveDevice = useCallback(
    (sourceDeviceId: string, targetDeviceId: string, mode: ReorderMode) => {
      if (!sourceDeviceId || !targetDeviceId || sourceDeviceId === targetDeviceId) {
        return;
      }
      setDeviceOrder((prev) => {
        const base =
          prev.length > 0 ? [...prev] : orderedDevices.map((d) => d.device_id);
        const sourceIdx = base.indexOf(sourceDeviceId);
        const targetIdx = base.indexOf(targetDeviceId);
        if (sourceIdx < 0 || targetIdx < 0 || sourceIdx === targetIdx) {
          return prev;
        }
        const next = [...base];
        if (mode === "swap") {
          const temp = next[sourceIdx];
          next[sourceIdx] = next[targetIdx];
          next[targetIdx] = temp;
          return sameStringArray(prev, next) ? prev : next;
        }
        const [moved] = next.splice(sourceIdx, 1);
        const nextTargetIdx = next.indexOf(targetDeviceId);
        if (nextTargetIdx < 0) {
          return prev;
        }
        const insertIdx = mode === "before" ? nextTargetIdx : nextTargetIdx + 1;
        next.splice(insertIdx, 0, moved);
        return sameStringArray(prev, next) ? prev : next;
      });
    },
    [orderedDevices, setDeviceOrder]
  );

  const insertDeviceByIndex = useCallback(
    (sourceDeviceId: string, insertIndex: number) => {
      if (!sourceDeviceId || !Number.isFinite(insertIndex)) {
        return;
      }
      setDeviceOrder((prev) => {
        const base =
          prev.length > 0 ? [...prev] : orderedDevices.map((d) => d.device_id);
        const sourceIdx = base.indexOf(sourceDeviceId);
        if (sourceIdx < 0) {
          return prev;
        }
        const next = [...base];
        const [moved] = next.splice(sourceIdx, 1);
        const clamped = Math.max(0, Math.min(Math.trunc(insertIndex), next.length));
        next.splice(clamped, 0, moved);
        return sameStringArray(prev, next) ? prev : next;
      });
    },
    [orderedDevices, setDeviceOrder]
  );

  const handleDeviceTelemetryToggle = useCallback(
    (deviceId: string) => {
      setTelemetryCollapsedByDevice((prev) => ({
        ...prev,
        [deviceId]: !Boolean(prev[deviceId]),
      }));
    },
    [setTelemetryCollapsedByDevice]
  );

  const handleDeviceDragStart = useCallback(
    (deviceId: string, event: DragEvent<HTMLElement>) => {
      setDragDeviceId(deviceId);
      setDragOverDeviceTarget(null);
      setDeviceInsertIndex(null);
      const payload = { kind: "device", deviceId };
      event.dataTransfer.setData("application/x-ec-device", deviceId);
      event.dataTransfer.setData("application/json", JSON.stringify(payload));
      event.dataTransfer.effectAllowed = "move";
    },
    []
  );

  const handleDeviceDragEnd = useCallback(() => {
    setDragDeviceId(null);
    setDragOverDeviceTarget(null);
    setDeviceInsertIndex(null);
  }, []);

  const handleDeviceDragOver = useCallback(
    (deviceId: string, event: DragEvent<HTMLElement>) => {
      if (!dragDeviceId || dragDeviceId === deviceId) {
        return;
      }
      const mode = computeVerticalReorderMode(event);
      if (mode === "swap") {
        event.preventDefault();
        event.stopPropagation();
        setDeviceInsertIndex(null);
        setDragOverDeviceTarget((prev) =>
          prev && prev.deviceId === deviceId && prev.mode === "swap"
            ? prev
            : { deviceId, mode: "swap" }
        );
        return;
      }
      if (dragOverDeviceTarget !== null) {
        setDragOverDeviceTarget(null);
      }
    },
    [dragDeviceId, dragOverDeviceTarget]
  );

  const handleDeviceDragLeave = useCallback(
    (deviceId: string) => {
      if (dragOverDeviceTarget?.deviceId === deviceId) {
        setDragOverDeviceTarget(null);
      }
    },
    [dragOverDeviceTarget]
  );

  const handleDeviceDrop = useCallback(
    (targetDeviceId: string, event: DragEvent<HTMLElement>) => {
      if (!dragDeviceId) {
        return;
      }
      const mode = computeVerticalReorderMode(event);
      if (mode !== "swap") {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      const sourceFromMime = event.dataTransfer.getData("application/x-ec-device");
      let sourceDeviceId = sourceFromMime || dragDeviceId;
      if (!sourceDeviceId) {
        try {
          const raw = event.dataTransfer.getData("application/json");
          if (raw) {
            const payload = JSON.parse(raw) as { kind?: string; deviceId?: string };
            if (payload.kind === "device" && typeof payload.deviceId === "string") {
              sourceDeviceId = payload.deviceId;
            }
          }
        } catch {
          sourceDeviceId = dragDeviceId;
        }
      }
      if (sourceDeviceId && sourceDeviceId !== targetDeviceId) {
        moveDevice(sourceDeviceId, targetDeviceId, mode);
      }
      setDragDeviceId(null);
      setDragOverDeviceTarget(null);
      setDeviceInsertIndex(null);
    },
    [dragDeviceId, moveDevice]
  );

  const handleDeviceGridDragOver = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      if (!dragDeviceId) {
        return;
      }
      event.preventDefault();
      if (
        event.target instanceof Element &&
        event.target.closest("[data-device-card-id]")
      ) {
        return;
      }
      const container = event.currentTarget;
      const entries = collectGridEntries(
        container,
        "data-device-card-id",
        dragDeviceId
      );
      const index = computeInsertIndexFromGrid(entries, event.clientX, event.clientY);
      setDragOverDeviceTarget(null);
      setDeviceInsertIndex((prev) => (prev === index ? prev : index));
    },
    [dragDeviceId]
  );

  const handleDeviceGridDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      if (!dragDeviceId) {
        return;
      }
      event.preventDefault();
      const sourceFromMime = event.dataTransfer.getData("application/x-ec-device");
      const sourceDeviceId = sourceFromMime || dragDeviceId;
      if (!sourceDeviceId) {
        setDragDeviceId(null);
        setDragOverDeviceTarget(null);
        setDeviceInsertIndex(null);
        return;
      }
      const container = event.currentTarget;
      const entries = collectGridEntries(
        container,
        "data-device-card-id",
        sourceDeviceId
      );
      const index =
        deviceInsertIndex ??
        computeInsertIndexFromGrid(entries, event.clientX, event.clientY);
      insertDeviceByIndex(sourceDeviceId, index);
      setDragDeviceId(null);
      setDragOverDeviceTarget(null);
      setDeviceInsertIndex(null);
    },
    [deviceInsertIndex, dragDeviceId, insertDeviceByIndex]
  );

  const handleDeviceGridDragLeave = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      if (!dragDeviceId) {
        return;
      }
      const nextTarget = event.relatedTarget as Node | null;
      if (nextTarget && event.currentTarget.contains(nextTarget)) {
        return;
      }
      setDeviceInsertIndex(null);
      setDragOverDeviceTarget(null);
    },
    [dragDeviceId]
  );

  return {
    dragDeviceId,
    dragOverDeviceTarget,
    deviceInsertIndex,
    handleDeviceTelemetryToggle,
    handleDeviceDragStart,
    handleDeviceDragEnd,
    handleDeviceDragOver,
    handleDeviceDragLeave,
    handleDeviceDrop,
    handleDeviceGridDragOver,
    handleDeviceGridDrop,
    handleDeviceGridDragLeave,
  };
}
