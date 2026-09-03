import { useSyncExternalStore } from "react";

import type { TelemetryMessage, TelemetrySignal } from "../../types";
import type { LatestSignals } from "./useTelemetryStream";

export type DeviceTelemetrySnapshot = Readonly<Record<string, TelemetrySignal>>;
export type TelemetryConnectionSnapshot = Readonly<{
  wsConnected: boolean;
  telemetryActive: boolean;
}>;

const EMPTY_DEVICE: DeviceTelemetrySnapshot = Object.freeze({});
const DISCONNECTED: TelemetryConnectionSnapshot = Object.freeze({
  wsConnected: false,
  telemetryActive: false,
});

export class TelemetryLatestStore {
  private readonly devices = new Map<string, DeviceTelemetrySnapshot>();
  private readonly deviceListeners = new Map<string, Set<() => void>>();
  private readonly connectionListeners = new Set<() => void>();
  private connection: TelemetryConnectionSnapshot = DISCONNECTED;
  private lastReceiptAt: number | null = null;

  getDevice = (deviceId: string): DeviceTelemetrySnapshot =>
    this.devices.get(deviceId) ?? EMPTY_DEVICE;

  getSignal(deviceId: string, signalName: string): TelemetrySignal | undefined {
    return this.devices.get(deviceId)?.[signalName];
  }

  getSignalNames(deviceId: string): string[] {
    return Object.keys(this.devices.get(deviceId) ?? EMPTY_DEVICE);
  }

  getLatestByDevice(): LatestSignals {
    return Object.fromEntries(this.devices) as LatestSignals;
  }

  getConnectionSnapshot = (): TelemetryConnectionSnapshot => this.connection;

  getLastReceiptAt(): number | null {
    return this.lastReceiptAt;
  }

  subscribeDevice(deviceId: string, listener: () => void): () => void {
    let listeners = this.deviceListeners.get(deviceId);
    if (!listeners) {
      listeners = new Set();
      this.deviceListeners.set(deviceId, listeners);
    }
    listeners.add(listener);
    return () => {
      listeners?.delete(listener);
      if (listeners?.size === 0) this.deviceListeners.delete(deviceId);
    };
  }

  subscribeConnection = (listener: () => void): (() => void) => {
    this.connectionListeners.add(listener);
    return () => this.connectionListeners.delete(listener);
  };

  hydrate(snapshot: LatestSignals): void {
    for (const [deviceId, signals] of Object.entries(snapshot)) {
      this.mergeDevice(deviceId, signals);
    }
  }

  applyMessage(message: TelemetryMessage, receiptAt = Date.now()): void {
    const deviceId = message.payload?.device_id;
    if (!deviceId) return;
    this.lastReceiptAt = receiptAt;
    this.mergeDevice(deviceId, message.payload.signals ?? {});
    this.setConnection(true, true);
  }

  setConnection(wsConnected: boolean, telemetryActive: boolean): void {
    if (
      this.connection.wsConnected === wsConnected &&
      this.connection.telemetryActive === telemetryActive
    ) {
      return;
    }
    this.connection = Object.freeze({ wsConnected, telemetryActive });
    for (const listener of this.connectionListeners) listener();
  }

  clear(): void {
    const changedIds = [...this.devices.keys()];
    this.devices.clear();
    this.lastReceiptAt = null;
    for (const deviceId of changedIds) this.notifyDevice(deviceId);
    this.setConnection(false, false);
  }

  private mergeDevice(
    deviceId: string,
    incoming: Readonly<Record<string, TelemetrySignal>>
  ): void {
    const entries = Object.entries(incoming);
    if (entries.length === 0) return;
    const previous = this.devices.get(deviceId) ?? EMPTY_DEVICE;
    let changed = false;
    const next: Record<string, TelemetrySignal> = { ...previous };
    for (const [name, signal] of entries) {
      if (next[name] !== signal) {
        next[name] = signal;
        changed = true;
      }
    }
    if (!changed) return;
    this.devices.set(deviceId, Object.freeze(next));
    this.notifyDevice(deviceId);
  }

  private notifyDevice(deviceId: string): void {
    for (const listener of this.deviceListeners.get(deviceId) ?? []) listener();
  }
}

export const telemetryLatestStore = new TelemetryLatestStore();

export function useDeviceTelemetry(
  deviceId: string,
  store: TelemetryLatestStore = telemetryLatestStore
): DeviceTelemetrySnapshot {
  return useSyncExternalStore(
    (listener) => store.subscribeDevice(deviceId, listener),
    () => store.getDevice(deviceId),
    () => store.getDevice(deviceId)
  );
}

export function useTelemetryConnectionState(
  store: TelemetryLatestStore = telemetryLatestStore
): TelemetryConnectionSnapshot {
  return useSyncExternalStore(
    store.subscribeConnection,
    store.getConnectionSnapshot,
    store.getConnectionSnapshot
  );
}
