import { useCallback, useSyncExternalStore } from "react";

import { perfCount } from "../performance/perfInstrumentation";

export const DEFAULT_PLOT_MAX_FPS = 30;

type Listener = () => void;
type ScheduleFrame = (callback: FrameRequestCallback) => number;
type CancelFrame = (handle: number) => void;

export class PanelInvalidationStore {
  private readonly versions = new Map<string, number>();
  private readonly listeners = new Map<string, Set<Listener>>();
  private readonly dirty = new Set<string>();
  private readonly maxFps = new Map<string, number>();
  private readonly lastFlushAt = new Map<string, number>();
  private readonly visibility = new Map<string, Map<string, boolean>>();
  private frame: number | null = null;
  private hidden = false;

  constructor(
    private readonly scheduleFrame: ScheduleFrame = (callback) =>
      window.requestAnimationFrame(callback),
    private readonly cancelFrame: CancelFrame = (handle) =>
      window.cancelAnimationFrame(handle)
  ) {}

  getRevision = (panelId: string): number => this.versions.get(panelId) ?? 0;

  subscribe(panelId: string, listener: Listener): () => void {
    let current = this.listeners.get(panelId);
    if (!current) {
      current = new Set();
      this.listeners.set(panelId, current);
    }
    current.add(listener);
    return () => {
      current?.delete(listener);
      if (current?.size === 0) this.listeners.delete(panelId);
    };
  }

  markPanelDirty(panelId: string): void {
    this.markPanelsDirty([panelId]);
  }

  markPanelsDirty(panelIds: Iterable<string>): void {
    let added = false;
    for (const panelId of panelIds) {
      if (!panelId) continue;
      perfCount("plot.invalidation_requests");
      if (!this.dirty.has(panelId)) {
        this.dirty.add(panelId);
        added = true;
      }
    }
    if (added && !this.hidden) this.ensureFrame();
  }

  setPanelMaxFps(panelId: string, fps: number): void {
    this.maxFps.set(panelId, Math.max(1, Number(fps) || DEFAULT_PLOT_MAX_FPS));
  }

  setPanelVisible(panelId: string, source: string, visible: boolean): void {
    let sources = this.visibility.get(panelId);
    if (!sources) {
      sources = new Map();
      this.visibility.set(panelId, sources);
    }
    sources.set(source, visible);
    if (visible && this.dirty.has(panelId)) this.ensureFrame();
  }

  removeVisibilitySource(panelId: string, source: string): void {
    const sources = this.visibility.get(panelId);
    sources?.delete(source);
    if (sources?.size === 0) this.visibility.delete(panelId);
  }

  setHidden(hidden: boolean): void {
    if (this.hidden === hidden) return;
    this.hidden = hidden;
    if (hidden && this.frame !== null) {
      this.cancelFrame(this.frame);
      this.frame = null;
    } else if (!hidden) {
      // Force one catch-up render for mounted, visible representations even
      // when no message arrived during the hidden interval.
      this.markPanelsDirty(
        [...this.listeners.keys()].filter((panelId) => this.isVisible(panelId))
      );
      if (this.dirty.size > 0) this.ensureFrame();
    }
  }

  removePanel(panelId: string): void {
    this.dirty.delete(panelId);
    this.listeners.delete(panelId);
    this.versions.delete(panelId);
    this.maxFps.delete(panelId);
    this.lastFlushAt.delete(panelId);
    this.visibility.delete(panelId);
  }

  dispose(): void {
    if (this.frame !== null) this.cancelFrame(this.frame);
    this.frame = null;
    this.dirty.clear();
  }

  private ensureFrame(): void {
    if (this.frame !== null || this.hidden) return;
    this.frame = this.scheduleFrame((now) => this.flush(now));
  }

  private flush(now: number): void {
    this.frame = null;
    if (this.hidden) return;
    const notified: string[] = [];
    for (const panelId of [...this.dirty]) {
      if (!this.isVisible(panelId)) continue;
      const interval = 1000 / (this.maxFps.get(panelId) ?? DEFAULT_PLOT_MAX_FPS);
      const previous = this.lastFlushAt.get(panelId) ?? Number.NEGATIVE_INFINITY;
      if (now - previous + 0.01 < interval) continue;
      this.dirty.delete(panelId);
      this.lastFlushAt.set(panelId, now);
      this.versions.set(panelId, (this.versions.get(panelId) ?? 0) + 1);
      notified.push(panelId);
    }
    if (notified.length > 0) {
      perfCount("plot.flushes");
      perfCount("plot.dirty_panels_flushed", notified.length);
      for (const panelId of notified) {
        for (const listener of this.listeners.get(panelId) ?? []) listener();
      }
    }
    if ([...this.dirty].some((panelId) => this.isVisible(panelId))) this.ensureFrame();
  }

  private isVisible(panelId: string): boolean {
    const sources = this.visibility.get(panelId);
    return !sources || sources.size === 0 || [...sources.values()].some(Boolean);
  }
}

export const panelInvalidationStore = new PanelInvalidationStore();

if (typeof document !== "undefined") {
  panelInvalidationStore.setHidden(document.hidden);
  document.addEventListener("visibilitychange", () => {
    panelInvalidationStore.setHidden(document.hidden);
  });
}

export function markPanelDirty(panelId: string): void {
  panelInvalidationStore.markPanelDirty(panelId);
}

export function markPanelsDirty(panelIds: Iterable<string>): void {
  panelInvalidationStore.markPanelsDirty(panelIds);
}

export function usePanelRevision(panelId: string): number {
  const subscribe = useCallback(
    (listener: Listener) => panelInvalidationStore.subscribe(panelId, listener),
    [panelId]
  );
  return useSyncExternalStore(
    subscribe,
    () => panelInvalidationStore.getRevision(panelId),
    () => panelInvalidationStore.getRevision(panelId)
  );
}
