import { notifications } from "@mantine/notifications";

import { computeTelemetryAutoYRange } from "../../components/PlotPanel";
import { computeStreamBin2dAutoZRange } from "../../components/StreamBin2dPanel";
import { computeStreamBinStatsAutoYRange } from "../../components/StreamBinStatsPanel";
import { computeStreamRawAutoYRange } from "../../components/StreamRawPanel";
import { computeStreamWaterfallAutoZRange } from "../../components/StreamWaterfallPanel";
import {
  isStreamBin2dPanel,
  isStreamBinStatsPanel,
  isStreamScalarPanel,
  isStreamRawPanel,
  isStreamWaterfallPanel,
  isTelemetryPanel,
  normalizeAutoRange,
  streamScalarTrace,
} from "../stream/panel_helpers";
import type {
  PlotPanelState,
  PlotTelemetryPanelState,
  YScaleMode,
  YOffsetMode,
} from "../stream/types";
import { parseNumberInput } from "../stream/utils";
import { useTelemetry } from "../telemetry/TelemetryContext";
import { RingBuffer } from "../../utils/ringBuffer";
import {
  streamBinStatsFitOverlayCurves,
  streamBinStatsOverlaySeries,
  streamTraceOverlaySeries,
} from "./overlayHelpers";
import { usePanels } from "./PanelsContext";

/**
 * Plot-options modal handlers + Y-axis range editor.
 *
 * This hook collects the panel-handler subset that needs to compute
 * auto Y-range values from the live plot buffers (telemetry RingBuffers,
 * stream-frame caches, bin-stats snapshots). App.tsx historically
 * defined these inline alongside the simpler panel-UI handlers; their
 * dependency on TelemetryContext refs + the auto-range compute helpers
 * makes them a coherent group of their own.
 *
 * **What's in this hook**:
 *
 * - `openPlotOptions(panelId)` — populates the y-axis-range modal
 *   with the panel's current settings (or the auto range as defaults
 *   when in auto mode).
 * - `closePlotOptions()` — closes the modal + clears the draft state.
 * - `applyPlotOptionsAxis(panelId)` — validates the draft + applies
 *   the manual y range, with toast feedback on invalid input.
 * - `setPlotOptionsAxisMode(panel, mode)` — switches between auto and
 *   manual scale mode, populating the manual draft with the current
 *   auto range when entering manual.
 * - `setTelemetryYOffsetMode(panelId, mode, value?)` — toggles the
 *   delta-display freeze/auto offset.
 *
 * Plus the two helpers that drive the auto-range computation:
 * `resolveTelemetryPanelOffset` and `resolvePanelAutoYRange`.
 *
 * **Why it takes `setPanelYScaleMode` / `setPanelManualYRange` as
 * args**: those are exposed by `usePanelUiHandlers` (round 15). Rather
 * than re-implementing them here, the caller passes them in so the
 * extracted state-mutation lives in one place.
 */

export interface PanelAutoRangeHandlersArgs {
  setPanelYScaleMode: (panelId: string, mode: YScaleMode) => void;
  setPanelManualYRange: (panelId: string, min: number, max: number) => void;
}

export function usePanelAutoRangeHandlers(args: PanelAutoRangeHandlersArgs) {
  const { setPanelYScaleMode, setPanelManualYRange } = args;
  const {
    panels,
    setPanels,
    yAxisDraftMin,
    yAxisDraftMax,
    setYAxisDraftMin,
    setYAxisDraftMax,
    setYAxisAutoRange,
    setPlotOptionsPanelId,
  } = usePanels();
  const {
    buffersRef,
    streamFramesRef,
    streamTraceOverlayRef,
    streamBinStatsOverlayRef,
    streamBinStatsFitOverlayRef,
    streamBinStatsRef,
    streamBin2dRef,
  } = useTelemetry();

  const resolveTelemetryPanelOffset = (
    panel: PlotTelemetryPanelState
  ): number | null => {
    if (panel.yDisplayMode !== "delta") {
      return null;
    }
    if (
      panel.yOffsetMode === "freeze" &&
      typeof panel.yOffsetValue === "number" &&
      Number.isFinite(panel.yOffsetValue)
    ) {
      return Math.round(panel.yOffsetValue);
    }
    const numericTraces = panel.traces.filter(
      (trace) => trace.valueKind !== "boolean"
    );
    if (numericTraces.length === 0) {
      return null;
    }
    const panelBuffers = buffersRef.get(panel.id) ?? new Map<string, RingBuffer>();
    const range = normalizeAutoRange(
      computeTelemetryAutoYRange(numericTraces, panelBuffers, panel.timeWindowS)
    );
    if (!range) {
      return null;
    }
    return Math.round((range.min + range.max) / 2);
  };

  const resolvePanelAutoYRange = (
    panel: PlotPanelState | null
  ): { min: number; max: number } | null => {
    if (!panel) {
      return null;
    }
    if (isTelemetryPanel(panel)) {
      const panelBuffers = buffersRef.get(panel.id) ?? new Map<string, RingBuffer>();
      const range = normalizeAutoRange(
        computeTelemetryAutoYRange(panel.traces, panelBuffers, panel.timeWindowS)
      );
      if (!range) {
        return null;
      }
      if (panel.yDisplayMode !== "delta") {
        return range;
      }
      const offset = resolveTelemetryPanelOffset(panel);
      if (offset === null) {
        return range;
      }
      return {
        min: range.min - offset,
        max: range.max - offset,
      };
    }
    if (isStreamScalarPanel(panel)) {
      const panelBuffers = buffersRef.get(panel.id) ?? new Map<string, RingBuffer>();
      return normalizeAutoRange(
        computeTelemetryAutoYRange(
          [streamScalarTrace(panel)],
          panelBuffers,
          panel.timeWindowS
        )
      );
    }
    if (isStreamBinStatsPanel(panel)) {
      const snapshot = streamBinStatsRef.get(panel.id) ?? null;
      return normalizeAutoRange(
        computeStreamBinStatsAutoYRange(
          snapshot?.series ?? null,
          panel.uncertaintyMode,
          panel.uncertaintyScale,
          streamBinStatsOverlaySeries(panel, streamBinStatsOverlayRef),
          streamBinStatsFitOverlayCurves(panel, streamBinStatsFitOverlayRef)
        )
      );
    }
    if (isStreamBin2dPanel(panel)) {
      const snapshot = streamBin2dRef.get(panel.id) ?? null;
      return normalizeAutoRange(
        computeStreamBin2dAutoZRange(snapshot?.series ?? null, panel.reducer)
      );
    }
    const frames = streamFramesRef.get(panel.id) ?? [];
    if (isStreamWaterfallPanel(panel)) {
      return normalizeAutoRange(
        computeStreamWaterfallAutoZRange(
          frames,
          panel.overlayCount,
          panel.sourceMode === "raw" ? panel.channelIndex : 0
        )
      );
    }
    if (isStreamRawPanel(panel)) {
      return normalizeAutoRange(
        computeStreamRawAutoYRange(
          frames,
          panel.overlayCount,
          panel.sourceMode === "raw" ? panel.channelIndex : 0,
          panel.sourceMode === "dag"
            ? streamTraceOverlaySeries(panel, streamTraceOverlayRef)
            : []
        )
      );
    }
    return null;
  };

  const setTelemetryYOffsetMode = (
    panelId: string,
    mode: YOffsetMode,
    value: number | null = null
  ) => {
    const panel = panels.find((entry) => entry.id === panelId);
    const resolvedFreezeValue =
      mode === "freeze"
        ? typeof value === "number" && Number.isFinite(value)
          ? value
          : panel && isTelemetryPanel(panel)
          ? resolveTelemetryPanelOffset(panel)
          : null
        : null;
    setPanels((prev) =>
      prev.map((entry) => {
        if (entry.id !== panelId || !isTelemetryPanel(entry)) {
          return entry;
        }
        if (
          mode === "freeze" &&
          typeof resolvedFreezeValue === "number" &&
          Number.isFinite(resolvedFreezeValue)
        ) {
          return {
            ...entry,
            yOffsetMode: "freeze",
            yOffsetValue: Math.round(resolvedFreezeValue),
          };
        }
        return { ...entry, yOffsetMode: "auto", yOffsetValue: null };
      })
    );
  };

  const openPlotOptions = (panelId: string) => {
    const panel = panels.find((entry) => entry.id === panelId) ?? null;
    if (!panel) {
      return;
    }
    const autoRange = resolvePanelAutoYRange(panel);
    setPlotOptionsPanelId(panelId);
    setYAxisAutoRange(autoRange);
    const panelWithY = panel as { yScaleMode?: YScaleMode; yMin?: number | null; yMax?: number | null };
    if (
      panelWithY.yScaleMode === "manual" &&
      typeof panelWithY.yMin === "number" &&
      typeof panelWithY.yMax === "number" &&
      Number.isFinite(panelWithY.yMin) &&
      Number.isFinite(panelWithY.yMax) &&
      panelWithY.yMin < panelWithY.yMax
    ) {
      setYAxisDraftMin(panelWithY.yMin);
      setYAxisDraftMax(panelWithY.yMax);
      return;
    }
    setYAxisDraftMin(autoRange ? autoRange.min : "");
    setYAxisDraftMax(autoRange ? autoRange.max : "");
  };

  const closePlotOptions = () => {
    setPlotOptionsPanelId(null);
    setYAxisDraftMin("");
    setYAxisDraftMax("");
    setYAxisAutoRange(null);
  };

  const applyPlotOptionsAxis = (panelId: string) => {
    const min = parseNumberInput(yAxisDraftMin);
    const max = parseNumberInput(yAxisDraftMax);
    if (min === null || max === null) {
      notifications.show({
        color: "red",
        title: "Invalid y range",
        message: "Manual y-axis limits require numeric min and max values.",
      });
      return;
    }
    if (min >= max) {
      notifications.show({
        color: "red",
        title: "Invalid y range",
        message: "Y-axis min must be less than y-axis max.",
      });
      return;
    }
    setPanelManualYRange(panelId, min, max);
  };

  const setPlotOptionsAxisMode = (panel: PlotPanelState, mode: YScaleMode) => {
    if (mode === "auto") {
      setPanelYScaleMode(panel.id, "auto");
      return;
    }
    const autoRange = resolvePanelAutoYRange(panel);
    setYAxisAutoRange(autoRange);
    const panelWithY = panel as { yScaleMode?: YScaleMode; yMin?: number | null; yMax?: number | null };
    if (
      panelWithY.yScaleMode !== "manual" ||
      typeof panelWithY.yMin !== "number" ||
      typeof panelWithY.yMax !== "number" ||
      !Number.isFinite(panelWithY.yMin) ||
      !Number.isFinite(panelWithY.yMax) ||
      panelWithY.yMin >= panelWithY.yMax
    ) {
      if (autoRange) {
        setYAxisDraftMin(autoRange.min);
        setYAxisDraftMax(autoRange.max);
      } else {
        setYAxisDraftMin(0);
        setYAxisDraftMax(1);
      }
    }
    setPanelYScaleMode(panel.id, "manual");
  };

  return {
    resolveTelemetryPanelOffset,
    resolvePanelAutoYRange,
    setTelemetryYOffsetMode,
    openPlotOptions,
    closePlotOptions,
    applyPlotOptionsAxis,
    setPlotOptionsAxisMode,
  };
}
