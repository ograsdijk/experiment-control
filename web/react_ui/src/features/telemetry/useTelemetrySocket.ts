import { useEffect, useRef } from "react";

import { buildWsUrl, fetchTelemetrySnapshot } from "../../api";
import type { TelemetryMessage } from "../../types";
import { openReconnectingSocket } from "../common/reconnectingSocket";
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

/** Base reconnect delay; later attempts back off exponentially with jitter. */
export const TELEMETRY_RECONNECT_DELAY_MS = 1000;

/** Low-level telemetry transport. It never stores message data in React state. */
export function useTelemetrySocket(options: UseTelemetrySocketOptions = {}): void {
  const { hydrate = true, store = telemetryLatestStore, onMessage, onHydrate } = options;
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;
  const onHydrateRef = useRef(onHydrate);
  onHydrateRef.current = onHydrate;
  const hydrateRef = useRef(hydrate);
  hydrateRef.current = hydrate;

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
    // Values that changed while the socket was down are not replayed by
    // the stream; refetch the snapshot after every reconnect. The store's
    // hydrate keeps any newer live value that arrived meanwhile.
    const rehydrate = () => {
      if (!hydrateRef.current) return;
      void fetchTelemetrySnapshot()
        .then((snapshot) => {
          if (disposed) return;
          store.hydrate(snapshot);
          onHydrateRef.current?.(snapshot);
        })
        .catch(() => undefined);
    };
    const socket = openReconnectingSocket({
      url: buildWsUrl("/ws/telemetry"),
      backoff: { baseMs: TELEMETRY_RECONNECT_DELAY_MS },
      onOpen: ({ reconnect }) => {
        store.setConnection(true, false);
        if (reconnect) rehydrate();
      },
      onClose: () => store.setConnection(false, false),
      onMessage: (event) => {
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
      },
    });
    return () => {
      disposed = true;
      socket.close();
      store.setConnection(false, false);
    };
  }, [store]);
}
