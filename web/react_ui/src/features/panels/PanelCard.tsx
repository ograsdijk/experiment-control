import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  ActionIcon,
  Divider,
  Group,
  NumberInput,
  Popover,
  SegmentedControl,
  Select,
  Stack,
  Text,
  useComputedColorScheme,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCheck, IconSettings, IconX } from "@tabler/icons-react";

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
 * control or the per-kind advanced modal it opens. What used to be a row
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
    setPanelLayout,
    removeTraceFromPanel,
    setPanelTimeWindow,
    openPlotOptions,
    closePlotOptions,
    applyPlotOptionsAxis,
    setPlotOptionsAxisMode,
    setTelemetryYDisplayMode,
    setTelemetryYOffsetMode,
    setTelemetrySmoothingMode,
    setTelemetrySmoothingWindow,
    clearPanelBuffers,
    clearStreamPanelFrames,
    clearStreamBinStatsPanel,
    clearStreamBin2dPanel,
    setStreamAnalysisPanelWorkspace,
    setStreamAnalysisPanelOutput,
    openExpandedPlot,
    openStreamTraceOptionsModal,
    openStreamBin2dOptionsModal,
    openStreamParamsOptionsModal,
    openStreamBinStatsOptionsModal,
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

  // --- settings ------------------------------------------------------
  const supportsWorkspaceSelect =
    isStreamScalarPanel(panel) ||
    isStreamParamsPanel(panel) ||
    isStreamBinStatsPanel(panel) ||
    isStreamBin2dPanel(panel);
  const supportsOutputSelect = outputKindForPanel(panel) !== null;
  const advancedOptionsHandler = isStreamTracePanel(panel)
    ? () => openStreamTraceOptionsModal(panel.id)
    : isStreamParamsPanel(panel)
    ? () => openStreamParamsOptionsModal(panel.id)
    : isStreamBin2dPanel(panel)
    ? () => openStreamBin2dOptionsModal(panel.id)
    : isStreamBinStatsPanel(panel)
    ? () => openStreamBinStatsOptionsModal(panel.id)
    : null;

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
    <Popover
      opened={plotOptionsPanelId === panel.id}
      onChange={(opened) => {
        if (!opened && plotOptionsPanelId === panel.id) {
          closePlotOptions();
        }
      }}
      position="bottom-end"
      withArrow
      shadow="md"
      withinPortal
      zIndex={700}
      width={420}
    >
      <Popover.Target>
        <ActionIcon
          size="sm"
          variant="subtle"
          color="gray"
          aria-label="Panel settings"
          title="Settings"
          onClick={() => {
            if (plotOptionsPanelId === panel.id) {
              closePlotOptions();
              return;
            }
            openPlotOptions(panel.id);
          }}
        >
          <IconSettings size={15} />
        </ActionIcon>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="sm">
          {supportsWorkspaceSelect ? (
            <Stack gap={6}>
              <Text size="xs" fw={600} c="dimmed">
                Source
              </Text>
              <Select
                size="xs"
                searchable
                label="Workspace"
                placeholder="Select workspace"
                comboboxProps={{ zIndex: 800 }}
                data={streamWorkspaceOptions}
                value={panel.workspaceId}
                onChange={(value) =>
                  setStreamAnalysisPanelWorkspace(panel.id, value)
                }
              />
              {supportsOutputSelect ? (
                <Select
                  size="xs"
                  searchable
                  clearable
                  label="Output"
                  placeholder="Select output"
                  comboboxProps={{ zIndex: 800 }}
                  data={outputOptions}
                  value={"outputId" in panel ? panel.outputId : null}
                  onChange={(value) =>
                    setStreamAnalysisPanelOutput(panel.id, value)
                  }
                />
              ) : null}
            </Stack>
          ) : null}

          {!isStreamParamsPanel(panel) ? (
            <Stack gap={6}>
              <Text size="xs" fw={600} c="dimmed">
                Display
              </Text>
              <Group justify="space-between" align="center">
                <Text size="xs" c="dimmed">
                  {(isStreamWaterfallPanel(panel) ||
                    isStreamBin2dPanel(panel)
                    ? "Z"
                    : "Y") + " axis"}
                </Text>
                <SegmentedControl
                  size="xs"
                  value={panel.yScaleMode}
                  onChange={(value) =>
                    setPlotOptionsAxisMode(panel, value as YScaleMode)
                  }
                  data={[
                    { value: "auto", label: "Auto" },
                    { value: "manual", label: "Manual" },
                  ]}
                />
              </Group>
              {panel.yScaleMode === "manual" ? (
                <>
                  <Group grow>
                    <NumberInput
                      size="xs"
                      label="Min"
                      value={yAxisDraftMin}
                      onChange={setYAxisDraftMin}
                    />
                    <NumberInput
                      size="xs"
                      label="Max"
                      value={yAxisDraftMax}
                      onChange={setYAxisDraftMax}
                    />
                  </Group>
                  <Group justify="space-between" align="center">
                    <Text size="xs" c="dimmed">
                      {yAxisAutoRange
                        ? `auto: ${yAxisAutoRange.min.toFixed(
                            4
                          )} .. ${yAxisAutoRange.max.toFixed(4)}`
                        : "auto range unavailable"}
                    </Text>
                    <ActionIcon
                      variant="light"
                      color="teal"
                      size="sm"
                      onClick={() => applyPlotOptionsAxis(panel.id)}
                      disabled={yAxisDraftInvalid}
                      aria-label="Apply axis range"
                      title="Apply axis range"
                    >
                      <IconCheck size={14} />
                    </ActionIcon>
                  </Group>
                </>
              ) : (
                <Text size="xs" c="dimmed">
                  {yAxisAutoRange
                    ? `auto: ${yAxisAutoRange.min.toFixed(
                        4
                      )} .. ${yAxisAutoRange.max.toFixed(4)}`
                    : "auto range unavailable"}
                </Text>
              )}
            </Stack>
          ) : null}

          {isTelemetryPanel(panel) ? (
            <Stack gap={6}>
              <Group grow>
                <NumberInput
                  size="xs"
                  label="Window (s)"
                  min={5}
                  max={600}
                  value={panel.timeWindowS}
                  onChange={(value) =>
                    setPanelTimeWindow(panel.id, Number(value))
                  }
                />
              </Group>
              <Group justify="space-between" align="center">
                <Text size="xs" c="dimmed">
                  Display
                </Text>
                <SegmentedControl
                  size="xs"
                  value={panel.yDisplayMode}
                  data={[
                    { value: "absolute", label: "Abs" },
                    { value: "delta", label: "Delta" },
                  ]}
                  onChange={(value) => {
                    const nextMode = value as YDisplayMode;
                    if (
                      nextMode === "delta" &&
                      telemetryNumericTraceCount === 0
                    ) {
                      notifications.show({
                        color: "yellow",
                        title: "No numeric traces",
                        message:
                          "Delta display requires at least one numeric telemetry trace.",
                      });
                      return;
                    }
                    setTelemetryYDisplayMode(panel.id, nextMode);
                  }}
                />
              </Group>
              {panel.yDisplayMode === "delta" ? (
                <>
                  <Group justify="space-between" align="center">
                    <Text size="xs" c="dimmed">
                      Offset
                    </Text>
                    <SegmentedControl
                      size="xs"
                      value={panel.yOffsetMode}
                      data={[
                        { value: "auto", label: "Auto" },
                        { value: "freeze", label: "Freeze" },
                      ]}
                      onChange={(value) => {
                        const nextMode = value as YOffsetMode;
                        if (nextMode === "auto") {
                          setTelemetryYOffsetMode(panel.id, "auto");
                          return;
                        }
                        if (
                          typeof telemetryOffset !== "number" ||
                          !Number.isFinite(telemetryOffset)
                        ) {
                          notifications.show({
                            color: "yellow",
                            title: "Offset unavailable",
                            message:
                              "No numeric telemetry samples available to freeze offset yet.",
                          });
                          return;
                        }
                        setTelemetryYOffsetMode(
                          panel.id,
                          "freeze",
                          telemetryOffset
                        );
                      }}
                    />
                  </Group>
                  <Text size="xs" c="dimmed">
                    offset: {telemetryOffsetLabel}
                    {telemetryOffsetFullLabel &&
                    telemetryOffsetFullLabel !== telemetryOffsetLabel
                      ? ` (${telemetryOffsetFullLabel})`
                      : ""}
                  </Text>
                </>
              ) : null}
              <Group justify="space-between" align="center">
                <Text size="xs" c="dimmed">
                  Smoothing
                </Text>
                <SegmentedControl
                  size="xs"
                  value={panel.smoothingMode}
                  data={[
                    { value: "none", label: "Off" },
                    { value: "sma", label: "SMA" },
                    { value: "ema", label: "EMA" },
                  ]}
                  onChange={(value) =>
                    setTelemetrySmoothingMode(
                      panel.id,
                      value as TelemetrySmoothingMode
                    )
                  }
                />
              </Group>
              {panel.smoothingMode !== "none" ? (
                <NumberInput
                  size="xs"
                  label="Smoothing window (s)"
                  min={1}
                  max={300}
                  value={panel.smoothingWindowS}
                  onChange={(value) =>
                    setTelemetrySmoothingWindow(panel.id, Number(value))
                  }
                />
              ) : null}
            </Stack>
          ) : null}

          {isStreamScalarPanel(panel) ? (
            <NumberInput
              size="xs"
              label="Window (s)"
              min={5}
              max={600}
              value={panel.timeWindowS}
              onChange={(value) => setPanelTimeWindow(panel.id, Number(value))}
            />
          ) : null}

          <Divider />
          <Stack gap={6}>
            <Text size="xs" fw={600} c="dimmed">
              Card
            </Text>
            <Group grow align="flex-end">
              <NumberInput
                size="xs"
                label="Plot height (px)"
                description="Empty follows the card"
                placeholder="auto"
                min={MIN_PANEL_HEIGHT_PX}
                max={MAX_PANEL_HEIGHT_PX}
                value={panel.heightPx ?? ""}
                onChange={(value) =>
                  setPanelLayout(panel.id, {
                    heightPx:
                      value === "" || value === null ? null : Number(value),
                  })
                }
              />
              <div>
                <Text size="xs" c="dimmed" mb={4}>
                  Width
                </Text>
                <SegmentedControl
                  size="xs"
                  fullWidth
                  value={String(panel.colSpan ?? 1)}
                  data={[
                    { value: "1", label: "1" },
                    { value: "2", label: "2" },
                    { value: "3", label: "3" },
                  ]}
                  onChange={(value) =>
                    setPanelLayout(panel.id, { colSpan: Number(value) })
                  }
                />
              </div>
            </Group>
          </Stack>

          {statusDetailRows.length > 0 ? (
            <>
              <Divider />
              <Stack gap={2}>
                <Text size="xs" fw={600} c="dimmed">
                  Status
                </Text>
                {statusDetailRows.map(([label, value]) => (
                  <Group
                    key={label}
                    justify="space-between"
                    gap="xs"
                    wrap="nowrap"
                  >
                    <Text size="xs" c="dimmed">
                      {label}
                    </Text>
                    <Text size="xs" style={{ textAlign: "right" }}>
                      {value}
                    </Text>
                  </Group>
                ))}
              </Stack>
            </>
          ) : null}

          {advancedOptionsHandler ? (
            <Text
              size="xs"
              c="teal"
              style={{ cursor: "pointer" }}
              onClick={() => {
                closePlotOptions();
                advancedOptionsHandler();
              }}
            >
              Open advanced options…
            </Text>
          ) : null}
        </Stack>
      </Popover.Dropdown>
    </Popover>
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

  // Colour order mirrors the data order the plot components build:
  // primary series first, then extras/overlays, then fit curves.
  const legendItems: PlotLegendItem[] = [];
  if (isStreamRawPanel(panel) && traceExtraSeries.length > 0) {
    legendItems.push({
      key: "primary",
      label:
        panel.sourceMode === "dag"
          ? panel.outputId ?? "output"
          : `ch ${panel.channelIndex}`,
      color: traceColorAt(0),
    });
    traceExtraSeries.forEach((series, idx) => {
      legendItems.push({
        key: `extra-${series.label}-${idx}`,
        label: series.label,
        color: traceColorAt(traceOverlayCount + idx),
      });
    });
  }
  if (
    isStreamBinStatsPanel(panel) &&
    (binStatsOverlays.length > 0 || binStatsFits.length > 0)
  ) {
    legendItems.push({
      key: "mean",
      label: panel.outputId ?? "mean",
      color: binStatsMeanStroke(isDark),
    });
    binStatsOverlays.forEach((series, idx) => {
      legendItems.push({
        key: `overlay-${series.label}-${idx}`,
        label: series.label,
        color: BIN_STATS_OVERLAY_COLORS[idx % BIN_STATS_OVERLAY_COLORS.length],
      });
    });
    binStatsFits.forEach((curve, idx) => {
      legendItems.push({
        key: `fit-${curve.label}-${idx}`,
        label: `${curve.label} (fit)`,
        color:
          BIN_STATS_FIT_OVERLAY_COLORS[
            idx % BIN_STATS_FIT_OVERLAY_COLORS.length
          ],
      });
    });
  }

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
                    {trace.deviceId}.{trace.signal}
                    {units}
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
      <PlotLegend items={legendItems} />
      <PanelStatusLine
        connected={linkConnected}
        linkLabel={linkLabel}
        items={statusItems}
      />
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
