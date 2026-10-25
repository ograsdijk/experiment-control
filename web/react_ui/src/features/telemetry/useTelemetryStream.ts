import { useEffect, useMemo, useRef, useState } from "react";
import { buildWsUrl, fetchTelemetrySnapshot } from "../../api";
import type { TelemetryMessage, TelemetrySignal } from "../../types";
import { openReconnectingSocket } from "../common/reconnectingSocket";
import { perfCount } from "../performance/perfInstrumentation";

export type LatestSignals = Record<string, Record<string, TelemetrySignal>>;

export type UseTelemetryStreamOptions = {
  /**
   * Hydrate state via GET /api/snapshots/telemetry on mount (and again after
   * a reconnect) so the UI is populated before the first WS message arrives.
   * Defaults to true.
   */
  hydrate?: boolean;
  /**
   * Keep only these device ids. When undefined, keeps every device reported
   * by the gateway. The list is also sent to the gateway, which then skips
   * other devices' messages before they reach the browser.
   */
  deviceIds?: readonly string[];
  /**
   * Optional callback fired for each accepted WS message, immediately (not
   * batched like `latestByDevice`). Use for plot-buffer pushes, receipt
   * times, or other side effects that need every raw payload. Errors thrown
   * by the callback are swallowed so a bad listener can't tear down the
   * socket.
   */
  onMessage?: (msg: TelemetryMessage) => void;
  /**
   * Optional callback fired after each HTTP hydrate (on mount and after a
   * reconnect; only when `hydrate` is true and the snapshot added at least
   * one signal). Receives the same `Record<deviceId, Record<signal,
   * TelemetrySignal>>` shape as `latestByDevice`, limited to signals the
   * socket had not already delivered. Use to backfill plot buffers. Note the
   * snapshot holds the gateway's last values, which may be old: a reconnect
   * hydrate is not evidence that a signal is currently updating.
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

type PendingSignals = Map<string, Record<string, TelemetrySignal>>;

/** Next animation frame, or a short timeout where rAF is unavailable. */
function scheduleFlush(callback: () => void): () => void {
  if (typeof window !== "undefined" && typeof window.requestAnimationFrame === "function") {
    const handle = window.requestAnimationFrame(() => callback());
    return () => window.cancelAnimationFrame(handle);
  }
  const handle = setTimeout(callback, 16);
  return () => clearTimeout(handle);
}

export function telemetrySocketPath(deviceIds: readonly string[] | null): string {
  if (!deviceIds || deviceIds.length === 0) {
    return "/ws/telemetry";
  }
  return `/ws/telemetry?device_ids=${encodeURIComponent(deviceIds.join(","))}`;
}

/**
 * Subscribe to /ws/telemetry and accumulate the most recent signal sample
 * per (device, signal). Optionally seeds from an HTTP snapshot.
 *
 * Messages are merged as they arrive but committed to React state at most
 * once per animation frame, so a burst of messages costs one render. Browsers
 * stop animation frames in hidden pages and in hidden iframes (the Control
 * Center keeps recently used instance UIs mounted but hidden), so a hidden
 * consumer stops rendering until it is shown again; it then catches up in one
 * render. `onMessage` still runs for every message.
 *
 * The socket reconnects with backoff after a drop and re-hydrates from the
 * snapshot, so a gateway restart does not need a page reload.
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

  // A stable key, so a new array with the same ids does not reopen the socket.
  const deviceKey = deviceIds && deviceIds.length > 0 ? [...deviceIds].sort().join(",") : "";
  const deviceFilter = useMemo(
    () => (deviceKey ? new Set(deviceKey.split(",")) : null),
    [deviceKey],
  );
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;
  const onHydrateRef = useRef(onHydrate);
  onHydrateRef.current = onHydrate;

  useEffect(() => {
    let disposed = false;
    let pending: PendingSignals = new Map();
    let pendingLastMessageAt: number | null = null;
    let cancelFlush: (() => void) | null = null;
    // Signals delivered by the current socket connection. A snapshot fetched
    // around the same time may be older than these, so it never overwrites
    // them (see loadSnapshot).
    let receivedOnSocket = new Map<string, Set<string>>();

    const accepts = (deviceId: string) => deviceFilter === null || deviceFilter.has(deviceId);

    const flush = () => {
      cancelFlush = null;
      if (disposed || (pending.size === 0 && pendingLastMessageAt === null)) {
        return;
      }
      const batch = pending;
      pending = new Map();
      if (batch.size > 0) {
        setLatestByDevice((prev) => {
          const next: LatestSignals = { ...prev };
          for (const [deviceId, signals] of batch) {
            next[deviceId] = { ...(next[deviceId] ?? {}), ...signals };
          }
          return next;
        });
      }
      if (pendingLastMessageAt !== null) {
        setLastMessageAt(pendingLastMessageAt);
        setTelemetryActive(true);
        pendingLastMessageAt = null;
      }
    };

    const enqueue = (deviceId: string, signals: Record<string, TelemetrySignal>) => {
      pending.set(deviceId, { ...(pending.get(deviceId) ?? {}), ...signals });
      if (cancelFlush === null) {
        cancelFlush = scheduleFlush(flush);
      }
    };

    const loadSnapshot = async () => {
      try {
        const snapshot = await fetchTelemetrySnapshot();
        if (disposed) {
          return;
        }
        // Fill in signals the socket has not delivered yet: the snapshot
        // replaces values shown from before a drop, but anything this
        // connection already received (flushed or still queued) is newer.
        const accepted: LatestSignals = {};
        for (const [deviceId, signals] of Object.entries(snapshot)) {
          if (!accepts(deviceId)) {
            continue;
          }
          const received = receivedOnSocket.get(deviceId);
          const fresh = received
            ? Object.fromEntries(Object.entries(signals).filter(([name]) => !received.has(name)))
            : signals;
          if (Object.keys(fresh).length > 0) {
            accepted[deviceId] = fresh;
          }
        }
        if (Object.keys(accepted).length === 0) {
          return;
        }
        setLatestByDevice((prev) => {
          const next: LatestSignals = { ...prev };
          for (const [deviceId, signals] of Object.entries(accepted)) {
            next[deviceId] = { ...(next[deviceId] ?? {}), ...signals };
          }
          return next;
        });
        const handler = onHydrateRef.current;
        if (handler) {
          try {
            handler(accepted);
          } catch {
            // Don't let a faulty listener prevent the WS subscription from running.
          }
        }
      } catch {
        // Best-effort hydrate; WS will populate state once messages arrive.
      }
    };

    if (hydrate) {
      void loadSnapshot();
    }

    const socket = openReconnectingSocket({
      url: buildWsUrl(telemetrySocketPath(deviceFilter ? [...deviceFilter] : null)),
      onOpen: ({ reconnect }) => {
        receivedOnSocket = new Map();
        setWsConnected(true);
        setTelemetryActive(false);
        if (reconnect && hydrate) {
          void loadSnapshot();
        }
      },
      onClose: () => {
        setWsConnected(false);
        setTelemetryActive(false);
      },
      onMessage: (event) => {
        try {
          const msg = JSON.parse(event.data) as TelemetryMessage;
          const deviceId = msg?.payload?.device_id;
          if (!deviceId || !accepts(deviceId)) {
            return;
          }
          const signals = msg.payload.signals ?? {};
          perfCount("telemetry.messages");
          perfCount("telemetry.signal_values", Object.keys(signals).length);
          pendingLastMessageAt = Date.now();
          let received = receivedOnSocket.get(deviceId);
          if (!received) {
            received = new Set();
            receivedOnSocket.set(deviceId, received);
          }
          for (const name of Object.keys(signals)) {
            received.add(name);
          }
          enqueue(deviceId, signals);
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
      },
    });

    return () => {
      disposed = true;
      cancelFlush?.();
      socket.close();
    };
  }, [hydrate, deviceFilter]);

  return { latestByDevice, wsConnected, telemetryActive, lastMessageAt };
}
