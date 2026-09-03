import { useCallback } from "react";

import { normalizeTime } from "../stream/messages";
import { isTelemetryPanel } from "../stream/panel_helpers";
import type { TelemetryMessage } from "../../types";
import { usePanels } from "../panels/PanelsContext";
import { useTelemetry } from "./TelemetryContext";
import { markPanelsDirty } from "../panels/PanelInvalidationStore";
import {
  type LatestSignals,
} from "./useTelemetryStream";
import {
  telemetryLatestStore,
  useTelemetryConnectionState,
} from "./TelemetryLatestStore";
import { useTelemetrySocket } from "./useTelemetrySocket";
import { RingBuffer } from "../../utils/ringBuffer";

export function pushTelemetrySampleToPanels(
  reverseIndex: Map<string, Set<string>>,
  buffers: Map<string, Map<string, RingBuffer>>,
  traceKey: string,
  time: number,
  value: number,
  dirtyPanelIds: Set<string>
): void {
  for (const panelId of reverseIndex.get(traceKey) ?? []) {
    const buffer = buffers.get(panelId)?.get(traceKey);
    if (!buffer) continue;
    buffer.push(time, value);
    dirtyPanelIds.add(panelId);
  }
}

/**
 * End-to-end telemetry pipeline: subscribes to `/ws/telemetry` and
 * fans incoming samples out into the panel buffers, while also
 * promoting trace `valueKind` to `"boolean"` the first time a
 * signal arrives as a bool.
 *
 * Combines what App.tsx used to wire as four separate pieces:
 *
 * - `handleTelemetryHydrate(snapshot)` — bulk-applies the initial
 *   `latest_by_device` snapshot returned at WS connect.
 * - `handleTelemetryMessage(msg)` — per-message live update.
 * - The boolean-promotion logic that flips telemetry trace
 *   `valueKind` to `"boolean"` once a signal proves boolean.
 * - The `useTelemetryStream({ hydrate, onHydrate, onMessage })` call
 *   that owns the WS connection.
 *
 * The two handlers share most of their body (a P5 reverse-index
 * lookup per signal). Keeping them together makes the shared shape
 * obvious. Pushes happen through `buffersRef` so the hot path
 * doesn't trigger React renders; once a batch pushed at least one
 * sample, exactly the interested panel IDs are scheduled for redraw.
 *
 * The hook pulls the telemetry refs, the panel setters, and the
 * reverse index directly from their respective contexts so the
 * call site can drop in with no args. Returns:
 *
 *     { latestByDevice, wsConnected, telemetryActive }
 */
export function useTelemetryPipeline(): {
  wsConnected: boolean;
  telemetryActive: boolean;
} {
  const { buffersRef, panelBuffersByTraceKey } = useTelemetry();
  const { panelsRef, setPanels } = usePanels();

  const promoteBooleanTraces = useCallback(
    (booleanSignalKeys: Set<string>) => {
      const reverseIndex = panelBuffersByTraceKey.current;
      const needsPromotion = [...booleanSignalKeys].some((key) =>
        [...(reverseIndex.get(key) ?? [])].some((panelId) => {
          const panel = panelsRef.current.find((candidate) => candidate.id === panelId);
          return (
            panel !== undefined &&
            isTelemetryPanel(panel) &&
            panel.traces.some(
              (trace) =>
                `${trace.deviceId}:${trace.signal}` === key &&
                trace.valueKind !== "boolean"
            )
          );
        })
      );
      if (!needsPromotion) return;
      setPanels((prev) => {
        let changed = false;
        const next = prev.map((panel) => {
          if (!isTelemetryPanel(panel)) return panel;
          let tracesChanged = false;
          const nextTraces = panel.traces.map((trace) => {
            const key = `${trace.deviceId}:${trace.signal}`;
            if (!booleanSignalKeys.has(key) || trace.valueKind === "boolean") {
              return trace;
            }
            tracesChanged = true;
            changed = true;
            return { ...trace, valueKind: "boolean" as const };
          });
          return tracesChanged ? { ...panel, traces: nextTraces } : panel;
        });
        return changed ? next : prev;
      });
    },
    [panelBuffersByTraceKey, panelsRef, setPanels]
  );

  const handleTelemetryHydrate = useCallback(
    (snapshot: LatestSignals) => {
      const booleanSignalKeys = new Set<string>();
      const dirtyPanelIds = new Set<string>();
      const reverseIndex = panelBuffersByTraceKey.current;
      for (const [deviceId, signals] of Object.entries(snapshot)) {
        for (const [name, signal] of Object.entries(signals)) {
          const traceKey = `${deviceId}:${name}`;
          let plotValue: number | null = null;
          if (typeof signal.value === "number" && Number.isFinite(signal.value)) {
            plotValue = signal.value;
          } else if (typeof signal.value === "boolean") {
            plotValue = signal.value ? 1 : 0;
            booleanSignalKeys.add(traceKey);
          }
          if (plotValue !== null) {
            pushTelemetrySampleToPanels(
              reverseIndex,
              buffersRef,
              traceKey,
              normalizeTime(signal),
              plotValue,
              dirtyPanelIds
            );
          }
        }
      }
      if (booleanSignalKeys.size > 0) promoteBooleanTraces(booleanSignalKeys);
      markPanelsDirty(dirtyPanelIds);
    },
    [buffersRef, panelBuffersByTraceKey, promoteBooleanTraces]
  );

  const handleTelemetryMessage = useCallback(
    (msg: TelemetryMessage) => {
      const deviceId = msg.payload?.device_id;
      if (!deviceId) {
        return;
      }
      const bundleTs = msg.payload.ts?.t_wall;
      const booleanSignalKeys = new Set<string>();
      const dirtyPanelIds = new Set<string>();
      const reverseIndex = panelBuffersByTraceKey.current;
      for (const [name, signal] of Object.entries(msg.payload.signals ?? {})) {
        const traceKey = `${deviceId}:${name}`;
        let plotValue: number | null = null;
        if (typeof signal.value === "number" && Number.isFinite(signal.value)) {
          plotValue = signal.value;
        } else if (typeof signal.value === "boolean") {
          plotValue = signal.value ? 1 : 0;
          booleanSignalKeys.add(traceKey);
        }
        if (plotValue !== null) {
          pushTelemetrySampleToPanels(
            reverseIndex,
            buffersRef,
            traceKey,
            normalizeTime(signal, bundleTs),
            plotValue,
            dirtyPanelIds
          );
        }
      }
      markPanelsDirty(dirtyPanelIds);
      if (booleanSignalKeys.size > 0) promoteBooleanTraces(booleanSignalKeys);
    },
    [buffersRef, panelBuffersByTraceKey, promoteBooleanTraces]
  );

  useTelemetrySocket({
    hydrate: true,
    onHydrate: handleTelemetryHydrate,
    onMessage: handleTelemetryMessage,
  });
  return useTelemetryConnectionState(telemetryLatestStore);
}
