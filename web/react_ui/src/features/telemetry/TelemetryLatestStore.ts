import { useCallback, useSyncExternalStore } from "react";

import type { TelemetryMessage, TelemetrySignal } from "../../types";
import type { LatestSignals } from "./useTelemetryStream";

export type DeviceTelemetrySnapshot = Readonly<Record<string, TelemetrySignal>>;
export type DeviceTelemetrySignalNames = readonly string[];
type DeviceSignalChangeListener = (
  changedSignalNames: readonly string[]
) => void;
export type TelemetryConnectionSnapshot = Readonly<{
  wsConnected: boolean;
  telemetryActive: boolean;
}>;

const EMPTY_DEVICE: DeviceTelemetrySnapshot = Object.freeze({});
const EMPTY_SIGNAL_NAMES: DeviceTelemetrySignalNames = Object.freeze([]);
const noopUnsubscribe = () => {};
const DISCONNECTED: TelemetryConnectionSnapshot = Object.freeze({
  wsConnected: false,
  telemetryActive: false,
});

export class TelemetryLatestStore {
  private readonly devices = new Map<string, DeviceTelemetrySnapshot>();
  private readonly deviceListeners = new Map<string, Set<() => void>>();
  private readonly deviceSignalListeners = new Map<
    string,
    Set<DeviceSignalChangeListener>
  >();
  private readonly signalNameSnapshots = new Map<
    string,
    DeviceTelemetrySignalNames
  >();
  private readonly signalNameListeners = new Map<string, Set<() => void>>();
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

  getSignalNamesSnapshot = (deviceId: string): DeviceTelemetrySignalNames =>
    this.signalNameSnapshots.get(deviceId) ?? EMPTY_SIGNAL_NAMES;

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

  /**
   * A narrow companion to subscribeDevice for consumers that can avoid
   * inspecting an entire device snapshot on every incoming message.
   */
  subscribeDeviceSignalChanges(
    deviceId: string,
    listener: DeviceSignalChangeListener
  ): () => void {
    let listeners = this.deviceSignalListeners.get(deviceId);
    if (!listeners) {
      listeners = new Set();
      this.deviceSignalListeners.set(deviceId, listeners);
    }
    listeners.add(listener);
    return () => {
      listeners?.delete(listener);
      if (listeners?.size === 0) this.deviceSignalListeners.delete(deviceId);
    };
  }

  subscribeSignalNames(deviceId: string, listener: () => void): () => void {
    let listeners = this.signalNameListeners.get(deviceId);
    if (!listeners) {
      listeners = new Set();
      this.signalNameListeners.set(deviceId, listeners);
    }
    listeners.add(listener);
    return () => {
      listeners?.delete(listener);
      if (listeners?.size === 0) this.signalNameListeners.delete(deviceId);
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
    const clearedSignalNames = new Map(
      [...this.devices].map(([deviceId, signals]) => [
        deviceId,
        Object.keys(signals),
      ])
    );
    const changedIds = new Set([
      ...this.devices.keys(),
      ...this.signalNameSnapshots.keys(),
    ]);
    this.devices.clear();
    this.signalNameSnapshots.clear();
    this.lastReceiptAt = null;
    for (const deviceId of changedIds) {
      this.notifyDevice(deviceId);
      this.notifyDeviceSignalChanges(
        deviceId,
        clearedSignalNames.get(deviceId) ?? EMPTY_SIGNAL_NAMES
      );
      this.notifySignalNames(deviceId);
    }
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
    let signalNamesChanged = false;
    const changedSignalNames = this.deviceSignalListeners.has(deviceId)
      ? [] as string[]
      : null;
    const next: Record<string, TelemetrySignal> = { ...previous };
    for (const [name, signal] of entries) {
      if (next[name] !== signal) {
        next[name] = signal;
        changed = true;
        changedSignalNames?.push(name);
      }
      if (!(name in previous)) signalNamesChanged = true;
    }
    if (!changed) return;
    this.devices.set(deviceId, Object.freeze(next));
    if (signalNamesChanged) {
      this.signalNameSnapshots.set(
        deviceId,
        Object.freeze(Object.keys(next).sort((a, b) => a.localeCompare(b)))
      );
    }
    this.notifyDevice(deviceId);
    if (changedSignalNames) {
      this.notifyDeviceSignalChanges(deviceId, changedSignalNames);
    }
    if (signalNamesChanged) this.notifySignalNames(deviceId);
  }

  private notifyDevice(deviceId: string): void {
    for (const listener of this.deviceListeners.get(deviceId) ?? []) listener();
  }

  private notifyDeviceSignalChanges(
    deviceId: string,
    changedSignalNames: readonly string[]
  ): void {
    for (const listener of this.deviceSignalListeners.get(deviceId) ?? []) {
      listener(changedSignalNames);
    }
  }

  private notifySignalNames(deviceId: string): void {
    for (const listener of this.signalNameListeners.get(deviceId) ?? []) listener();
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

export function useDeviceTelemetrySignalNames(
  deviceId: string,
  enabled = true,
  store: TelemetryLatestStore = telemetryLatestStore
): DeviceTelemetrySignalNames {
  const subscribe = useCallback(
    (listener: () => void) =>
      enabled ? store.subscribeSignalNames(deviceId, listener) : noopUnsubscribe,
    [deviceId, enabled, store]
  );
  const getSnapshot = useCallback(
    () => store.getSignalNamesSnapshot(deviceId),
    [deviceId, store]
  );
  return useSyncExternalStore(
    subscribe,
    getSnapshot,
    getSnapshot
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
