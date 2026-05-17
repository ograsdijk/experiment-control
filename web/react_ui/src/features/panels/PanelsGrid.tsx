import { type CSSProperties, type MutableRefObject } from "react";
import {
  ActionIcon,
  Badge,
  Button,
  Group,
  NumberInput,
  Popover,
  SegmentedControl,
  Select,
  Stack,
  Text,
  TextInput,
  useComputedColorScheme,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconArrowsMaximize,
  IconCheck,
  IconPencil,
  IconSettings,
  IconStar,
  IconTrash,
  IconX,
} from "@tabler/icons-react";
import { SortableContext, rectSortingStrategy } from "@dnd-kit/sortable";

import { PlotPanel } from "../../components/PlotPanel";
import { StreamBin2dPanel } from "../../components/StreamBin2dPanel";
import { StreamBinStatsPanel } from "../../components/StreamBinStatsPanel";
import { StreamParamsPanel } from "../../components/StreamParamsPanel";
import { StreamRawPanel } from "../../components/StreamRawPanel";
import { StreamWaterfallPanel } from "../../components/StreamWaterfallPanel";
import { DraggableTraceChip } from "../../components/DraggableTraceChip";
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
  PlotStreamBinStatsPanelState,
  PlotStreamPanelState,
  PlotStreamWaterfallPanelState,
  PlotTelemetryPanelState,
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
import { useStreamAnalysis } from "../stream_analysis/StreamAnalysisContext";
import { useTelemetry } from "../telemetry/TelemetryContext";
import { useLayout } from "../layout/LayoutContext";
import { colorWithAlpha, traceColorAt } from "../../utils/traceColors";
import { usePanels } from "./PanelsContext";

/**
 * Panel render loop — `<PanelsGrid>` owns the SortableContext wrapper
 * and the per-panel render iteration that used to live inline in
 * App.tsx (~1037 LOC). Each iteration renders one
 * `<ReorderableCardShell>` containing the title bar, plot-options
 * popover, per-kind body, and clear/expand/remove actions.
 *
 * The component pulls a lot of state from contexts directly (panels,
 * editor state, refs, workspaces, layout grid styles, color scheme)
 * to keep the prop surface manageable. App-local handlers from the
 * various panel-handler hooks come in as props grouped into
 * `helpers` and `handlers` bags.
 *
 * This is structural-only (PR 1 of the original 3-PR plan). The
 * per-kind body split (PR 2) and memoization (PR 3, P9) are
 * follow-ups.
 */

const PANEL_SORTABLE_PREFIX = "panel:";
function panelSortableId(panelId: string): string {
  return `${PANEL_SORTABLE_PREFIX}${panelId}`;
}

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

export interface PanelsGridHelpers {
  resolveTelemetryPanelOffset: (
    panel: PlotTelemetryPanelState
  ) => number | null;
  streamTraceOverlaySeries: (
    panel: PlotStreamPanelState | PlotStreamWaterfallPanelState
  ) => Array<{ label: string; values: number[] }>;
  streamBinStatsOverlaySeries: (
    panel: PlotStreamBinStatsPanelState
  ) => Array<{ label: string; values: number[] }>;
  streamBinStatsFitOverlayCurves: (
    panel: PlotStreamBinStatsPanelState
  ) => Array<{ label: string; x: number[]; y: number[] }>;
  isExpandablePlotPanel: (panel: PlotPanelState) => boolean;
  copyTextToClipboard: (label: string, text: string) => Promise<void>;
}

export interface PanelsGridHandlers {
  startPanelTitleEdit: (panel: PlotPanelState) => void;
  commitPanelTitleEdit: () => void;
  cancelPanelTitleEdit: () => void;
  removePanel: (panelId: string) => void;
  removeTraceFromPanel: (
    panelId: string,
    trace: { deviceId: string; signal: string }
  ) => void;
  setPanelTimeWindow: (panelId: string, value: number) => void;
  openPlotOptions: (panelId: string) => void;
  closePlotOptions: () => void;
  applyPlotOptionsAxis: (panelId: string) => void;
  setPlotOptionsAxisMode: (panel: PlotPanelState, mode: YScaleMode) => void;
  setTelemetryYDisplayMode: (panelId: string, mode: YDisplayMode) => void;
  setTelemetryYOffsetMode: (
    panelId: string,
    mode: YOffsetMode,
    value?: number | null
  ) => void;
  setTelemetrySmoothingMode: (
    panelId: string,
    mode: TelemetrySmoothingMode
  ) => void;
  setTelemetrySmoothingWindow: (panelId: string, value: number) => void;
  clearPanelBuffers: (panelId: string) => void;
  clearStreamPanelFrames: (panelId: string) => void;
  clearStreamBinStatsPanel: (panelId: string) => Promise<void>;
  clearStreamBin2dPanel: (panelId: string) => Promise<void>;
  setStreamAnalysisPanelWorkspace: (
    panelId: string,
    workspaceId: string | null
  ) => void;
  setStreamAnalysisPanelOutput: (
    panelId: string,
    outputId: string | null
  ) => void;
  openExpandedPlot: (panelId: string) => void;
  openStreamTraceOptionsModal: (panelId: string) => void;
  openStreamBin2dOptionsModal: (panelId: string) => void;
  openStreamParamsOptionsModal: (panelId: string) => void;
  openStreamBinStatsOptionsModal: (panelId: string) => void;
}

export interface PanelsGridProps {
  streamWorkspaceOptions: Array<{ value: string; label: string }>;
  yAxisDraftInvalid: boolean;
  streamWsConnected: boolean;
  streamAnalysisWsConnected: boolean;
  activeUiDrag: { kind: string; panelId?: string } | null;
  helpers: PanelsGridHelpers;
  handlers: PanelsGridHandlers;
}

export function PanelsGrid({
  streamWorkspaceOptions,
  yAxisDraftInvalid,
  streamWsConnected,
  streamAnalysisWsConnected,
  activeUiDrag,
  helpers,
  handlers,
}: PanelsGridProps) {
  const {
    panels,
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
    plotTick,
    setPlotTick,
  } = usePanels();
  const {
    buffersRef,
    streamFramesRef,
    streamBinStatsRef,
    streamBin2dRef,
    streamParamsLatestRef,
  } = useTelemetry();
  const { streamWorkspaces } = useStreamAnalysis();
  const { plotGridStyle, plotGridRef } = useLayout();
  const computedColorScheme = useComputedColorScheme("light");

  const {
    resolveTelemetryPanelOffset,
    streamTraceOverlaySeries,
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

  return (
    <SortableContext
      items={panels.map((panel) => panelSortableId(panel.id))}
      strategy={rectSortingStrategy}
    >
      <div
        className="plot-grid"
        style={plotGridStyle as CSSProperties}
        ref={plotGridRef as MutableRefObject<HTMLDivElement | null>}
      >
                {panels.map((panel) => {
                  const isActive = panel.id === activePanelId;
                  const panelBuffers = buffersRef.get(panel.id) ?? new Map();
                  const streamWorkspace =
                    isStreamScalarPanel(panel) ||
                    isStreamParamsPanel(panel) ||
                    isStreamBinStatsPanel(panel) ||
                    isStreamBin2dPanel(panel) ||
                    (isStreamTracePanel(panel) && panel.sourceMode === "dag")
                      ? streamWorkspaces[panel.workspaceId] ?? null
                      : null;
                  const integralOutputOptions = isStreamScalarPanel(panel)
                    ? workspaceOutputOptionsByKind(streamWorkspace, "scalar")
                    : [];
                  const binStatsXLabel = isStreamBinStatsPanel(panel)
                    ? workspaceXAxisLabel(streamWorkspace, panel.outputId)
                    : DEFAULT_STREAM_CONTEXT_FIELD;
                  const binStatsSnapshot = isStreamBinStatsPanel(panel)
                    ? streamBinStatsRef.get(panel.id) ?? null
                    : null;
                  const bin2dSnapshot = isStreamBin2dPanel(panel)
                    ? streamBin2dRef.get(panel.id) ?? null
                    : null;
                  const bin2dXLabel = isStreamBin2dPanel(panel)
                    ? workspaceBin2dAxisLabel(streamWorkspace, panel.outputId, "x")
                    : DEFAULT_STREAM_CONTEXT_FIELD;
                  const bin2dYLabel = isStreamBin2dPanel(panel)
                    ? workspaceBin2dAxisLabel(streamWorkspace, panel.outputId, "y")
                    : "context_y";
                  const telemetryNumericTraceCount = isTelemetryPanel(panel)
                    ? panel.traces.filter((trace) => trace.valueKind !== "boolean")
                        .length
                    : 0;
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
                  const telemetryOffsetUnit = (() => {
                    if (!isTelemetryPanel(panel)) {
                      return "";
                    }
                    const units = panel.traces
                      .filter((trace) => trace.valueKind !== "boolean")
                      .map((trace) => (typeof trace.units === "string" ? trace.units.trim() : ""))
                      .filter((unit) => unit.length > 0);
                    if (units.length === 0) {
                      return "";
                    }
                    const unique = new Set(units);
                    return unique.size === 1 ? units[0] : "";
                  })();
                  const telemetryOffsetLabel = telemetryOffsetUnit
                    ? `${telemetryOffsetCompact} ${telemetryOffsetUnit}`
                    : telemetryOffsetCompact;
                  const telemetryOffsetFullLabel =
                    telemetryOffsetFull !== null
                      ? telemetryOffsetUnit
                        ? `${telemetryOffsetFull} ${telemetryOffsetUnit}`
                        : telemetryOffsetFull
                      : null;
                  return (
                    <ReorderableCardShell
                      key={panel.id}
                      id={panelSortableId(panel.id)}
                      data={{ kind: "panel", panelId: panel.id }}
                      className="plot-workspace-card"
                      dataPanelCardId={panel.id}
                      dragHandleTitle="Drag from border to reorder panels"
                      style={{
                        border:
                          isActive
                            ? "2px solid #0e9f9a"
                            : "1px solid var(--card-border)",
                        background: "var(--card)",
                        position: "relative",
                      }}
                    >
                      <Group justify="space-between" align="center">
                        <Group gap="sm" align="center">
                          {editingPanelId === panel.id ? (
                            <Group gap={6} align="center">
                              <TextInput
                                size="xs"
                                w={180}
                                value={panelTitleDraft}
                                onChange={(event) =>
                                  setPanelTitleDraft(event.currentTarget.value)
                                }
                                onKeyDown={(event) => {
                                  if (event.key === "Enter") {
                                    event.preventDefault();
                                    commitPanelTitleEdit();
                                    return;
                                  }
                                  if (event.key === "Escape") {
                                    event.preventDefault();
                                    cancelPanelTitleEdit();
                                  }
                                }}
                                autoFocus
                                placeholder={panel.id}
                              />
                              <ActionIcon
                                size="sm"
                                variant="light"
                                color="teal"
                                onClick={commitPanelTitleEdit}
                              >
                                <IconCheck size={14} />
                              </ActionIcon>
                              <ActionIcon
                                size="sm"
                                variant="light"
                                color="gray"
                                onClick={cancelPanelTitleEdit}
                              >
                                <IconX size={14} />
                              </ActionIcon>
                            </Group>
                          ) : (
                            <Group gap={6} align="center">
                              <Text fw={600}>{panel.title}</Text>
                              <ActionIcon
                                size="sm"
                                variant="subtle"
                                color="gray"
                                onClick={() => startPanelTitleEdit(panel)}
                              >
                                <IconPencil size={14} />
                              </ActionIcon>
                            </Group>
                          )}
                          {activeUiDrag?.kind === "panel" &&
                          activeUiDrag.panelId === panel.id && (
                            <Badge variant="light" color="blue">
                              Dragging
                            </Badge>
                          )}
                          {isActive ? (
                            <Badge variant="light" color="teal">
                              Active
                            </Badge>
                          ) : (
                            <Button
                              size="xs"
                              variant="light"
                              leftSection={<IconStar size={14} />}
                              onClick={() => setActivePanelId(panel.id)}
                            >
                              Set active
                            </Button>
                          )}
                          <Badge
                            variant="light"
                            color={
                              isTelemetryPanel(panel)
                                ? "teal"
                                : isStreamRawPanel(panel)
                                ? "orange"
                                : isStreamWaterfallPanel(panel)
                                ? "cyan"
                                : isStreamParamsPanel(panel)
                                ? "lime"
                                : isStreamBinStatsPanel(panel)
                                ? "blue"
                                : isStreamBin2dPanel(panel)
                                ? "violet"
                                : "green"
                            }
                          >
                            {isTelemetryPanel(panel)
                              ? "Telemetry"
                              : isStreamRawPanel(panel)
                              ? "Stream trace"
                              : isStreamWaterfallPanel(panel)
                              ? "Stream waterfall"
                              : isStreamParamsPanel(panel)
                              ? "Stream params"
                              : isStreamBinStatsPanel(panel)
                              ? "Stream bin stats"
                              : isStreamBin2dPanel(panel)
                              ? "Stream 2D bins"
                              : "Stream scalar"}
                          </Badge>
                          <Popover
                            opened={plotOptionsPanelId === panel.id}
                            onChange={(opened) => {
                              if (!opened && plotOptionsPanelId === panel.id) {
                                closePlotOptions();
                              }
                            }}
                            position="bottom-start"
                            withArrow
                            shadow="md"
                            withinPortal
                            zIndex={700}
                            width={420}
                          >
                            <Popover.Target>
                              <Button
                                size="xs"
                                variant="light"
                                leftSection={<IconSettings size={14} />}
                                style={{ marginLeft: "auto" }}
                                onClick={() => {
                                  if (plotOptionsPanelId === panel.id) {
                                    closePlotOptions();
                                    return;
                                  }
                                  openPlotOptions(panel.id);
                                }}
                              >
                                Plot options
                              </Button>
                            </Popover.Target>
                            <Popover.Dropdown>
                              <Stack gap="sm">
                                {!isStreamParamsPanel(panel) ? (
                                  <Stack gap={6}>
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
                                          <Button
                                            size="xs"
                                            variant="light"
                                            onClick={() => applyPlotOptionsAxis(panel.id)}
                                            disabled={yAxisDraftInvalid}
                                          >
                                            Apply axis
                                          </Button>
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
                                          setTelemetrySmoothingWindow(
                                            panel.id,
                                            Number(value)
                                          )
                                        }
                                      />
                                    ) : null}
                                  </Stack>
                                ) : null}
                                {isStreamScalarPanel(panel) ? (
                                  <Stack gap={6}>
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
                                    <Select
                                      size="xs"
                                      searchable
                                      clearable
                                      label="Scalar output"
                                      placeholder="Select scalar output"
                                      comboboxProps={{ zIndex: 800 }}
                                      data={integralOutputOptions}
                                      value={panel.outputId}
                                      onChange={(value) =>
                                        setStreamAnalysisPanelOutput(panel.id, value)
                                      }
                                    />
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
                                  </Stack>
                                ) : null}
                                {(isStreamTracePanel(panel) ||
                                  isStreamParamsPanel(panel) ||
                                  isStreamBinStatsPanel(panel) ||
                                  isStreamBin2dPanel(panel)) && (
                                  <Button
                                    size="xs"
                                    variant="light"
                                    onClick={() => {
                                      closePlotOptions();
                                      if (isStreamTracePanel(panel)) {
                                        openStreamTraceOptionsModal(panel.id);
                                        return;
                                      }
                                      if (isStreamParamsPanel(panel)) {
                                        openStreamParamsOptionsModal(panel.id);
                                        return;
                                      }
                                      if (isStreamBin2dPanel(panel)) {
                                        openStreamBin2dOptionsModal(panel.id);
                                        return;
                                      }
                                      openStreamBinStatsOptionsModal(panel.id);
                                    }}
                                  >
                                    Open advanced options
                                  </Button>
                                )}
                              </Stack>
                            </Popover.Dropdown>
                          </Popover>
                          <Button
                            size="xs"
                            variant="light"
                            onClick={() => {
                              if (isTelemetryPanel(panel) || isStreamScalarPanel(panel)) {
                                clearPanelBuffers(panel.id);
                                return;
                              }
                              if (isStreamParamsPanel(panel)) {
                                streamParamsLatestRef.set(panel.id, {});
                                setPlotTick((tick) => tick + 1);
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
                            }}
                          >
                            {isStreamBinStatsPanel(panel) || isStreamBin2dPanel(panel)
                              ? "Clear binned data"
                              : "Clear"}
                          </Button>
                        </Group>
                        <Group gap="xs">
                            {isExpandablePlotPanel(panel) ? (
                              <ActionIcon
                                variant="light"
                                color="gray"
                                onClick={() => openExpandedPlot(panel.id)}
                                title="Enlarge plot"
                              >
                                <IconArrowsMaximize size={14} />
                              </ActionIcon>
                            ) : null}
                            <ActionIcon
                              variant="light"
                              color="red"
                            onClick={() => removePanel(panel.id)}
                            disabled={panels.length <= 1}
                          >
                            <IconTrash size={14} />
                          </ActionIcon>
                        </Group>
                      </Group>
                      {isTelemetryPanel(panel) ? (
                        <>
                          <PlotPanel
                            traces={panel.traces}
                            buffers={panelBuffers}
                            tick={plotTick}
                            timeWindowS={panel.timeWindowS}
                            colorScheme={computedColorScheme}
                            yScaleMode={panel.yScaleMode}
                            yMin={panel.yMin}
                            yMax={panel.yMax}
                            yDisplayMode={panel.yDisplayMode}
                            yOffset={telemetryOffset}
                            smoothingMode={panel.smoothingMode}
                            smoothingWindowS={panel.smoothingWindowS}
                          />
                          <Group gap="sm" wrap="wrap" mt="sm">
                            {panel.traces.map((trace, traceIndex) => {
                              const traceColor = traceColorAt(traceIndex);
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
                                      computedColorScheme === "dark" ? 0.22 : 0.14
                                    ),
                                    border: `1px solid ${colorWithAlpha(
                                      traceColor,
                                      computedColorScheme === "dark" ? 0.45 : 0.3
                                    )}`,
                                  }}
                                >
                                  {trace.deviceId}.{trace.signal}
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
                        <>
                          {isStreamRawPanel(panel) ? (
                            <StreamRawPanel
                              frames={streamFramesRef.get(panel.id) ?? []}
                              overlayCount={panel.overlayCount}
                              channelIndex={panel.sourceMode === "raw" ? panel.channelIndex : 0}
                              tick={plotTick}
                              colorScheme={computedColorScheme}
                              units={panel.stream?.units ?? null}
                              extraSeries={
                                panel.sourceMode === "dag"
                                  ? streamTraceOverlaySeries(panel)
                                  : []
                              }
                              yScaleMode={panel.yScaleMode}
                              yMin={panel.yMin}
                              yMax={panel.yMax}
                            />
                          ) : (
                            <StreamWaterfallPanel
                              frames={streamFramesRef.get(panel.id) ?? []}
                              historyRows={panel.overlayCount}
                              channelIndex={panel.sourceMode === "raw" ? panel.channelIndex : 0}
                              tick={plotTick}
                              colorScheme={computedColorScheme}
                              zScaleMode={panel.yScaleMode}
                              zMin={panel.yMin}
                              zMax={panel.yMax}
                            />
                          )}
                          <Group gap="sm" wrap="wrap" mt="sm">
                            <Badge
                              variant="light"
                              color={panel.sourceMode === "raw" ? "orange" : "teal"}
                              onClick={() => openStreamTraceOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              source: {panel.sourceMode}
                            </Badge>
                            {panel.sourceMode === "raw" ? (
                              <>
                                {panel.stream ? (
                                  <Badge
                                    variant="light"
                                    color="orange"
                                    onClick={() => openStreamTraceOptionsModal(panel.id)}
                                    style={{ cursor: "pointer" }}
                                  >
                                    {panel.stream.deviceId}.{panel.stream.stream}
                                  </Badge>
                                ) : (
                                  <Text size="xs" c="dimmed">
                                    Select a stream to start plotting raw frames.
                                  </Text>
                                )}
                                {panel.stream &&
                                inferChannelCountFromShape(panel.stream.shape) > 1 ? (
                                  <Badge
                                    variant="light"
                                    color="indigo"
                                    onClick={() => openStreamTraceOptionsModal(panel.id)}
                                    style={{ cursor: "pointer" }}
                                  >
                                    ch: {panel.channelIndex}
                                  </Badge>
                                ) : null}
                              </>
                            ) : (
                              <>
                                {streamWorkspace ? (
                                  <Badge
                                    variant="light"
                                    color="teal"
                                    onClick={() => openStreamTraceOptionsModal(panel.id)}
                                    style={{ cursor: "pointer" }}
                                  >
                                    {streamWorkspace.name}
                                  </Badge>
                                ) : null}
                                {streamWorkspace?.stream ? (
                                  <Badge
                                    variant="light"
                                    color="orange"
                                    onClick={() => openStreamTraceOptionsModal(panel.id)}
                                    style={{ cursor: "pointer" }}
                                  >
                                    {streamWorkspace.stream.deviceId}.{streamWorkspace.stream.stream}
                                  </Badge>
                                ) : (
                                  <Text size="xs" c="dimmed">
                                    Bind this panel to a configured DAG workspace.
                                  </Text>
                                )}
                                <Badge
                                  variant="light"
                                  color="teal"
                                  onClick={() => openStreamTraceOptionsModal(panel.id)}
                                  style={{ cursor: "pointer" }}
                                >
                                  output: {panel.outputId ?? "none"}
                                </Badge>
                              </>
                            )}
                            {panel.overlayCount > 1 ? (
                              <Badge
                                variant="light"
                                color="indigo"
                                onClick={() => openStreamTraceOptionsModal(panel.id)}
                                style={{ cursor: "pointer" }}
                              >
                                {isStreamWaterfallPanel(panel) ? "rows" : "N"}:{" "}
                                {panel.overlayCount}
                              </Badge>
                            ) : null}
                            {panel.rollingWindow > 1 ? (
                              <Badge
                                variant="light"
                                color="indigo"
                                onClick={() => openStreamTraceOptionsModal(panel.id)}
                                style={{ cursor: "pointer" }}
                              >
                                avg({panel.averageMode}): {panel.rollingWindow}
                              </Badge>
                            ) : null}
                            <Badge
                              variant="light"
                              color="indigo"
                              onClick={() => openStreamTraceOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              decimator:{" "}
                              {panel.traceDecimator === "minmax"
                                ? "min-max"
                                : panel.traceDecimator}
                            </Badge>
                            <Badge
                              variant="light"
                              color="indigo"
                              onClick={() => openStreamTraceOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              pts: {panel.traceMaxPoints}
                            </Badge>
                            <Badge
                              variant="light"
                              color="indigo"
                              onClick={() => openStreamTraceOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              hz: {panel.traceMaxFps.toFixed(1)}
                            </Badge>
                            <Badge variant="light" color="indigo">
                              {isStreamWaterfallPanel(panel) ? "z" : "y"}:{" "}
                              {panel.yScaleMode === "manual" &&
                              Number.isFinite(panel.yMin ?? NaN) &&
                              Number.isFinite(panel.yMax ?? NaN)
                                ? `manual (${Number(panel.yMin).toPrecision(4)}, ${Number(
                                    panel.yMax
                                  ).toPrecision(4)})`
                                : "auto"}
                            </Badge>
                            <Badge
                              variant="light"
                              color={
                                panel.sourceMode === "raw"
                                  ? streamWsConnected
                                    ? "teal"
                                    : "red"
                                  : streamAnalysisWsConnected
                                  ? "teal"
                                  : "red"
                              }
                            >
                              {panel.sourceMode === "raw" ? "stream" : "analysis"} link:{" "}
                              {panel.sourceMode === "raw"
                                ? streamWsConnected
                                  ? "connected"
                                  : "disconnected"
                                : streamAnalysisWsConnected
                                ? "connected"
                                : "disconnected"}
                            </Badge>
                          </Group>
                        </>
                      ) : isStreamScalarPanel(panel) ? (
                        <>
                          <PlotPanel
                            traces={[streamScalarTrace(panel)]}
                            buffers={panelBuffers}
                            tick={plotTick}
                            timeWindowS={panel.timeWindowS}
                            colorScheme={computedColorScheme}
                            yScaleMode={panel.yScaleMode}
                            yMin={panel.yMin}
                            yMax={panel.yMax}
                          />
                          <Group gap="sm" wrap="wrap" mt="sm">
                            {streamWorkspace ? (
                              <Badge variant="light" color="teal">
                                {streamWorkspace.name}
                              </Badge>
                            ) : null}
                            {streamWorkspace?.stream ? (
                              <Badge variant="light" color="green">
                                {streamWorkspace.stream.deviceId}.{streamWorkspace.stream.stream}
                              </Badge>
                            ) : (
                              <Text size="xs" c="dimmed">
                                Bind this panel to a configured DAG workspace.
                              </Text>
                            )}
                            <Badge variant="light" color="teal">
                              output: {panel.outputId ?? "none"}
                            </Badge>
                            <Text size="xs" c="dimmed">
                              Analysis link{" "}
                              {streamAnalysisWsConnected ? "connected" : "disconnected"}
                            </Text>
                          </Group>
                        </>
                      ) : isStreamParamsPanel(panel) ? (
                        <>
                          <StreamParamsPanel
                            valuesByOutputId={streamParamsLatestRef.get(panel.id) ?? {}}
                            selectedOutputIds={panel.outputIds}
                            onCopyJson={(payload) => {
                              void copyTextToClipboard("Params JSON", payload);
                            }}
                          />
                          <Group gap="sm" wrap="wrap" mt="sm">
                            {streamWorkspace ? (
                              <Badge
                                variant="light"
                                color="teal"
                                onClick={() => openStreamParamsOptionsModal(panel.id)}
                                style={{ cursor: "pointer" }}
                              >
                                {streamWorkspace.name}
                              </Badge>
                            ) : null}
                            <Badge
                              variant="light"
                              color="indigo"
                              onClick={() => openStreamParamsOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              selected: {panel.outputIds.length}
                            </Badge>
                            <Badge
                              variant="light"
                              color={streamAnalysisWsConnected ? "teal" : "red"}
                            >
                              analysis link:{" "}
                              {streamAnalysisWsConnected ? "connected" : "disconnected"}
                            </Badge>
                          </Group>
                        </>
                      ) : isStreamBinStatsPanel(panel) ? (
                        <>
                        <StreamBinStatsPanel
                          series={binStatsSnapshot?.series ?? null}
                          overlaySeries={streamBinStatsOverlaySeries(panel)}
                          fitOverlays={streamBinStatsFitOverlayCurves(panel)}
                          xLabel={binStatsXLabel}
                          uncertaintyMode={panel.uncertaintyMode}
                          uncertaintyScale={panel.uncertaintyScale}
                          showBinMarkers={panel.showBinMarkers}
                            tick={plotTick}
                            colorScheme={computedColorScheme}
                            yScaleMode={panel.yScaleMode}
                            yMin={panel.yMin}
                            yMax={panel.yMax}
                          />
                          <Group gap="sm" wrap="wrap" mt="sm">
                            {streamWorkspace ? (
                              <Badge
                                variant="light"
                                color="teal"
                                onClick={() => openStreamBinStatsOptionsModal(panel.id)}
                                style={{ cursor: "pointer" }}
                              >
                                {streamWorkspace.name}
                              </Badge>
                            ) : null}
                            {streamWorkspace?.stream ? (
                              <Badge
                                variant="light"
                                color="blue"
                                onClick={() => openStreamBinStatsOptionsModal(panel.id)}
                                style={{ cursor: "pointer" }}
                              >
                                {streamWorkspace.stream.deviceId}.{streamWorkspace.stream.stream}
                              </Badge>
                            ) : (
                              <Text size="xs" c="dimmed">
                                Bind this panel to a configured DAG workspace.
                              </Text>
                            )}
                            <Badge variant="light" color="indigo">
                              x: {binStatsXLabel}
                            </Badge>
                            <Badge
                              variant="light"
                              color="indigo"
                              onClick={() => openStreamBinStatsOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              output: {panel.outputId ?? "none"}
                            </Badge>
                            <Badge variant="light" color="indigo">
                              bins:{" "}
                              {(() => {
                                const active =
                                  binStatsSnapshot?.populatedBinCount ??
                                  binStatsSnapshot?.activeBinCount ??
                                  null;
                                const max = binStatsSnapshot?.maxBinCount ?? null;
                                if (active === null || max === null || max <= 0) {
                                  return "n/a";
                                }
                                return `${active}/${max}`;
                              })()}
                            </Badge>
                            <Badge
                              variant="light"
                              color="indigo"
                              onClick={() => openStreamBinStatsOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              mode: {panel.uncertaintyMode}
                            </Badge>
                            {panel.uncertaintyScale !== 1 ? (
                              <Badge
                                variant="light"
                                color="indigo"
                                onClick={() => openStreamBinStatsOptionsModal(panel.id)}
                                style={{ cursor: "pointer" }}
                              >
                                k: {panel.uncertaintyScale}
                              </Badge>
                            ) : null}
                            <Badge
                              variant="light"
                              color={streamAnalysisWsConnected ? "teal" : "red"}
                            >
                              analysis link:{" "}
                              {streamAnalysisWsConnected ? "connected" : "disconnected"}
                            </Badge>
                          </Group>
                        </>
                      ) : isStreamBin2dPanel(panel) ? (
                        <>
                          <StreamBin2dPanel
                            series={bin2dSnapshot?.series ?? null}
                            reducer={panel.reducer}
                            tick={plotTick}
                            colorScheme={computedColorScheme}
                            zScaleMode={panel.yScaleMode}
                            zMin={panel.yMin}
                            zMax={panel.yMax}
                          />
                          <Group gap="sm" wrap="wrap" mt="sm">
                            {streamWorkspace ? (
                              <Badge
                                variant="light"
                                color="teal"
                                onClick={() => openStreamBin2dOptionsModal(panel.id)}
                                style={{ cursor: "pointer" }}
                              >
                                {streamWorkspace.name}
                              </Badge>
                            ) : null}
                            <Badge
                              variant="light"
                              color="indigo"
                              onClick={() => openStreamBin2dOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              output: {panel.outputId ?? "none"}
                            </Badge>
                            <Badge variant="light" color="indigo">
                              x: {bin2dXLabel}
                            </Badge>
                            <Badge variant="light" color="indigo">
                              y: {bin2dYLabel}
                            </Badge>
                            <Badge
                              variant="light"
                              color="indigo"
                              onClick={() => openStreamBin2dOptionsModal(panel.id)}
                              style={{ cursor: "pointer" }}
                            >
                              mode: {panel.reducer}
                            </Badge>
                            <Badge variant="light" color="indigo">
                              bins:{" "}
                              {(() => {
                                const xActive = bin2dSnapshot?.xActiveBinCount ?? null;
                                const yActive = bin2dSnapshot?.yActiveBinCount ?? null;
                                const xMax = bin2dSnapshot?.xMaxBinCount ?? null;
                                const yMax = bin2dSnapshot?.yMaxBinCount ?? null;
                                if (
                                  xActive === null ||
                                  yActive === null ||
                                  xMax === null ||
                                  yMax === null ||
                                  xMax <= 0 ||
                                  yMax <= 0
                                ) {
                                  return "n/a";
                                }
                                return `${xActive}x${yActive}/${xMax}x${yMax}`;
                              })()}
                            </Badge>
                            <Badge variant="light" color="indigo">
                              filled: {bin2dSnapshot?.populatedBinCount ?? "n/a"}
                            </Badge>
                            <Badge variant="light" color="indigo">
                              dropped: {bin2dSnapshot?.droppedSamples ?? "n/a"}
                            </Badge>
                            <Badge
                              variant="light"
                              color={streamAnalysisWsConnected ? "teal" : "red"}
                            >
                              analysis link:{" "}
                              {streamAnalysisWsConnected ? "connected" : "disconnected"}
                            </Badge>
                          </Group>
                        </>
                      ) : null}
                    </ReorderableCardShell>
                  );
                })}
      </div>
    </SortableContext>
  );
}
