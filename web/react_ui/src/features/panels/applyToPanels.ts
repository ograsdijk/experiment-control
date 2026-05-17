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
    const currentFrames = deps.streamFramesRef.get(panel.id) ?? [];
    if (
      currentFrames.length > 0 &&
      currentFrames[currentFrames.length - 1].seq === frame.seq
    ) {
      continue;
    }
    const appended = [
      ...currentFrames,
      {
        seq: frame.seq,
        shape: frame.shape,
        values: frame.values,
      },
    ];
    const keep = Math.max(MAX_STREAM_FRAME_BUFFER, panel.overlayCount * 4);
    const nextFrames =
      appended.length > keep ? appended.slice(appended.length - keep) : appended;
    deps.streamFramesRef.set(panel.id, nextFrames);
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
  let updated = false;
  if (output.kind === "scalar") {
    const scalar = Number(output.value);
    if (Number.isFinite(scalar)) {
      for (const panel of deps.panelsRef.current) {
        if (!isStreamScalarPanel(panel)) {
          if (isStreamParamsPanel(panel)) {
            if (panel.workspaceId !== output.workspaceId) {
              continue;
            }
            if (!(panel.outputIds ?? []).includes(output.outputId)) {
              continue;
            }
            const latest = deps.streamParamsLatestRef.get(panel.id) ?? {};
            latest[output.outputId] = scalar;
            deps.streamParamsLatestRef.set(panel.id, latest);
            updated = true;
          }
          continue;
        }
        if (panel.workspaceId !== output.workspaceId) {
          continue;
        }
        if ((panel.outputId ?? "") !== output.outputId) {
          continue;
        }
        const panelBuffers = ensurePanelBuffers(deps.buffersRef, panel.id);
        const key = traceKeyId(streamScalarTrace(panel));
        let buffer = panelBuffers.get(key);
        if (!buffer) {
          buffer = new RingBuffer(panelCapacity(panel.timeWindowS));
          panelBuffers.set(key, buffer);
        }
        buffer.push(output.tWallS, scalar);
        updated = true;
      }
    }
  }
  if (output.kind === "params_map") {
    const paramsMap = normalizeFitParamsMapValue(output.value);
    if (paramsMap) {
      for (const panel of deps.panelsRef.current) {
        if (!isStreamParamsPanel(panel)) {
          continue;
        }
        if (panel.workspaceId !== output.workspaceId) {
          continue;
        }
        if (!(panel.outputIds ?? []).includes(output.outputId)) {
          continue;
        }
        const latest = deps.streamParamsLatestRef.get(panel.id) ?? {};
        latest[output.outputId] = paramsMap;
        deps.streamParamsLatestRef.set(panel.id, latest);
        updated = true;
      }
    }
  }
  if (output.kind === "hist_agg") {
    const series = normalizeHistAggValue(output.value);
    if (series) {
      for (const panel of deps.panelsRef.current) {
        if (!isStreamBinStatsPanel(panel)) {
          continue;
        }
        if (panel.workspaceId !== output.workspaceId) {
          continue;
        }
        if ((panel.outputId ?? "") !== output.outputId) {
          continue;
        }
        deps.streamBinStatsRef.set(panel.id, series);
        updated = true;
      }
    }
  }
  if (output.kind === "hist2d") {
    const snapshot = normalizeHist2dValue(output.value);
    if (snapshot) {
      for (const panel of deps.panelsRef.current) {
        if (!isStreamBin2dPanel(panel)) {
          continue;
        }
        if (panel.workspaceId !== output.workspaceId) {
          continue;
        }
        if ((panel.outputId ?? "") !== output.outputId) {
          continue;
        }
        deps.streamBin2dRef.set(panel.id, snapshot);
        updated = true;
      }
    }
  }
  if (output.kind === "fit_1d") {
    const fit = normalizeFitCurveValue(output.value);
    if (fit) {
      for (const panel of deps.panelsRef.current) {
        if (!isStreamBinStatsPanel(panel)) {
          continue;
        }
        if (panel.workspaceId !== output.workspaceId) {
          continue;
        }
        const overlayIds = new Set(
          (panel.fitOverlayOutputIds ?? []).map((id) => String(id ?? "").trim())
        );
        if (!overlayIds.has(output.outputId)) {
          continue;
        }
        const perPanel =
          deps.streamBinStatsFitOverlayRef.get(panel.id) ?? new Map();
        perPanel.set(output.outputId, fit);
        deps.streamBinStatsFitOverlayRef.set(panel.id, perPanel);
        updated = true;
      }
    }
  }
  if (output.kind === "trace") {
    const values = normalizeTraceValues(output.value);
    if (values !== null) {
      for (const panel of deps.panelsRef.current) {
        if (
          isStreamBinStatsPanel(panel) &&
          panel.workspaceId === output.workspaceId
        ) {
          const overlayIds = new Set(
            (panel.overlayOutputIds ?? []).map((id) => String(id ?? "").trim())
          );
          if (overlayIds.has(output.outputId)) {
            const perPanel =
              deps.streamBinStatsOverlayRef.get(panel.id) ?? new Map();
            const seq =
              output.seq ?? (perPanel.get(output.outputId)?.seq ?? 0) + 1;
            perPanel.set(output.outputId, { seq, values });
            deps.streamBinStatsOverlayRef.set(panel.id, perPanel);
            updated = true;
          }
        }
        if (
          !isStreamTracePanel(panel) ||
          panel.sourceMode !== "dag" ||
          panel.workspaceId !== output.workspaceId
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
        const overlayOutputIds = new Set(
          (panel.overlayOutputIds ?? []).map((id) => String(id ?? "").trim())
        );
        const isPrimary = primaryOutputId.length > 0 && primaryOutputId === output.outputId;
        const isOverlay = overlayOutputIds.has(output.outputId);
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
        const currentFrames = deps.streamFramesRef.get(panel.id) ?? [];
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
        const appended = [
          ...currentFrames,
          {
            seq,
            shape: [values.length],
            values,
          },
        ];
        const keep = Math.max(MAX_STREAM_FRAME_BUFFER, panel.overlayCount * 4);
        const nextFrames =
          appended.length > keep
            ? appended.slice(appended.length - keep)
            : appended;
        deps.streamFramesRef.set(panel.id, nextFrames);
        updated = true;
      }
    }
  }
  return updated;
}
