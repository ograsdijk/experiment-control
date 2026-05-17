import { useCallback } from "react";

import { normalizeTime } from "../stream/messages";
import { isTelemetryPanel } from "../stream/panel_helpers";
import type { TelemetryMessage } from "../../types";
import { usePanels } from "../panels/PanelsContext";
import { useTelemetry } from "./TelemetryContext";
import {
  useTelemetryStream,
  type LatestSignals,
} from "./useTelemetryStream";

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
 * sample, `setPlotTick` bumps the re-render counter.
 *
 * The hook pulls the telemetry refs, the panel setters, and the
 * reverse index directly from their respective contexts so the
 * call site can drop in with no args. Returns:
 *
 *     { latestByDevice, wsConnected, telemetryActive }
 */
export function useTelemetryPipeline(): {
  latestByDevice: LatestSignals;
  wsConnected: boolean;
  telemetryActive: boolean;
} {
  const { buffersRef, panelBuffersByTraceKey } = useTelemetry();
  const { setPanels, setPlotTick } = usePanels();

  const handleTelemetryHydrate = useCallback(
    (snapshot: LatestSignals) => {
      const booleanSignalKeys = new Set<string>();
      let pushedSamples = false;
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
            const panelIds = reverseIndex.get(traceKey);
            if (panelIds) {
              for (const panelId of panelIds) {
                const buffer = buffersRef.get(panelId)?.get(traceKey);
                if (buffer) {
                  buffer.push(normalizeTime(signal), plotValue);
                  pushedSamples = true;
                }
              }
            }
          }
        }
      }
      if (booleanSignalKeys.size > 0) {
        setPanels((prev) => {
          let changed = false;
          const next = prev.map((panel) => {
            if (!isTelemetryPanel(panel)) {
              return panel;
            }
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
      }
      if (pushedSamples) {
        setPlotTick((tick) => tick + 1);
      }
    },
    [buffersRef, panelBuffersByTraceKey, setPanels, setPlotTick]
  );

  const handleTelemetryMessage = useCallback(
    (msg: TelemetryMessage) => {
      const deviceId = msg.payload?.device_id;
      if (!deviceId) {
        return;
      }
      const bundleTs = msg.payload.ts?.t_wall;
      const booleanSignalKeys = new Set<string>();
      let pushedSamples = false;
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
          const panelIds = reverseIndex.get(traceKey);
          if (panelIds) {
            for (const panelId of panelIds) {
              const buffer = buffersRef.get(panelId)?.get(traceKey);
              if (buffer) {
                buffer.push(normalizeTime(signal, bundleTs), plotValue);
                pushedSamples = true;
              }
            }
          }
        }
      }
      if (pushedSamples) {
        setPlotTick((tick) => tick + 1);
      }
      if (booleanSignalKeys.size > 0) {
        setPanels((prev) => {
          let changed = false;
          const next = prev.map((panel) => {
            if (!isTelemetryPanel(panel)) {
              return panel;
            }
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
      }
    },
    [buffersRef, panelBuffersByTraceKey, setPanels, setPlotTick]
  );

  return useTelemetryStream({
    hydrate: true,
    onHydrate: handleTelemetryHydrate,
    onMessage: handleTelemetryMessage,
  });
}
