import type { MutableRefObject } from "react";

import {
  normalizeFitCurveValue,
  normalizeFitParamsMapValue,
  normalizeHist2dValue,
  normalizeHistAggValue,
  normalizeTraceValues,
  type normalizeStreamAnalysisOutputMessage,
} from "../stream/messages";
import {
  isStreamBin2dPanel,
  isStreamBinStatsPanel,
  isStreamParamsPanel,
  isStreamScalarPanel,
  isStreamTracePanel,
  streamScalarTrace,
} from "../stream/panel_helpers";
import type {
  PlotPanelState,
  RawStreamSubscription,
  StreamBin2dSnapshot,
  StreamBinStatsSnapshot,
  StreamFitCurveSnapshot,
  StreamFrameSample,
  StreamParamsOutputValue,
  StreamTraceAverageMode,
  StreamTraceDecimator,
} from "../stream/types";
import {
  normalizeTraceAverageMode,
  normalizeTraceDecimator,
  normalizeTraceMaxFps,
  normalizeTraceMaxPoints,
  normalizeTraceRollingWindow,
  traceKeyId,
} from "../stream/utils";
import { RingBuffer } from "../../utils/ringBuffer";

/**
 * Pure (controlled-mutation) helpers that route WS-arriving stream
 * data onto the per-panel plot buffers and overlay caches.
 *
 * These were defined inline in App.tsx as arrow functions closing over
 * panelsRef + the 8 telemetry/overlay refs that TelemetryContext owns.
 * Moving them out into a standalone module makes them testable in
 * isolation and lets the next round (panel handlers extraction) drop
 * one of App.tsx's largest blocks (~280 LOC).
 *
 * **Mutation semantics**: each helper writes only to the refs in the
 * provided `deps` object. They return `true` when at least one panel
 * received an update so the caller knows to bump `plotTick` and
 * trigger a re-render.
 *
 * **Read semantics**: both helpers iterate `deps.panelsRef.current` —
 * the state-mirror ref that PanelsContext keeps in sync with the
 * `panels` state. This avoids capturing a stale snapshot when the
 * helper is called from async callbacks.
 */

export const MAX_STREAM_FRAME_BUFFER = 240;
export const DEFAULT_BUFFER_POINTS = 500;

export function panelCapacity(timeWindow: number): number {
  return Math.max(DEFAULT_BUFFER_POINTS, Math.floor(timeWindow * 10));
}

export interface ApplyHelpersDeps {
  panelsRef: MutableRefObject<PlotPanelState[]>;
  /**
   * Reverse index for stream-analysis output dispatch.
   *
   * Keyed by `workspaceId → outputId → panels[]`. Each panel appears
   * under every output it cares about: primary outputId, every
   * overlay outputId (trace panels + bin-stats panels), every fit-
   * overlay outputId (bin-stats panels), and every params
   * subscription outputId.
   *
   * Built and updated by `useStreamOutputIndex` whenever the panels
   * list changes. `applyStreamAnalysisOutputToPanels` reads
   * `panelsByWorkspaceOutputRef.current.get(workspaceId)?.get(outputId)`
   * for an O(matching) loop instead of O(all panels) per WS message.
   */
  panelsByWorkspaceOutputRef: MutableRefObject<
    Map<string, Map<string, PlotPanelState[]>>
  >;
  buffersRef: Map<string, Map<string, RingBuffer>>;
  streamFramesRef: Map<string, StreamFrameSample[]>;
  streamTraceOverlayRef: Map<
    string,
    Map<string, { seq: number; values: number[] }>
  >;
  streamBinStatsOverlayRef: Map<
    string,
    Map<string, { seq: number; values: number[] }>
  >;
  streamBinStatsFitOverlayRef: Map<
    string,
    Map<string, StreamFitCurveSnapshot>
  >;
  streamParamsLatestRef: Map<string, Record<string, StreamParamsOutputValue>>;
  streamBinStatsRef: Map<string, StreamBinStatsSnapshot>;
  streamBin2dRef: Map<string, StreamBin2dSnapshot>;
}

/**
 * Build the (workspaceId, outputId) → panels[] reverse index from
 * the current panels list. Each panel appears under every output it
 * references in any role (primary / overlay / fit-overlay / params
 * subscription); the apply helpers do the role-check inline.
 */
export function buildPanelsByWorkspaceOutput(
  panels: PlotPanelState[]
): Map<string, Map<string, PlotPanelState[]>> {
  const out = new Map<string, Map<string, PlotPanelState[]>>();
  const add = (workspaceId: string, outputId: string, panel: PlotPanelState) => {
    if (!workspaceId || !outputId) {
      return;
    }
    let inner = out.get(workspaceId);
    if (!inner) {
      inner = new Map();
      out.set(workspaceId, inner);
    }
    let bucket = inner.get(outputId);
    if (!bucket) {
      bucket = [];
      inner.set(outputId, bucket);
    }
    bucket.push(panel);
  };
  for (const panel of panels) {
    const workspaceId =
      "workspaceId" in panel ? String(panel.workspaceId ?? "").trim() : "";
    if (!workspaceId) {
      continue;
    }
    if (isStreamScalarPanel(panel)) {
      add(workspaceId, String(panel.outputId ?? "").trim(), panel);
      continue;
    }
    if (isStreamParamsPanel(panel)) {
      for (const id of panel.outputIds ?? []) {
        add(workspaceId, String(id ?? "").trim(), panel);
      }
      continue;
    }
    if (isStreamBinStatsPanel(panel)) {
      add(workspaceId, String(panel.outputId ?? "").trim(), panel);
      for (const id of panel.overlayOutputIds ?? []) {
        add(workspaceId, String(id ?? "").trim(), panel);
      }
      for (const id of panel.fitOverlayOutputIds ?? []) {
        add(workspaceId, String(id ?? "").trim(), panel);
      }
      continue;
    }
    if (isStreamBin2dPanel(panel)) {
      add(workspaceId, String(panel.outputId ?? "").trim(), panel);
      continue;
    }
    if (isStreamTracePanel(panel) && panel.sourceMode === "dag") {
      add(workspaceId, String(panel.outputId ?? "").trim(), panel);
      for (const id of panel.overlayOutputIds ?? []) {
        add(workspaceId, String(id ?? "").trim(), panel);
      }
    }
  }
  return out;
}

export function ensurePanelBuffers(
  buffersRef: ApplyHelpersDeps["buffersRef"],
  panelId: string
): Map<string, RingBuffer> {
  let panelBuffers = buffersRef.get(panelId);
  if (!panelBuffers) {
    panelBuffers = new Map<string, RingBuffer>();
    buffersRef.set(panelId, panelBuffers);
  }
  return panelBuffers;
}

export function applyRawStreamFrameToPanels(
  deps: ApplyHelpersDeps,
  subscription: RawStreamSubscription,
  frame: {
    seq: number;
    shape: number[];
    values: unknown;
  }
): boolean {
  let updated = false;
  for (const panel of deps.panelsRef.current) {
    if (
      !isStreamTracePanel(panel) ||
      panel.sourceMode !== "raw" ||
      panel.stream === null
    ) {
      continue;
    }
    if (
      panel.stream.deviceId !== subscription.deviceId ||
      panel.stream.stream !== subscription.stream
    ) {
      continue;
    }
    if (Math.max(0, Math.trunc(panel.channelIndex)) !== subscription.channelIndex) {
      continue;
    }
    if (normalizeTraceDecimator(panel.traceDecimator) !== subscription.traceDecimator) {
      continue;
    }
    if (normalizeTraceMaxPoints(panel.traceMaxPoints) !== subscription.traceMaxPoints) {
      continue;
    }
    if (normalizeTraceMaxFps(panel.traceMaxFps) !== subscription.traceMaxFps) {
      continue;
    }
    if (normalizeTraceRollingWindow(panel.rollingWindow) !== subscription.rollingWindow) {
      continue;
    }
    if (normalizeTraceAverageMode(panel.averageMode) !== subscription.averageMode) {
      continue;
    }
    let currentFrames = deps.streamFramesRef.get(panel.id);
    if (!currentFrames) {
      currentFrames = [];
      deps.streamFramesRef.set(panel.id, currentFrames);
    }
    if (
      currentFrames.length > 0 &&
      currentFrames[currentFrames.length - 1].seq === frame.seq
    ) {
      continue;
    }
    // PerfD: mutate the frames array in place to avoid the spread+
    // slice double-allocation per WS message. Consumers re-render via
    // plotTick anyway; their useMemos re-fire on tick changes, not on
    // frames-array identity.
    currentFrames.push({
      seq: frame.seq,
      shape: frame.shape,
      values: frame.values,
    });
    const keep = Math.max(MAX_STREAM_FRAME_BUFFER, panel.overlayCount * 4);
    if (currentFrames.length > keep) {
      currentFrames.splice(0, currentFrames.length - keep);
    }
    updated = true;
  }
  return updated;
}

export function applyStreamAnalysisOutputToPanels(
  deps: ApplyHelpersDeps,
  output: NonNullable<ReturnType<typeof normalizeStreamAnalysisOutputMessage>>,
  traceFilter?: {
    traceDecimator: StreamTraceDecimator;
    traceMaxPoints: number;
    traceMaxFps: number;
    traceRollingWindow: number;
    traceAverageMode: StreamTraceAverageMode;
  } | undefined
): boolean {
  // PerfC: O(matching) loop via reverse index instead of O(N panels).
  // `interested` lists every panel that referenced this exact
  // (workspaceId, outputId) at the last index build (panel add/edit/
  // remove). The per-panel role-check below stays — it determines
  // which mutation path the output drives (scalar push, params
  // update, hist replace, trace primary/overlay, fit overlay).
  const interested = deps.panelsByWorkspaceOutputRef.current
    .get(output.workspaceId)
    ?.get(output.outputId);
  if (!interested || interested.length === 0) {
    return false;
  }
  let updated = false;
  if (output.kind === "scalar") {
    const scalar = Number(output.value);
    if (Number.isFinite(scalar)) {
      for (const panel of interested) {
        if (isStreamScalarPanel(panel)) {
          const panelBuffers = ensurePanelBuffers(deps.buffersRef, panel.id);
          const key = traceKeyId(streamScalarTrace(panel));
          let buffer = panelBuffers.get(key);
          if (!buffer) {
            buffer = new RingBuffer(panelCapacity(panel.timeWindowS));
            panelBuffers.set(key, buffer);
          }
          buffer.push(output.tWallS, scalar);
          updated = true;
          continue;
        }
        if (isStreamParamsPanel(panel)) {
          const latest = deps.streamParamsLatestRef.get(panel.id) ?? {};
          latest[output.outputId] = scalar;
          deps.streamParamsLatestRef.set(panel.id, latest);
          updated = true;
        }
      }
    }
    return updated;
  }
  if (output.kind === "params_map") {
    const paramsMap = normalizeFitParamsMapValue(output.value);
    if (paramsMap) {
      for (const panel of interested) {
        if (!isStreamParamsPanel(panel)) {
          continue;
        }
        const latest = deps.streamParamsLatestRef.get(panel.id) ?? {};
        latest[output.outputId] = paramsMap;
        deps.streamParamsLatestRef.set(panel.id, latest);
        updated = true;
      }
    }
    return updated;
  }
  if (output.kind === "hist_agg") {
    const series = normalizeHistAggValue(output.value);
    if (series) {
      for (const panel of interested) {
        if (!isStreamBinStatsPanel(panel)) {
          continue;
        }
        if ((panel.outputId ?? "") !== output.outputId) {
          // Index match was via overlay/fit-overlay; not the primary
          // bin-stats output, so skip the snapshot replace.
          continue;
        }
        deps.streamBinStatsRef.set(panel.id, series);
        updated = true;
      }
    }
    return updated;
  }
  if (output.kind === "hist2d") {
    const snapshot = normalizeHist2dValue(output.value);
    if (snapshot) {
      for (const panel of interested) {
        if (!isStreamBin2dPanel(panel)) {
          continue;
        }
        deps.streamBin2dRef.set(panel.id, snapshot);
        updated = true;
      }
    }
    return updated;
  }
  if (output.kind === "fit_1d") {
    const fit = normalizeFitCurveValue(output.value);
    if (fit) {
      for (const panel of interested) {
        if (!isStreamBinStatsPanel(panel)) {
          continue;
        }
        // Must be a fit-overlay; index lookup matched, but skip if
        // the panel only listed it as primary or trace overlay.
        if (
          !(panel.fitOverlayOutputIds ?? []).includes(output.outputId)
        ) {
          continue;
        }
        const perPanel =
          deps.streamBinStatsFitOverlayRef.get(panel.id) ?? new Map();
        perPanel.set(output.outputId, fit);
        deps.streamBinStatsFitOverlayRef.set(panel.id, perPanel);
        updated = true;
      }
    }
    return updated;
  }
  if (output.kind === "trace") {
    const values = normalizeTraceValues(output.value);
    if (values !== null) {
      for (const panel of interested) {
        if (isStreamBinStatsPanel(panel)) {
          // Trace overlay on a bin-stats panel.
          if (!(panel.overlayOutputIds ?? []).includes(output.outputId)) {
            continue;
          }
          const perPanel =
            deps.streamBinStatsOverlayRef.get(panel.id) ?? new Map();
          const seq =
            output.seq ?? (perPanel.get(output.outputId)?.seq ?? 0) + 1;
          perPanel.set(output.outputId, { seq, values });
          deps.streamBinStatsOverlayRef.set(panel.id, perPanel);
          updated = true;
          continue;
        }
        if (
          !isStreamTracePanel(panel) ||
          panel.sourceMode !== "dag"
        ) {
          continue;
        }
        if (traceFilter) {
          if (
            normalizeTraceDecimator(panel.traceDecimator) !== traceFilter.traceDecimator ||
            normalizeTraceMaxPoints(panel.traceMaxPoints) !== traceFilter.traceMaxPoints ||
            normalizeTraceMaxFps(panel.traceMaxFps) !== traceFilter.traceMaxFps ||
            normalizeTraceRollingWindow(panel.rollingWindow) !==
              traceFilter.traceRollingWindow ||
            normalizeTraceAverageMode(panel.averageMode) !== traceFilter.traceAverageMode
          ) {
            continue;
          }
        }
        const primaryOutputId = String(panel.outputId ?? "").trim();
        const isPrimary =
          primaryOutputId.length > 0 && primaryOutputId === output.outputId;
        const isOverlay = (panel.overlayOutputIds ?? []).includes(
          output.outputId
        );
        if (!isPrimary && !isOverlay) {
          continue;
        }
        if (isOverlay) {
          const perPanel = deps.streamTraceOverlayRef.get(panel.id) ?? new Map();
          const seq = output.seq ?? (perPanel.get(output.outputId)?.seq ?? 0) + 1;
          perPanel.set(output.outputId, { seq, values });
          deps.streamTraceOverlayRef.set(panel.id, perPanel);
          updated = true;
          continue;
        }
        let currentFrames = deps.streamFramesRef.get(panel.id);
        if (!currentFrames) {
          currentFrames = [];
          deps.streamFramesRef.set(panel.id, currentFrames);
        }
        const seq =
          output.seq ??
          (currentFrames.length > 0
            ? currentFrames[currentFrames.length - 1].seq + 1
            : 0);
        if (
          currentFrames.length > 0 &&
          currentFrames[currentFrames.length - 1].seq === seq
        ) {
          continue;
        }
        // PerfD: in-place mutation (see raw-stream apply for rationale).
        currentFrames.push({
          seq,
          shape: [values.length],
          values,
        });
        const keep = Math.max(MAX_STREAM_FRAME_BUFFER, panel.overlayCount * 4);
        if (currentFrames.length > keep) {
          currentFrames.splice(0, currentFrames.length - keep);
        }
        updated = true;
      }
    }
    return updated;
  }
  return updated;
}
