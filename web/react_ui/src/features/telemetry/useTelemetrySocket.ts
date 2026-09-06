import { useEffect, useRef } from "react";

import { buildWsUrl, fetchTelemetrySnapshot } from "../../api";
import type { TelemetryMessage } from "../../types";
import { perfCount } from "../performance/perfInstrumentation";
import {
  telemetryLatestStore,
  type TelemetryLatestStore,
} from "./TelemetryLatestStore";
import type { LatestSignals } from "./useTelemetryStream";

export type UseTelemetrySocketOptions = {
  hydrate?: boolean;
  store?: TelemetryLatestStore;
  onMessage?: (message: TelemetryMessage) => void;
  onHydrate?: (snapshot: LatestSignals) => void;
};

export const TELEMETRY_RECONNECT_DELAY_MS = 1000;

/** Low-level telemetry transport. It never stores message data in React state. */
export function useTelemetrySocket(options: UseTelemetrySocketOptions = {}): void {
  const { hydrate = true, store = telemetryLatestStore, onMessage, onHydrate } = options;
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;
  const onHydrateRef = useRef(onHydrate);
  onHydrateRef.current = onHydrate;

  useEffect(() => {
    if (!hydrate) return;
    let cancelled = false;
    void fetchTelemetrySnapshot()
      .then((snapshot) => {
        if (cancelled) return;
        store.hydrate(snapshot);
        onHydrateRef.current?.(snapshot);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [hydrate, store]);

  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (disposed) return;
      const nextSocket = new WebSocket(buildWsUrl("/ws/telemetry"));
      socket = nextSocket;
      nextSocket.onopen = () => store.setConnection(true, false);
      nextSocket.onclose = () => {
        store.setConnection(false, false);
        if (!disposed && reconnectTimer === null) {
          reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            connect();
          }, TELEMETRY_RECONNECT_DELAY_MS);
        }
      };
      nextSocket.onerror = () => undefined;
      nextSocket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data) as TelemetryMessage;
          if (!message?.payload?.device_id) return;
          perfCount("telemetry.messages");
          perfCount(
            "telemetry.signal_values",
            Object.keys(message.payload.signals ?? {}).length
          );
          store.applyMessage(message);
          try {
            onMessageRef.current?.(message);
          } catch {
            // A consumer callback must not tear down the transport.
          }
        } catch {
          // Ignore malformed frames.
        }
      };
    };

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [store]);
}
