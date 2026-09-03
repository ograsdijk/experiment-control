import { useCallback, useEffect, useSyncExternalStore } from "react";

import type { TelemetrySignal } from "../../types";
import { perfCount, perfSet } from "../performance/perfInstrumentation";
import {
  telemetryLatestStore,
  type TelemetryLatestStore,
} from "./TelemetryLatestStore";

/**
 * The authoritative latest-value store is deliberately unthrottled.  This
 * limit applies only to React-facing snapshots, where a fast device can
 * otherwise cause every visible text cell to render for every sample.
 */
export const DEVICE_TELEMETRY_MAX_FPS = 4;

type Listener = () => void;
type TimeoutHandle = ReturnType<typeof globalThis.setTimeout>;
type AnimationFrameHandle = number | TimeoutHandle;
type VisibilityDocument = Pick<
  Document,
  "hidden" | "addEventListener" | "removeEventListener"
>;

type PresentationSchedulerOptions = {
  now?: () => number;
  setTimeout?: (callback: () => void, delay: number) => TimeoutHandle;
  clearTimeout?: (handle: TimeoutHandle) => void;
  requestAnimationFrame?: (callback: FrameRequestCallback) => AnimationFrameHandle;
  cancelAnimationFrame?: (handle: AnimationFrameHandle) => void;
  document?: VisibilityDocument;
};

export type DeviceTelemetryPresentationStoreOptions =
  PresentationSchedulerOptions & {
    latestStore?: TelemetryLatestStore;
    maxFps?: number;
  };

type DevicePresentationState = {
  readonly listeners: Map<string, Set<Listener>>;
  readonly retainedSignals: Map<string, number>;
  readonly snapshots: Map<string, TelemetrySignal | undefined>;
  readonly dirtySignals: Set<string>;
  sourceUnsubscribe: (() => void) | null;
  lastFlushAt: number | null;
};

const noopUnsubscribe = () => {};

function browserDocument(): VisibilityDocument | undefined {
  return typeof document === "undefined" ? undefined : document;
}

/**
 * Converts immediate telemetry-store changes into paced, narrow snapshots for
 * display.  One source subscription serves all active signal cells of a
 * device, while each cell receives notifications only for its own signal.
 */
export class DeviceTelemetryPresentationStore {
  private readonly latestStore: TelemetryLatestStore;
  private readonly intervalMs: number;
  private readonly now: () => number;
  private readonly scheduleTimeout: (
    callback: () => void,
    delay: number
  ) => TimeoutHandle;
  private readonly cancelTimeout: (handle: TimeoutHandle) => void;
  private readonly scheduleAnimationFrame: (
    callback: FrameRequestCallback
  ) => AnimationFrameHandle;
  private readonly cancelAnimationFrame: (
    handle: AnimationFrameHandle
  ) => void;
  private readonly visibilityDocument: VisibilityDocument | undefined;
  private readonly devices = new Map<string, DevicePresentationState>();

  private timer: TimeoutHandle | null = null;
  private timerDueAt: number | null = null;
  private animationFrame: AnimationFrameHandle | null = null;
  private listeningForVisibility = false;
  private activeDeviceSubscriptions = 0;
  private activeSignalSubscriptions = 0;

  constructor(options: DeviceTelemetryPresentationStoreOptions = {}) {
    this.latestStore = options.latestStore ?? telemetryLatestStore;
    const maxFps = options.maxFps ?? DEVICE_TELEMETRY_MAX_FPS;
    this.intervalMs = 1000 / Math.max(1, maxFps);
    this.now = options.now ?? (() => Date.now());
    const setTimeoutImpl = options.setTimeout ?? globalThis.setTimeout;
    const clearTimeoutImpl = options.clearTimeout ?? globalThis.clearTimeout;
    // Wrap host functions so calling the stored callback as an instance member
    // cannot supply this store as the native Window receiver. Chromium rejects
    // an unbound Window.setTimeout with "Illegal invocation".
    this.scheduleTimeout = (callback, delay) => setTimeoutImpl(callback, delay);
    this.cancelTimeout = (handle) => clearTimeoutImpl(handle);
    const requestAnimationFrameImpl =
      options.requestAnimationFrame ??
      ((callback: FrameRequestCallback) => {
        if (typeof globalThis.requestAnimationFrame === "function") {
          return globalThis.requestAnimationFrame(callback);
        }
        return this.scheduleTimeout(() => callback(this.now()), 0);
      });
    const cancelAnimationFrameImpl =
      options.cancelAnimationFrame ??
      ((handle: AnimationFrameHandle) => {
        if (typeof globalThis.cancelAnimationFrame === "function") {
          globalThis.cancelAnimationFrame(handle as number);
        } else {
          this.cancelTimeout(handle as TimeoutHandle);
        }
      });
    this.scheduleAnimationFrame = (callback) =>
      requestAnimationFrameImpl(callback);
    this.cancelAnimationFrame = (handle) =>
      cancelAnimationFrameImpl(handle);
    this.visibilityDocument = options.document ?? browserDocument();
  }

  getDisplayedSignal = (
    deviceId: string,
    signalName: string
  ): TelemetrySignal | undefined => {
    const state = this.devices.get(deviceId);
    if (state?.snapshots.has(signalName)) {
      return state.snapshots.get(signalName);
    }
    return this.latestStore.getSignal(deviceId, signalName);
  };

  /** Keep an offscreen cell's last display snapshot without live updates. */
  retainSignal(deviceId: string, signalName: string): () => void {
    const state = this.getOrCreateDevice(deviceId);
    state.retainedSignals.set(
      signalName,
      (state.retainedSignals.get(signalName) ?? 0) + 1
    );
    if (!state.snapshots.has(signalName)) {
      state.snapshots.set(
        signalName,
        this.latestStore.getSignal(deviceId, signalName)
      );
    }
    return () => {
      const retained = state.retainedSignals.get(signalName) ?? 0;
      if (retained <= 1) {
        state.retainedSignals.delete(signalName);
        this.removeUnusedSignal(state, signalName);
      } else {
        state.retainedSignals.set(signalName, retained - 1);
      }
      this.removeDeviceIfUnused(deviceId, state);
    };
  }

  subscribeSignal(
    deviceId: string,
    signalName: string,
    listener: Listener
  ): () => void {
    const state = this.getOrCreateDevice(deviceId);
    const hadActiveListeners = this.hasActiveListeners(state);
    let listeners = state.listeners.get(signalName);
    if (!listeners) {
      listeners = new Set();
      state.listeners.set(signalName, listeners);
    }
    if (!listeners.has(listener)) {
      listeners.add(listener);
      this.activeSignalSubscriptions += 1;
      perfSet(
        "telemetry_ui.active_signal_subscriptions",
        this.activeSignalSubscriptions
      );
    }
    if (!hadActiveListeners) {
      state.lastFlushAt = this.now();
      state.sourceUnsubscribe = this.latestStore.subscribeDeviceSignalChanges(
        deviceId,
        (changedSignalNames) =>
          this.handleSourceUpdate(deviceId, changedSignalNames)
      );
      this.activeDeviceSubscriptions += 1;
      perfSet(
        "telemetry_ui.active_device_subscriptions",
        this.activeDeviceSubscriptions
      );
      this.ensureVisibilityListener();
    }
    this.publishLatestSignal(deviceId, state, signalName);

    return () => {
      if (listeners?.delete(listener)) {
        this.activeSignalSubscriptions -= 1;
        perfSet(
          "telemetry_ui.active_signal_subscriptions",
          this.activeSignalSubscriptions
        );
      }
      if (listeners?.size === 0) {
        state.listeners.delete(signalName);
        state.dirtySignals.delete(signalName);
        this.removeUnusedSignal(state, signalName);
      }
      if (!this.hasActiveListeners(state)) {
        state.sourceUnsubscribe?.();
        if (state.sourceUnsubscribe) {
          this.activeDeviceSubscriptions -= 1;
          perfSet(
            "telemetry_ui.active_device_subscriptions",
            this.activeDeviceSubscriptions
          );
        }
        state.sourceUnsubscribe = null;
        this.removeVisibilityListenerIfUnused();
      }
      this.removeDeviceIfUnused(deviceId, state);
      this.cancelSchedulerIfIdle();
    };
  }

  dispose(): void {
    for (const state of this.devices.values()) {
      state.sourceUnsubscribe?.();
      state.listeners.clear();
      state.retainedSignals.clear();
    }
    this.devices.clear();
    this.activeDeviceSubscriptions = 0;
    this.activeSignalSubscriptions = 0;
    perfSet("telemetry_ui.active_device_subscriptions", 0);
    perfSet("telemetry_ui.active_signal_subscriptions", 0);
    this.cancelPendingScheduler();
    if (this.listeningForVisibility && this.visibilityDocument) {
      this.visibilityDocument.removeEventListener(
        "visibilitychange",
        this.handleVisibilityChange
      );
      this.listeningForVisibility = false;
    }
  }

  /** Exposed only for deterministic lifecycle tests and diagnostics. */
  getDebugState(): {
    activeDeviceSubscriptions: number;
    activeSignalSubscriptions: number;
    retainedSignals: number;
    pendingSchedulerWork: boolean;
  } {
    let activeDeviceSubscriptions = 0;
    let activeSignalSubscriptions = 0;
    let retainedSignals = 0;
    for (const state of this.devices.values()) {
      if (state.sourceUnsubscribe) activeDeviceSubscriptions += 1;
      for (const listeners of state.listeners.values()) {
        activeSignalSubscriptions += listeners.size;
      }
      for (const count of state.retainedSignals.values()) retainedSignals += count;
    }
    return {
      activeDeviceSubscriptions,
      activeSignalSubscriptions,
      retainedSignals,
      pendingSchedulerWork: this.timer !== null || this.animationFrame !== null,
    };
  }

  private getOrCreateDevice(deviceId: string): DevicePresentationState {
    let state = this.devices.get(deviceId);
    if (!state) {
      state = {
        listeners: new Map(),
        retainedSignals: new Map(),
        snapshots: new Map(),
        dirtySignals: new Set(),
        sourceUnsubscribe: null,
        lastFlushAt: null,
      };
      this.devices.set(deviceId, state);
    }
    return state;
  }

  private hasActiveListeners(state: DevicePresentationState): boolean {
    return state.listeners.size > 0;
  }

  private removeUnusedSignal(
    state: DevicePresentationState,
    signalName: string
  ): void {
    if (
      !state.listeners.has(signalName) &&
      !state.retainedSignals.has(signalName)
    ) {
      state.snapshots.delete(signalName);
      state.dirtySignals.delete(signalName);
    }
  }

  private removeDeviceIfUnused(
    deviceId: string,
    state: DevicePresentationState
  ): void {
    if (
      !this.hasActiveListeners(state) &&
      state.retainedSignals.size === 0
    ) {
      this.devices.delete(deviceId);
    }
  }

  private handleSourceUpdate(
    deviceId: string,
    changedSignalNames: readonly string[]
  ): void {
    const state = this.devices.get(deviceId);
    if (!state) return;
    perfCount("telemetry_ui.source_updates");
    for (const signalName of changedSignalNames) {
      if (!state.listeners.has(signalName)) continue;
      if (
        this.latestStore.getSignal(deviceId, signalName) !==
        state.snapshots.get(signalName)
      ) {
        state.dirtySignals.add(signalName);
      }
    }
    if (state.dirtySignals.size > 0) this.scheduleFlush();
  }

  private publishLatestSignal(
    deviceId: string,
    state: DevicePresentationState,
    signalName: string
  ): boolean {
    const next = this.latestStore.getSignal(deviceId, signalName);
    if (state.snapshots.has(signalName) && state.snapshots.get(signalName) === next) {
      return false;
    }
    state.snapshots.set(signalName, next);
    for (const listener of state.listeners.get(signalName) ?? []) listener();
    return true;
  }

  private scheduleFlush(): void {
    if (this.isDocumentHidden() || this.animationFrame !== null) return;
    const now = this.now();
    let earliestDueAt: number | null = null;
    for (const state of this.devices.values()) {
      if (state.dirtySignals.size === 0) continue;
      const dueAt = state.lastFlushAt === null
        ? now
        : state.lastFlushAt + this.intervalMs;
      earliestDueAt = earliestDueAt === null ? dueAt : Math.min(earliestDueAt, dueAt);
    }
    if (earliestDueAt === null) return;
    if (this.timer !== null && this.timerDueAt !== null && this.timerDueAt <= earliestDueAt) {
      return;
    }
    if (this.timer !== null) this.cancelTimeout(this.timer);
    this.timerDueAt = earliestDueAt;
    this.timer = this.scheduleTimeout(() => {
      this.timer = null;
      this.timerDueAt = null;
      if (this.isDocumentHidden() || this.animationFrame !== null) return;
      let animationFrameRan = false;
      const animationFrame = this.scheduleAnimationFrame(() => {
        animationFrameRan = true;
        this.animationFrame = null;
        this.flushDirtySignals();
      });
      // Test schedulers may invoke RAF synchronously; do not leave their
      // completed handle marked as outstanding in that case.
      this.animationFrame = animationFrameRan ? null : animationFrame;
    }, Math.max(0, earliestDueAt - now));
  }

  private flushDirtySignals(): void {
    if (this.isDocumentHidden()) return;
    const now = this.now();
    let flushedDevices = 0;
    for (const [deviceId, state] of this.devices) {
      if (state.dirtySignals.size === 0) continue;
      const dueAt = state.lastFlushAt === null
        ? now
        : state.lastFlushAt + this.intervalMs;
      if (now < dueAt) continue;
      const dirtySignals = [...state.dirtySignals];
      state.dirtySignals.clear();
      state.lastFlushAt = now;
      flushedDevices += 1;
      for (const signalName of dirtySignals) {
        if (state.listeners.has(signalName)) {
          this.publishLatestSignal(deviceId, state, signalName);
        }
      }
    }
    if (flushedDevices > 0) {
      perfCount("telemetry_ui.flushes");
      perfCount("telemetry_ui.dirty_devices_flushed", flushedDevices);
    }
    this.scheduleFlush();
  }

  private ensureVisibilityListener(): void {
    if (this.listeningForVisibility || !this.visibilityDocument) return;
    this.visibilityDocument.addEventListener(
      "visibilitychange",
      this.handleVisibilityChange
    );
    this.listeningForVisibility = true;
  }

  private removeVisibilityListenerIfUnused(): void {
    if (
      !this.listeningForVisibility ||
      !this.visibilityDocument ||
      [...this.devices.values()].some((state) => this.hasActiveListeners(state))
    ) {
      return;
    }
    this.visibilityDocument.removeEventListener(
      "visibilitychange",
      this.handleVisibilityChange
    );
    this.listeningForVisibility = false;
  }

  private readonly handleVisibilityChange = (): void => {
    if (this.isDocumentHidden()) {
      this.cancelPendingScheduler();
      return;
    }
    let catchups = 0;
    for (const [deviceId, state] of this.devices) {
      if (!this.hasActiveListeners(state)) continue;
      let changed = false;
      for (const signalName of state.listeners.keys()) {
        changed = this.publishLatestSignal(deviceId, state, signalName) || changed;
      }
      state.dirtySignals.clear();
      state.lastFlushAt = this.now();
      if (changed) catchups += 1;
    }
    if (catchups > 0) perfCount("telemetry_ui.visibility_catchups", catchups);
  };

  private isDocumentHidden(): boolean {
    return this.visibilityDocument?.hidden === true;
  }

  private cancelSchedulerIfIdle(): void {
    if ([...this.devices.values()].some((state) => state.dirtySignals.size > 0)) {
      return;
    }
    this.cancelPendingScheduler();
  }

  private cancelPendingScheduler(): void {
    if (this.timer !== null) this.cancelTimeout(this.timer);
    if (this.animationFrame !== null) {
      this.cancelAnimationFrame(this.animationFrame);
    }
    this.timer = null;
    this.timerDueAt = null;
    this.animationFrame = null;
  }
}

export const deviceTelemetryPresentationStore =
  new DeviceTelemetryPresentationStore();

export function useDisplayedTelemetrySignal(
  deviceId: string,
  signalName: string,
  enabled = true,
  store: DeviceTelemetryPresentationStore = deviceTelemetryPresentationStore
): TelemetrySignal | undefined {
  useEffect(
    () => store.retainSignal(deviceId, signalName),
    [deviceId, signalName, store]
  );
  const subscribe = useCallback(
    (listener: Listener) =>
      enabled
        ? store.subscribeSignal(deviceId, signalName, listener)
        : noopUnsubscribe,
    [deviceId, enabled, signalName, store]
  );
  const getSnapshot = useCallback(
    () => store.getDisplayedSignal(deviceId, signalName),
    [deviceId, signalName, store]
  );
  return useSyncExternalStore(
    subscribe,
    getSnapshot,
    getSnapshot
  );
}
