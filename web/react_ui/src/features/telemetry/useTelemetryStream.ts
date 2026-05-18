import { useEffect, useRef, useState } from "react";
import { buildWsUrl, fetchTelemetrySnapshot } from "../../api";
import type { TelemetryMessage, TelemetrySignal } from "../../types";

export type LatestSignals = Record<string, Record<string, TelemetrySignal>>;

export type UseTelemetryStreamOptions = {
  /**
   * Hydrate state once via GET /api/snapshots/telemetry on mount so the UI is
   * populated before the first WS message arrives. Defaults to true.
   */
  hydrate?: boolean;
  /**
   * Filter incoming messages to a fixed set of device ids. When undefined,
   * keeps every device reported by the gateway.
   */
  deviceIds?: readonly string[];
  /**
   * Optional callback fired for each accepted WS message after `latestByDevice`
   * has been updated. Use for plot-buffer pushes, boolean-trace detection,
   * or other side effects that need the raw payload. Errors thrown by the
   * callback are swallowed so a bad listener can't tear down the socket.
   */
  onMessage?: (msg: TelemetryMessage) => void;
  /**
   * Optional callback fired once after the initial HTTP hydrate (only when
   * `hydrate` is true and the snapshot returned at least one device). Receives
   * the same `Record<deviceId, Record<signal, TelemetrySignal>>` shape as
   * `latestByDevice`. Use to backfill plot buffers from the snapshot.
   */
  onHydrate?: (snapshot: LatestSignals) => void;
};

export type UseTelemetryStreamResult = {
  latestByDevice: LatestSignals;
  wsConnected: boolean;
  telemetryActive: boolean;
  /** Wall-clock time of the most recent telemetry message, in ms. */
  lastMessageAt: number | null;
};

/**
 * Subscribe to /ws/telemetry and accumulate the most recent signal sample
 * per (device, signal). Optionally seeds from a one-shot HTTP snapshot.
 *
 * Consumers that need to feed plot ring buffers or detect boolean traces
 * pass an `onMessage` callback; the hook itself only owns connectivity and
 * the `latestByDevice` cache.
 */
export function useTelemetryStream(
  options: UseTelemetryStreamOptions = {},
): UseTelemetryStreamResult {
  const { hydrate = true, deviceIds, onMessage, onHydrate } = options;
  const [latestByDevice, setLatestByDevice] = useState<LatestSignals>({});
  const [wsConnected, setWsConnected] = useState(false);
  const [telemetryActive, setTelemetryActive] = useState(false);
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);

  const deviceFilterRef = useRef<Set<string> | null>(null);
  deviceFilterRef.current = deviceIds && deviceIds.length > 0 ? new Set(deviceIds) : null;
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;
  const onHydrateRef = useRef(onHydrate);
  onHydrateRef.current = onHydrate;

  useEffect(() => {
    if (!hydrate) {
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const snapshot = await fetchTelemetrySnapshot();
        if (cancelled) {
          return;
        }
        const entries = Object.entries(snapshot);
        if (entries.length === 0) {
          return;
        }
        const accepted: LatestSignals = {};
        setLatestByDevice((prev) => {
          const next: LatestSignals = { ...prev };
          for (const [deviceId, signals] of entries) {
            if (deviceFilterRef.current && !deviceFilterRef.current.has(deviceId)) {
              continue;
            }
            accepted[deviceId] = signals;
            next[deviceId] = { ...(next[deviceId] ?? {}), ...signals };
          }
          return next;
        });
        const handler = onHydrateRef.current;
        if (handler && Object.keys(accepted).length > 0) {
          try {
            handler(accepted);
          } catch {
            // Don't let a faulty listener prevent the WS subscription from starting.
          }
        }
      } catch {
        // Best-effort hydrate; WS will populate state once messages arrive.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [hydrate]);

  useEffect(() => {
    const ws = new WebSocket(buildWsUrl("/ws/telemetry"));
    ws.onopen = () => {
      setWsConnected(true);
      setTelemetryActive(false);
    };
    ws.onclose = () => {
      setWsConnected(false);
      setTelemetryActive(false);
    };
    ws.onerror = () => {
      // Authoritative disconnect signal is onclose.
    };
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as TelemetryMessage;
        const deviceId = msg?.payload?.device_id;
        if (!deviceId) {
          return;
        }
        if (deviceFilterRef.current && !deviceFilterRef.current.has(deviceId)) {
          return;
        }
        setWsConnected(true);
        setTelemetryActive(true);
        setLastMessageAt(Date.now());
        setLatestByDevice((prev) => {
          const next: LatestSignals = { ...prev };
          const deviceSignals = { ...(next[deviceId] ?? {}) };
          for (const [name, signal] of Object.entries(msg.payload.signals ?? {})) {
            deviceSignals[name] = signal;
          }
          next[deviceId] = deviceSignals;
          return next;
        });
        const handler = onMessageRef.current;
        if (handler) {
          try {
            handler(msg);
          } catch {
            // Don't let a faulty listener tear down the socket.
          }
        }
      } catch {
        // Ignore malformed frames.
      }
    };
    return () => {
      ws.close();
    };
  }, []);

  return { latestByDevice, wsConnected, telemetryActive, lastMessageAt };
}
