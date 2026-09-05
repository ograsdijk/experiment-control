import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  ActionIcon,
  Group,
  Text,
  useComputedColorScheme,
} from "@mantine/core";
import { IconX } from "@tabler/icons-react";

import { PlotPanel } from "../../components/PlotPanel";
import { PlotLegend, type PlotLegendItem } from "../../components/PlotLegend";
import {
  BIN_STATS_FIT_OVERLAY_COLORS,
  BIN_STATS_OVERLAY_COLORS,
  StreamBinStatsPanel,
  binStatsMeanStroke,
} from "../../components/StreamBinStatsPanel";
import { StreamBin2dPanel } from "../../components/StreamBin2dPanel";
import { StreamParamsPanel } from "../../components/StreamParamsPanel";
import { StreamRawPanel } from "../../components/StreamRawPanel";
import { StreamWaterfallPanel } from "../../components/StreamWaterfallPanel";
import { DraggableTraceChip } from "../../components/DraggableTraceChip";
import { perfCountScoped } from "../performance/perfInstrumentation";
import { ReorderableCardShell } from "../layout/ReorderableCardShell";
import {
  isStreamBin2dPanel,
  isStreamBinStatsPanel,
  isStreamParamsPanel,
  isStreamRawPanel,
  isStreamScalarPanel,
  isStreamTracePanel,
  isStreamWaterfallPanel,
  isTelemetryPanel,
  streamScalarTrace,
} from "../stream/panel_helpers";
import {
  DEFAULT_STREAM_CONTEXT_FIELD,
  inferChannelCountFromShape,
  traceKeyId,
} from "../stream/utils";
import type {
  PlotPanelState,
  TelemetrySmoothingMode,
  YDisplayMode,
  YOffsetMode,
  YScaleMode,
} from "../stream/types";
import {
  workspaceBin2dAxisLabel,
  workspaceOutputOptionsByKind,
  workspaceXAxisLabel,
} from "../stream/workspace";
import { MAX_PANEL_HEIGHT_PX, MIN_PANEL_HEIGHT_PX } from "../profile/plot_state";
import { outputDisplayName } from "../stream/output_labels";
import { useStreamAnalysis } from "../stream_analysis/StreamAnalysisContext";
import { useTelemetry } from "../telemetry/TelemetryContext";
import { colorWithAlpha, traceColorAt } from "../../utils/traceColors";
import { usePanels } from "./PanelsContext";
import {
  markPanelDirty,
  panelInvalidationStore,
  usePanelRevision,
} from "./PanelInvalidationStore";
import { PanelCardHeader, type PanelMenuItem } from "./PanelCardHeader";
import {
  EMPTY_PANEL_SETTINGS_OPTIONS,
  PanelSettings,
  type PanelSettingsOptions,
} from "./PanelSettings";
import { PanelStatusLine, type PanelStatusItem } from "./PanelStatusLine";
import { usePlotAreaHeight } from "./usePlotAreaHeight";
import type {
  PanelsGridHandlers,
  PanelsGridHelpers,
} from "./PanelsGrid";

/**
 * Single panel card — renders one `<ReorderableCardShell>` containing a
 * compact header, the per-kind plot body, a series legend, and one line
 * of live status.
 *
 * The card is monitoring-first: the title and the plot are permanent, and
 * everything that configures the panel lives behind the header's settings
 * control — one surface, no second modal behind it. What used to be a row
 * of up to fourteen badges under the plot is split by nature rather than
 * by place — configuration moved into settings, live state stayed on the
 * card, because a climbing dropped-sample count should not need a modal
 * to notice.
 *
 * Its memo boundary and panel-scoped revision subscription prevent
 * unrelated data updates from reaching this card.
 */

const PANEL_SORTABLE_PREFIX = "panel:";
function panelSortableId(panelId: string): string {
  return `${PANEL_SORTABLE_PREFIX}${panelId}`;
}

/**
 * Elements that own their own click. Activating the card from a pointer
 * that landed on one of these would fight the control the user actually
 * aimed at — the plot canvas included, which has cursor and drag-zoom
 * behaviour of its own.
 */
const NON_ACTIVATING_SELECTOR =
  "button, input, select, textarea, a, canvas, [role='menu'], [role='dialog'], [data-no-activate]";

/** Shared empty map so an untouched panel keeps a stable identity. */
const EMPTY_SERIES_LABELS: Record<string, string> = {};

/** Pointer travel beyond this reads as a drag, not a click. */
const ACTIVATION_DRAG_SLOP_PX = 4;

function formatOffsetCompact(value: number): string {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  const absValue = Math.abs(value);
  if (absValue >= 1e6 || (absValue > 0 && absValue < 1e-3)) {
    return value.toExponential(2);
  }
  return value.toPrecision(4);
}

function formatOffsetFull(value: number): string {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  return value.toFixed(6);
}

/**
 * Which published output kind a panel binds to, or null when the panel
 * picks its outputs through its own modal (params) or is not DAG-bound.
 */
function outputKindForPanel(
  panel: PlotPanelState
): "scalar" | "hist_agg" | "hist2d" | null {
  if (isStreamScalarPanel(panel)) return "scalar";
  if (isStreamBinStatsPanel(panel)) return "hist_agg";
  if (isStreamBin2dPanel(panel)) return "hist2d";
  return null;
}

export interface PanelCardProps {
  panel: PlotPanelState;
  streamWorkspaceOptions: Array<{ value: string; label: string }>;
  streamTargetOptions: Array<{ value: string; label: string }>;
  yAxisDraftInvalid: boolean;
  streamWsConnected: boolean;
  streamAnalysisWsConnected: boolean;
  activeUiDrag: { kind: string; panelId?: string } | null;
  helpers: PanelsGridHelpers;
  handlers: PanelsGridHandlers;
}

function PanelCardImpl({
  panel,
  streamWorkspaceOptions,
  streamTargetOptions,
  yAxisDraftInvalid,
  streamWsConnected,
  streamAnalysisWsConnected,
  activeUiDrag,
  helpers,
  handlers,
}: PanelCardProps) {
  perfCountScoped("react.PanelCard", panel.id, 1, ".renders");
  const {
    activePanelId,
    setActivePanelId,
    editingPanelId,
    panelTitleDraft,
    setPanelTitleDraft,
    plotOptionsPanelId,
    yAxisDraftMin,
    yAxisDraftMax,
    setYAxisDraftMin,
    setYAxisDraftMax,
    yAxisAutoRange,
    panels,
    expandedPlotPanelId,
  } = usePanels();
  const panelRevision = usePanelRevision(panel.id);
  const visibilityNodeRef = useRef<HTMLElement | null>(null);

  // While this panel is open in the expanded modal, the modal body
  // registers its own visibility source and renders the same data. The
  // card is behind an overlay and cannot be seen, so keeping its source
  // live would drive two plots for one panel at the full frame rate.
  const isExpanded = expandedPlotPanelId === panel.id;
  const isExpandedRef = useRef(isExpanded);
  isExpandedRef.current = isExpanded;
  const intersectingRef = useRef(false);
  const applyCardVisibility = useCallback(() => {
    const visible = intersectingRef.current && !isExpandedRef.current;
    panelInvalidationStore.setPanelVisible(panel.id, "card", visible);
    if (visible) markPanelDirty(panel.id);
  }, [panel.id]);

  useEffect(() => {
    const node = visibilityNodeRef.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        intersectingRef.current = Boolean(entry?.isIntersecting);
        applyCardVisibility();
      },
      { rootMargin: "300px 0px" }
    );
    observer.observe(node);
    return () => {
      observer.disconnect();
      panelInvalidationStore.removeVisibilitySource(panel.id, "card");
    };
  }, [panel.id, applyCardVisibility]);

  useEffect(() => {
    applyCardVisibility();
  }, [isExpanded, applyCardVisibility]);
  useEffect(() => {
    const fps = isStreamTracePanel(panel) ? panel.traceMaxFps : 30;
    panelInvalidationStore.setPanelMaxFps(panel.id, fps);
  }, [panel]);
  const {
    buffersRef,
    streamFramesRef,
    streamBinStatsRef,
    streamBin2dRef,
    streamParamsLatestRef,
  } = useTelemetry();
  const { streamWorkspaces } = useStreamAnalysis();
  const computedColorScheme = useComputedColorScheme("light");
  const isDark = computedColorScheme === "dark";
  const { measureRef, plotHeight } = usePlotAreaHeight(panel.heightPx);
  // Which legend entry / trace chip is being renamed, and its draft. Local
  // to the card: unlike the y-axis draft, nothing outside needs to read it.
  const [renamingSeriesKey, setRenamingSeriesKey] = useState<string | null>(
    null
  );
  const [seriesRenameDraft, setSeriesRenameDraft] = useState("");

  const {
    resolveTelemetryPanelOffset,
    streamTraceOverlaySeries,
    streamExtraChannelSeries,
    streamBinStatsOverlaySeries,
    streamBinStatsFitOverlayCurves,
    isExpandablePlotPanel,
    copyTextToClipboard,
  } = helpers;
  const {
    startPanelTitleEdit,
    commitPanelTitleEdit,
    cancelPanelTitleEdit,
    removePanel,
    duplicatePanel,
    setPanelSeriesLabel,
    removeTraceFromPanel,
    openPlotOptions,
    closePlotOptions,
    clearPanelBuffers,
    clearStreamPanelFrames,
    clearStreamBinStatsPanel,
    clearStreamBin2dPanel,
    openExpandedPlot,
  } = handlers;

  // The active panel is where a clicked telemetry signal lands, so the
  // concept only means anything for telemetry panels. Stream cards no
  // longer take the marker for a state that would do nothing for them.
  const canActivate = isTelemetryPanel(panel);
  const isActive = canActivate && panel.id === activePanelId;

  // PerfB: workspace-keyed derivations are memoized so they
  // skip recomputation on panelRevision (which fires for this panel's WS
  // rate). These only depend on the panel and the workspaces
  // map; the panel-kind branches branch the same way each
  // render so the cost is in the workspace lookups +
  // workspaceOutputOptionsByKind iteration.
  const workspaceDerivations = useMemo(() => {
    const ws =
      isStreamScalarPanel(panel) ||
      isStreamParamsPanel(panel) ||
      isStreamBinStatsPanel(panel) ||
      isStreamBin2dPanel(panel) ||
      (isStreamTracePanel(panel) && panel.sourceMode === "dag")
        ? streamWorkspaces[panel.workspaceId] ?? null
        : null;
    const outputKind = outputKindForPanel(panel);
    return {
      streamWorkspace: ws,
      outputOptions: outputKind
        ? workspaceOutputOptionsByKind(ws, outputKind)
        : [],
      binStatsXLabel: isStreamBinStatsPanel(panel)
        ? workspaceXAxisLabel(ws, panel.outputId)
        : DEFAULT_STREAM_CONTEXT_FIELD,
      bin2dXLabel: isStreamBin2dPanel(panel)
        ? workspaceBin2dAxisLabel(ws, panel.outputId, "x")
        : DEFAULT_STREAM_CONTEXT_FIELD,
      bin2dYLabel: isStreamBin2dPanel(panel)
        ? workspaceBin2dAxisLabel(ws, panel.outputId, "y")
        : "context_y",
      telemetryNumericTraceCount: isTelemetryPanel(panel)
        ? panel.traces.filter(
            (trace) => trace.valueKind !== "boolean"
          ).length
        : 0,
      telemetryOffsetUnit: isTelemetryPanel(panel)
        ? (() => {
            const units = panel.traces
              .filter((trace) => trace.valueKind !== "boolean")
              .map((trace) =>
                typeof trace.units === "string"
                  ? trace.units.trim()
                  : ""
              )
              .filter((unit) => unit.length > 0);
            if (units.length === 0) {
              return "";
            }
            const unique = new Set(units);
            return unique.size === 1 ? units[0] : "";
          })()
        : "",
    };
  }, [panel, streamWorkspaces]);
  const {
    streamWorkspace,
    outputOptions,
    binStatsXLabel,
    bin2dXLabel,
    bin2dYLabel,
    telemetryNumericTraceCount,
    telemetryOffsetUnit,
  } = workspaceDerivations;

  // Ref-reads + tick-driven derivations stay un-memoized:
  // they intentionally fetch fresh data on every render so
  // panels reflect the latest snapshot pushed by the apply
  // helpers.
  const panelBuffers = buffersRef.get(panel.id) ?? new Map();
  const binStatsSnapshot = isStreamBinStatsPanel(panel)
    ? streamBinStatsRef.get(panel.id) ?? null
    : null;
  const bin2dSnapshot = isStreamBin2dPanel(panel)
    ? streamBin2dRef.get(panel.id) ?? null
    : null;
  const telemetryOffset = isTelemetryPanel(panel)
    ? resolveTelemetryPanelOffset(panel)
    : null;
  const telemetryOffsetCompact =
    typeof telemetryOffset === "number" &&
    Number.isFinite(telemetryOffset)
      ? formatOffsetCompact(telemetryOffset)
      : "n/a";
  const telemetryOffsetFull =
    typeof telemetryOffset === "number" &&
    Number.isFinite(telemetryOffset)
      ? formatOffsetFull(telemetryOffset)
      : null;
  const telemetryOffsetLabel = telemetryOffsetUnit
    ? `${telemetryOffsetCompact} ${telemetryOffsetUnit}`
    : telemetryOffsetCompact;
  const telemetryOffsetFullLabel =
    telemetryOffsetFull !== null
      ? telemetryOffsetUnit
        ? `${telemetryOffsetFull} ${telemetryOffsetUnit}`
        : telemetryOffsetFull
      : null;

  // --- settings options ----------------------------------------------
  //
  // The option lists the settings popover needs are derived from the
  // workspace, and there are up to four of them per panel. Computing
  // them for every card on every workspace update is the cost the old
  // modals avoided by deriving only for the one panel whose modal was
  // open; the gate keeps that property now that the surface is per card.
  const settingsOpen = plotOptionsPanelId === panel.id;
  const settingsOptions = useMemo<PanelSettingsOptions>(() => {
    if (!settingsOpen) {
      return EMPTY_PANEL_SETTINGS_OPTIONS;
    }
    const ws = streamWorkspace;
    const traceOptions = workspaceOutputOptionsByKind(ws, "trace");
    const primary =
      isStreamTracePanel(panel) && panel.sourceMode === "dag"
        ? traceOptions
        : outputOptions;
    const selectedPrimary =
      isStreamTracePanel(panel) ? String(panel.outputId ?? "").trim() : "";
    return {
      outputOptions: primary,
      overlayTraceOptions: isStreamTracePanel(panel)
        ? traceOptions.filter((option) => option.value !== selectedPrimary)
        : traceOptions,
      fitOverlayOptions: isStreamBinStatsPanel(panel)
        ? workspaceOutputOptionsByKind(ws, "fit_1d")
        : [],
      paramsOutputOptions: isStreamParamsPanel(panel)
        ? [
            ...workspaceOutputOptionsByKind(ws, "scalar").map((item) => ({
              value: item.value,
              label: `[scalar] ${item.label}`,
            })),
            ...workspaceOutputOptionsByKind(ws, "params_map").map((item) => ({
              value: item.value,
              label: `[fit params] ${item.label}`,
            })),
          ]
        : [],
      binStatsXLabel: binStatsXLabel,
      bin2dXLabel: bin2dXLabel,
      bin2dYLabel: bin2dYLabel,
    };
  }, [
    settingsOpen,
    panel,
    streamWorkspace,
    outputOptions,
    binStatsXLabel,
    bin2dXLabel,
    bin2dYLabel,
  ]);

  // --- live link -----------------------------------------------------
  const linkLabel =
    isStreamTracePanel(panel) && panel.sourceMode === "raw"
      ? "stream"
      : "analysis";
  const linkConnected = isTelemetryPanel(panel)
    ? null
    : isStreamTracePanel(panel) && panel.sourceMode === "raw"
    ? streamWsConnected
    : streamAnalysisWsConnected;

  // Card status: live state only, and only when it says something. The
  // settings popover below carries the same counters unconditionally.
  const statusItems: PanelStatusItem[] = [];
  // The dot in the header carries the healthy case. A link that is down
  // says so in words too — it is the one live state worth a row.
  if (linkConnected === false) {
    statusItems.push({ text: `${linkLabel} link down`, warn: true });
  }
  if (isStreamBinStatsPanel(panel)) {
    const active =
      binStatsSnapshot?.populatedBinCount ??
      binStatsSnapshot?.activeBinCount ??
      null;
    const max = binStatsSnapshot?.maxBinCount ?? null;
    if (active !== null && max !== null && max > 0) {
      statusItems.push({ text: `${active}/${max} bins` });
    }
  }
  if (isStreamBin2dPanel(panel)) {
    const filled = bin2dSnapshot?.populatedBinCount ?? null;
    if (filled !== null) {
      statusItems.push({ text: `${filled} bins filled` });
    }
    const dropped = bin2dSnapshot?.droppedSamples ?? 0;
    if (dropped > 0) {
      statusItems.push({ text: `${dropped} dropped`, warn: true });
    }
  }

  const clearPanel = () => {
    if (isTelemetryPanel(panel) || isStreamScalarPanel(panel)) {
      clearPanelBuffers(panel.id);
      return;
    }
    if (isStreamParamsPanel(panel)) {
      streamParamsLatestRef.set(panel.id, {});
      markPanelDirty(panel.id);
      return;
    }
    if (isStreamBinStatsPanel(panel)) {
      void clearStreamBinStatsPanel(panel.id);
      return;
    }
    if (isStreamBin2dPanel(panel)) {
      void clearStreamBin2dPanel(panel.id);
      return;
    }
    clearStreamPanelFrames(panel.id);
  };

  const menuItems: PanelMenuItem[] = [
    {
      key: "rename",
      label: "Rename",
      onClick: () => startPanelTitleEdit(panel),
    },
    {
      key: "duplicate",
      label: "Duplicate",
      onClick: () => duplicatePanel(panel.id),
    },
    {
      key: "clear",
      label:
        isStreamBinStatsPanel(panel) || isStreamBin2dPanel(panel)
          ? "Clear binned data"
          : "Clear",
      onClick: clearPanel,
    },
    {
      key: "remove",
      label: "Remove panel",
      color: "red",
      disabled: panels.length <= 1,
      onClick: () => removePanel(panel.id),
    },
  ];

  // Everything the badge row used to show, unconditionally. The card's
  // status line is a filtered view of the live half of this.
  const statusDetailRows: Array<[string, string]> = [];
  if (linkConnected !== null) {
    statusDetailRows.push([
      `${linkLabel} link`,
      linkConnected ? "connected" : "disconnected",
    ]);
  }
  if (isStreamBinStatsPanel(panel)) {
    const active =
      binStatsSnapshot?.populatedBinCount ??
      binStatsSnapshot?.activeBinCount ??
      null;
    const max = binStatsSnapshot?.maxBinCount ?? null;
    statusDetailRows.push([
      "bins",
      active !== null && max !== null && max > 0 ? `${active}/${max}` : "n/a",
    ]);
    statusDetailRows.push(["x axis", binStatsXLabel]);
    statusDetailRows.push(["uncertainty", panel.uncertaintyMode]);
    if (panel.uncertaintyScale !== 1) {
      statusDetailRows.push(["k", String(panel.uncertaintyScale)]);
    }
  }
  if (isStreamBin2dPanel(panel)) {
    const xActive = bin2dSnapshot?.xActiveBinCount ?? null;
    const yActive = bin2dSnapshot?.yActiveBinCount ?? null;
    const xMax = bin2dSnapshot?.xMaxBinCount ?? null;
    const yMax = bin2dSnapshot?.yMaxBinCount ?? null;
    statusDetailRows.push([
      "bins",
      xActive !== null &&
      yActive !== null &&
      xMax !== null &&
      yMax !== null &&
      xMax > 0 &&
      yMax > 0
        ? `${xActive}x${yActive}/${xMax}x${yMax}`
        : "n/a",
    ]);
    statusDetailRows.push([
      "filled",
      String(bin2dSnapshot?.populatedBinCount ?? "n/a"),
    ]);
    statusDetailRows.push([
      "dropped",
      String(bin2dSnapshot?.droppedSamples ?? "n/a"),
    ]);
    statusDetailRows.push(["x axis", bin2dXLabel]);
    statusDetailRows.push(["y axis", bin2dYLabel]);
    statusDetailRows.push(["reducer", panel.reducer]);
  }
  if (isStreamTracePanel(panel)) {
    statusDetailRows.push(["source", panel.sourceMode]);
    if (panel.sourceMode === "raw") {
      statusDetailRows.push([
        "stream",
        panel.stream
          ? `${panel.stream.deviceId}.${panel.stream.stream}`
          : "not selected",
      ]);
      if (
        panel.stream &&
        inferChannelCountFromShape(panel.stream.shape) > 1
      ) {
        statusDetailRows.push(["channel", String(panel.channelIndex)]);
      }
    } else {
      statusDetailRows.push(["output", panel.outputId ?? "none"]);
    }
    statusDetailRows.push(["decimator", panel.traceDecimator]);
    statusDetailRows.push(["max points", String(panel.traceMaxPoints)]);
    statusDetailRows.push(["max fps", panel.traceMaxFps.toFixed(1)]);
    if (panel.overlayCount > 1) {
      statusDetailRows.push([
        isStreamWaterfallPanel(panel) ? "rows" : "overlays",
        String(panel.overlayCount),
      ]);
    }
    if (panel.rollingWindow > 1) {
      statusDetailRows.push([
        `average (${panel.averageMode})`,
        String(panel.rollingWindow),
      ]);
    }
  }
  if (isStreamScalarPanel(panel)) {
    statusDetailRows.push(["output", panel.outputId ?? "none"]);
  }
  if (isStreamParamsPanel(panel)) {
    statusDetailRows.push(["outputs", String(panel.outputIds.length)]);
  }
  if (streamWorkspace) {
    statusDetailRows.push(["workspace", streamWorkspace.name]);
    if (streamWorkspace.stream) {
      statusDetailRows.push([
        "workspace stream",
        `${streamWorkspace.stream.deviceId}.${streamWorkspace.stream.stream}`,
      ]);
    }
  }

  const settingsSlot = (
    <PanelSettings
      panel={panel}
      opened={settingsOpen}
      onToggle={() => {
        if (settingsOpen) {
          closePlotOptions();
          return;
        }
        openPlotOptions(panel.id);
      }}
      onClose={closePlotOptions}
      streamWorkspaceOptions={streamWorkspaceOptions}
      streamTargetOptions={streamTargetOptions}
      options={settingsOptions}
      yAxisDraftMin={yAxisDraftMin}
      yAxisDraftMax={yAxisDraftMax}
      onYAxisDraftMinChange={setYAxisDraftMin}
      onYAxisDraftMaxChange={setYAxisDraftMax}
      yAxisAutoRange={yAxisAutoRange}
      yAxisDraftInvalid={yAxisDraftInvalid}
      statusRows={statusDetailRows}
      telemetryNumericTraceCount={telemetryNumericTraceCount}
      telemetryOffset={telemetryOffset}
      telemetryOffsetLabel={telemetryOffsetLabel}
      telemetryOffsetFullLabel={telemetryOffsetFullLabel}
      handlers={handlers}
    />
  );

  // --- activation ----------------------------------------------------
  const pointerOriginRef = useRef<{ x: number; y: number } | null>(null);
  const handlePointerDownCapture = (
    event: ReactPointerEvent<HTMLDivElement>
  ) => {
    pointerOriginRef.current = { x: event.clientX, y: event.clientY };
  };
  const handlePointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    const origin = pointerOriginRef.current;
    pointerOriginRef.current = null;
    if (!canActivate || isActive || !origin || activeUiDrag) {
      return;
    }
    if (
      Math.abs(event.clientX - origin.x) > ACTIVATION_DRAG_SLOP_PX ||
      Math.abs(event.clientY - origin.y) > ACTIVATION_DRAG_SLOP_PX
    ) {
      return;
    }
    const target = event.target as HTMLElement | null;
    if (target?.closest(NON_ACTIVATING_SELECTOR)) {
      return;
    }
    setActivePanelId(panel.id);
  };

  // --- series --------------------------------------------------------
  // Multi-channel mode is raw-only: a waterfall has no extra channels, so
  // the flag has to be read through the narrower guard.
  const hasExtraChannels =
    isStreamRawPanel(panel) && (panel.extraChannelIndices?.length ?? 0) > 0;
  const traceExtraSeries = isStreamTracePanel(panel)
    ? panel.sourceMode === "dag"
      ? streamTraceOverlaySeries(panel)
      : hasExtraChannels
      ? streamExtraChannelSeries(panel)
      : []
    : [];
  const traceOverlayCount = isStreamTracePanel(panel)
    ? panel.sourceMode === "raw" && hasExtraChannels
      ? 1
      : panel.overlayCount
    : 0;
  const binStatsOverlays = isStreamBinStatsPanel(panel)
    ? streamBinStatsOverlaySeries(panel)
    : [];
  const binStatsFits = isStreamBinStatsPanel(panel)
    ? streamBinStatsFitOverlayCurves(panel)
    : [];

  // A trace's name comes from the panel's own override first, then from
  // the workspace label / derived output name, then from the raw id. The
  // key is the series identity the overlay helpers already produce
  // (`output_id` or `ch N`), so a rename survives reordering.
  const seriesLabels = panel.seriesLabels ?? EMPTY_SERIES_LABELS;
  const seriesDisplayName = (seriesKey: string): string => {
    const override = seriesLabels[seriesKey];
    if (override) {
      return override;
    }
    if (seriesKey.startsWith("ch ")) {
      return seriesKey;
    }
    return outputDisplayName(streamWorkspace, seriesKey) || seriesKey;
  };

  // Colour order mirrors the data order the plot components build:
  // primary series first, then extras/overlays, then fit curves.
  const legendItems: PlotLegendItem[] = [];
  if (isStreamRawPanel(panel) && traceExtraSeries.length > 0) {
    const primaryKey =
      panel.sourceMode === "dag"
        ? panel.outputId ?? "output"
        : `ch ${panel.channelIndex}`;
    legendItems.push({
      key: primaryKey,
      label: seriesDisplayName(primaryKey),
      title: primaryKey,
      color: traceColorAt(0),
    });
    traceExtraSeries.forEach((series, idx) => {
      legendItems.push({
        key: series.label,
        label: seriesDisplayName(series.label),
        title: series.label,
        color: traceColorAt(traceOverlayCount + idx),
      });
    });
  }
  if (
    isStreamBinStatsPanel(panel) &&
    (binStatsOverlays.length > 0 || binStatsFits.length > 0)
  ) {
    const meanKey = panel.outputId ?? "mean";
    legendItems.push({
      key: meanKey,
      label: seriesDisplayName(meanKey),
      title: meanKey,
      color: binStatsMeanStroke(isDark),
    });
    binStatsOverlays.forEach((series, idx) => {
      legendItems.push({
        key: series.label,
        label: seriesDisplayName(series.label),
        title: series.label,
        color: BIN_STATS_OVERLAY_COLORS[idx % BIN_STATS_OVERLAY_COLORS.length],
      });
    });
    binStatsFits.forEach((curve, idx) => {
      // A fit curve is derived from a series that already has its own
      // legend entry, so it follows that name instead of taking a rename
      // of its own.
      legendItems.push({
        key: `fit:${curve.label}`,
        label: `${seriesDisplayName(curve.label)} (fit)`,
        title: `${curve.label} (fit)`,
        renamable: false,
        color:
          BIN_STATS_FIT_OVERLAY_COLORS[
            idx % BIN_STATS_FIT_OVERLAY_COLORS.length
          ],
      });
    });
  }

  const startSeriesRename = (seriesKey: string) => {
    setRenamingSeriesKey(seriesKey);
    setSeriesRenameDraft(seriesLabels[seriesKey] ?? "");
  };
  const commitSeriesRename = () => {
    if (renamingSeriesKey) {
      setPanelSeriesLabel(panel.id, renamingSeriesKey, seriesRenameDraft);
    }
    setRenamingSeriesKey(null);
    setSeriesRenameDraft("");
  };
  const cancelSeriesRename = () => {
    setRenamingSeriesKey(null);
    setSeriesRenameDraft("");
  };

  // --- empty states --------------------------------------------------
  // These used to be dimmed text inside the badge row. With the row gone
  // they take the plot's place, which is also where the eye already is.
  const emptyMessage = isStreamTracePanel(panel)
    ? panel.sourceMode === "raw"
      ? panel.stream
        ? null
        : "Select a stream in settings to start plotting raw frames."
      : streamWorkspace?.stream
      ? null
      : "Bind this panel to a configured DAG workspace in settings."
    : isStreamScalarPanel(panel) || isStreamBinStatsPanel(panel)
    ? streamWorkspace?.stream
      ? null
      : "Bind this panel to a configured DAG workspace in settings."
    : null;

  const emptyState = (
    <div className="plot-empty-state" style={{ height: plotHeight }}>
      <Text size="sm" c="dimmed">
        {emptyMessage}
      </Text>
    </div>
  );

  return (
    <ReorderableCardShell
      key={panel.id}
      id={panelSortableId(panel.id)}
      data={{ kind: "panel", panelId: panel.id }}
      className="plot-workspace-card"
      dataPanelCardId={panel.id}
      dense
      observeRef={(node) => {
        visibilityNodeRef.current = node;
      }}
      onPointerDownCapture={handlePointerDownCapture}
      onPointerUp={handlePointerUp}
      dragHandleTitle="Drag from border to reorder panels"
      style={{
        border:
          isActive
            ? "2px solid #0e9f9a"
            : "1px solid var(--card-border)",
        background: "var(--card)",
        position: "relative",
        ...(panel.colSpan && panel.colSpan > 1
          ? { gridColumn: `span ${panel.colSpan}` }
          : {}),
      }}
    >
      <PanelCardHeader
        title={panel.title}
        placeholder={panel.id}
        editing={editingPanelId === panel.id}
        draft={panelTitleDraft}
        onDraftChange={setPanelTitleDraft}
        onCommit={commitPanelTitleEdit}
        onCancel={cancelPanelTitleEdit}
        onStartEdit={() => startPanelTitleEdit(panel)}
        connected={linkConnected}
        linkLabel={linkLabel}
        settingsSlot={settingsSlot}
        onExpand={
          isExpandablePlotPanel(panel)
            ? () => openExpandedPlot(panel.id)
            : undefined
        }
        menuItems={menuItems}
      />
      <div ref={measureRef} style={{ width: "100%" }}>
        {isTelemetryPanel(panel) ? (
          <>
            <PlotPanel
              panelId={panel.id}
              traces={panel.traces}
              seriesLabels={panel.seriesLabels}
              buffers={panelBuffers}
              tick={panelRevision}
              timeWindowS={panel.timeWindowS}
              colorScheme={computedColorScheme}
              plotHeight={plotHeight}
              yScaleMode={panel.yScaleMode}
              yMin={panel.yMin}
              yMax={panel.yMax}
              yDisplayMode={panel.yDisplayMode}
              yOffset={telemetryOffset}
              smoothingMode={panel.smoothingMode}
              smoothingWindowS={panel.smoothingWindowS}
            />
            <Group gap={6} wrap="wrap" mt={6}>
              {panel.traces.map((trace, traceIndex) => {
                const traceColor = traceColorAt(traceIndex);
                const units =
                  typeof trace.units === "string" && trace.units.trim()
                    ? ` (${trace.units.trim()})`
                    : "";
                return (
                  <DraggableTraceChip
                    key={traceKeyId(trace)}
                    panelId={panel.id}
                    trace={trace}
                    className="trace-chip"
                    style={{
                      color: traceColor,
                      background: colorWithAlpha(
                        traceColor,
                        isDark ? 0.22 : 0.14
                      ),
                      border: `1px solid ${colorWithAlpha(
                        traceColor,
                        isDark ? 0.45 : 0.3
                      )}`,
                    }}
                  >
                    {renamingSeriesKey === traceKeyId(trace) ? (
                      <input
                        className="plot-legend-input"
                        autoFocus
                        data-no-activate="true"
                        aria-label={`Rename ${trace.deviceId}.${trace.signal}`}
                        placeholder={`${trace.deviceId}.${trace.signal}`}
                        value={seriesRenameDraft}
                        onChange={(event) =>
                          setSeriesRenameDraft(event.currentTarget.value)
                        }
                        onBlur={commitSeriesRename}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            commitSeriesRename();
                          } else if (event.key === "Escape") {
                            event.preventDefault();
                            cancelSeriesRename();
                          }
                        }}
                      />
                    ) : (
                      <span
                        title="Double-click to rename"
                        onDoubleClick={() =>
                          startSeriesRename(traceKeyId(trace))
                        }
                      >
                        {seriesLabels[traceKeyId(trace)] ??
                          `${trace.deviceId}.${trace.signal}`}
                        {units}
                      </span>
                    )}
                    <ActionIcon
                      size="sm"
                      variant="subtle"
                      color="red"
                      onClick={() => removeTraceFromPanel(panel.id, trace)}
                      aria-label={`Remove ${trace.deviceId}.${trace.signal}`}
                      title="Remove trace"
                    >
                      <IconX size={14} />
                    </ActionIcon>
                  </DraggableTraceChip>
                );
              })}
            </Group>
          </>
        ) : isStreamTracePanel(panel) ? (
          emptyMessage ? (
            emptyState
          ) : isStreamRawPanel(panel) ? (
            <StreamRawPanel
              panelId={panel.id}
              frames={streamFramesRef.get(panel.id) ?? []}
              overlayCount={traceOverlayCount}
              channelIndex={panel.sourceMode === "raw" ? panel.channelIndex : 0}
              tick={panelRevision}
              colorScheme={computedColorScheme}
              plotHeight={plotHeight}
              units={panel.stream?.units ?? null}
              extraSeries={traceExtraSeries}
              yScaleMode={panel.yScaleMode}
              yMin={panel.yMin}
              yMax={panel.yMax}
            />
          ) : (
            <StreamWaterfallPanel
              panelId={panel.id}
              frames={streamFramesRef.get(panel.id) ?? []}
              historyRows={panel.overlayCount}
              channelIndex={panel.sourceMode === "raw" ? panel.channelIndex : 0}
              tick={panelRevision}
              colorScheme={computedColorScheme}
              plotHeight={plotHeight}
              zScaleMode={panel.yScaleMode}
              zMin={panel.yMin}
              zMax={panel.yMax}
            />
          )
        ) : isStreamScalarPanel(panel) ? (
          emptyMessage ? (
            emptyState
          ) : (
            <PlotPanel
              panelId={panel.id}
              traces={[streamScalarTrace(panel)]}
              buffers={panelBuffers}
              tick={panelRevision}
              timeWindowS={panel.timeWindowS}
              colorScheme={computedColorScheme}
              plotHeight={plotHeight}
              yScaleMode={panel.yScaleMode}
              yMin={panel.yMin}
              yMax={panel.yMax}
            />
          )
        ) : isStreamParamsPanel(panel) ? (
          <StreamParamsPanel
            valuesByOutputId={streamParamsLatestRef.get(panel.id) ?? {}}
            selectedOutputIds={panel.outputIds}
            onCopyJson={(payload) => {
              void copyTextToClipboard("Params JSON", payload);
            }}
          />
        ) : isStreamBinStatsPanel(panel) ? (
          emptyMessage ? (
            emptyState
          ) : (
            <StreamBinStatsPanel
              panelId={panel.id}
              series={binStatsSnapshot?.series ?? null}
              overlaySeries={binStatsOverlays}
              fitOverlays={binStatsFits}
              xLabel={binStatsXLabel}
              uncertaintyMode={panel.uncertaintyMode}
              uncertaintyScale={panel.uncertaintyScale}
              showBinMarkers={panel.showBinMarkers}
              xOffset={panel.xOffset}
              xScale={panel.xScale}
              tick={panelRevision}
              colorScheme={computedColorScheme}
              plotHeight={plotHeight}
              yScaleMode={panel.yScaleMode}
              yMin={panel.yMin}
              yMax={panel.yMax}
            />
          )
        ) : isStreamBin2dPanel(panel) ? (
          <StreamBin2dPanel
            panelId={panel.id}
            series={bin2dSnapshot?.series ?? null}
            reducer={panel.reducer}
            tick={panelRevision}
            colorScheme={computedColorScheme}
            plotHeight={plotHeight}
            zScaleMode={panel.yScaleMode}
            zMin={panel.yMin}
            zMax={panel.yMax}
          />
        ) : null}
      </div>
      <PlotLegend
        items={legendItems}
        onRenameSeries={startSeriesRename}
        editingKey={renamingSeriesKey}
        editingValue={seriesRenameDraft}
        onEditingValueChange={setSeriesRenameDraft}
        onCommitRename={commitSeriesRename}
        onCancelRename={cancelSeriesRename}
      />
      <PanelStatusLine items={statusItems} />
    </ReorderableCardShell>
  );
}

/**
 * Memoized export — skips re-rendering when none of the props change.
 *
 * Mutable plot data stays in the telemetry/analysis contexts while the
 * high-frequency notification is panel-scoped. Configuration-context changes
 * can still rerender cards, but a sample for another panel cannot.
 */
export const PanelCard = memo(PanelCardImpl);
