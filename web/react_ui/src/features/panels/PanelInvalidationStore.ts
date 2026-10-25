import {
  Fragment,
  createElement,
  useCallback,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import { perfCount } from "../performance/perfInstrumentation";

export const DEFAULT_PLOT_MAX_FPS = 30;
export const MIN_TIME_SERIES_FPS = 5;

/**
 * Redraw ceiling for a scrolling time-series plot: about one redraw per pixel
 * the x axis advances, between MIN_TIME_SERIES_FPS and DEFAULT_PLOT_MAX_FPS.
 * A 120 s window on a 400 px plot moves ~3 px/s, so redrawing it at the data
 * rate (often 20-30 Hz) repaints nearly identical frames.
 */
export function timeSeriesMaxFps(plotWidthPx: number, timeWindowS: number): number {
  if (!(plotWidthPx > 0) || !(timeWindowS > 0)) return DEFAULT_PLOT_MAX_FPS;
  const pxPerSecond = plotWidthPx / timeWindowS;
  return Math.min(DEFAULT_PLOT_MAX_FPS, Math.max(MIN_TIME_SERIES_FPS, Math.ceil(pxPerSecond)));
}

type Listener = () => void;
type ScheduleFrame = (callback: FrameRequestCallback) => number;
type CancelFrame = (handle: number) => void;
type ScheduleTimeout = (callback: () => void, delayMs: number) => number;
type CancelTimeout = (handle: number) => void;

// A rate-limited panel that is due further away than this waits on a timer
// instead of re-arming requestAnimationFrame every vsync until it is due.
const FRAME_WAIT_THRESHOLD_MS = 12;

export class PanelInvalidationStore {
  private readonly versions = new Map<string, number>();
  private readonly listeners = new Map<string, Set<Listener>>();
  private readonly dirty = new Set<string>();
  private readonly maxFps = new Map<string, number>();
  private readonly lastFlushAt = new Map<string, number>();
  private readonly visibility = new Map<string, Map<string, boolean>>();
  private frame: number | null = null;
  private timer: number | null = null;
  private hidden = false;

  constructor(
    private readonly scheduleFrame: ScheduleFrame = (callback) =>
      window.requestAnimationFrame(callback),
    private readonly cancelFrame: CancelFrame = (handle) =>
      window.cancelAnimationFrame(handle),
    private readonly scheduleTimeout: ScheduleTimeout = (callback, delayMs) =>
      window.setTimeout(callback, delayMs),
    private readonly cancelTimeout: CancelTimeout = (handle) =>
      window.clearTimeout(handle)
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
    if (hidden) {
      if (this.frame !== null) this.cancelFrame(this.frame);
      this.frame = null;
      this.clearTimer();
    } else {
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
    this.clearTimer();
    this.dirty.clear();
  }

  private clearTimer(): void {
    if (this.timer !== null) this.cancelTimeout(this.timer);
    this.timer = null;
  }

  private ensureFrame(): void {
    if (this.frame !== null || this.hidden) return;
    this.frame = this.scheduleFrame((now) => this.flush(now));
  }

  private flush(now: number): void {
    this.frame = null;
    if (this.hidden) return;
    this.clearTimer();
    const notified: string[] = [];
    let nextDueInMs = Number.POSITIVE_INFINITY;
    for (const panelId of [...this.dirty]) {
      if (!this.isVisible(panelId)) continue;
      const interval = 1000 / (this.maxFps.get(panelId) ?? DEFAULT_PLOT_MAX_FPS);
      const previous = this.lastFlushAt.get(panelId) ?? Number.NEGATIVE_INFINITY;
      const dueInMs = previous + interval - now;
      if (dueInMs > 0.01) {
        nextDueInMs = Math.min(nextDueInMs, dueInMs);
        continue;
      }
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
    if (nextDueInMs === Number.POSITIVE_INFINITY) return;
    if (nextDueInMs <= FRAME_WAIT_THRESHOLD_MS) {
      this.ensureFrame();
      return;
    }
    // Wake slightly early so the flush still lands on the frame it is due in.
    this.timer = this.scheduleTimeout(() => {
      this.timer = null;
      this.ensureFrame();
    }, nextDueInMs - FRAME_WAIT_THRESHOLD_MS / 2);
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

const NO_SUBSCRIPTION = () => () => undefined;

/**
 * The panel's data revision. With `enabled` false the component does not
 * re-render on data updates (it still reads the current revision when it
 * renders for other reasons).
 */
export function usePanelRevision(panelId: string, enabled = true): number {
  const subscribe = useCallback(
    (listener: Listener) => panelInvalidationStore.subscribe(panelId, listener),
    [panelId]
  );
  return useSyncExternalStore(
    enabled ? subscribe : NO_SUBSCRIPTION,
    () => panelInvalidationStore.getRevision(panelId),
    () => panelInvalidationStore.getRevision(panelId)
  );
}

/**
 * Re-renders only its render function on each data update of `panelId`, so
 * a card's header, settings and other chrome stay out of the per-frame path.
 * The function is the parent's latest closure: configuration comes from the
 * parent's last render and live data is read from refs when it runs.
 */
export function PanelRevisionBoundary({
  panelId,
  children,
}: {
  panelId: string;
  children: (revision: number) => ReactNode;
}) {
  const revision = usePanelRevision(panelId);
  return createElement(Fragment, null, children(revision));
}
