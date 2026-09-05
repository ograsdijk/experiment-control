import {
  useEffect,
  useState,
  type ChangeEvent,
  type ReactNode,
} from "react";
import {
  ActionIcon,
  Collapse,
  Divider,
  Group,
  MultiSelect,
  NumberInput,
  Popover,
  SegmentedControl,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  UnstyledButton,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconSettings,
} from "@tabler/icons-react";

import type { Bin2dReducer } from "../../components/StreamBin2dPanel";
import type { UncertaintyMode } from "../../components/StreamBinStatsPanel";
import {
  isStreamBin2dPanel,
  isStreamBinStatsPanel,
  isStreamParamsPanel,
  isStreamRawPanel,
  isStreamScalarPanel,
  isStreamTracePanel,
  isStreamWaterfallPanel,
  isTelemetryPanel,
} from "../stream/panel_helpers";
import {
  DEFAULT_BIN2D_REDUCER,
  DEFAULT_TRACE_AVERAGE_MODE,
  DEFAULT_TRACE_DECIMATOR,
  inferChannelCountFromShape,
  parseNumberInput,
  streamTargetKey,
} from "../stream/utils";
import type {
  PlotPanelState,
  StreamTraceAverageMode,
  StreamTraceDecimator,
  TelemetrySmoothingMode,
  YDisplayMode,
  YOffsetMode,
  YScaleMode,
} from "../stream/types";
import { MAX_PANEL_HEIGHT_PX, MIN_PANEL_HEIGHT_PX } from "../profile/plot_state";
import type { PanelsGridHandlers } from "./PanelsGrid";

/**
 * The one place a panel is configured.
 *
 * Until this existed, a stream panel had two settings surfaces: this
 * popover, and a per-kind "advanced options" modal reachable only
 * through a text link at the bottom of it. The two overlapped — the
 * workspace and output selects were in both — and the split was
 * historical rather than meaningful: the y-axis range lived here while
 * the bin-stats *x*-axis transform lived in the modal, plot height here
 * while the trace decimator lived there. Nothing told you which half
 * held what.
 *
 * Now there is one surface with a fixed section order — Source, Display,
 * Card, Advanced, Status — and a panel renders only the sections that
 * apply to it. "Advanced" holds what you set once from the shape of the
 * signal and then leave alone — decimation, the point and frame-rate
 * ceilings, the bin-stats x calibration — and starts collapsed. Overlay
 * depth and averaging deliberately are *not* there: those are the knobs
 * you reach for while watching a trace, and burying them repeated the
 * same historical split this file exists to undo.
 *
 * The dropdown scrolls rather than growing without bound: a bin-stats
 * panel has enough controls to run past a short viewport.
 */

export type SelectOption = { value: string; label: string };

/**
 * Workspace-derived option lists and axis labels.
 *
 * The caller computes these only while this panel's popover is open —
 * six stream cards each recomputing four option lists on every workspace
 * update is a cost a monitoring wall notices, and a closed card needs
 * none of it. Hence the empty-list default rather than a required prop.
 */
export type PanelSettingsOptions = {
  /** Primary output for the panel's own kind. */
  outputOptions: SelectOption[];
  /** Trace outputs offered as overlays (raw trace and bin stats). */
  overlayTraceOptions: SelectOption[];
  /** `fit_1d` outputs offered as overlays (bin stats). */
  fitOverlayOptions: SelectOption[];
  /** Scalar + fit-params outputs (params panel). */
  paramsOutputOptions: SelectOption[];
  binStatsXLabel: string;
  bin2dXLabel: string;
  bin2dYLabel: string;
};

export const EMPTY_PANEL_SETTINGS_OPTIONS: PanelSettingsOptions = {
  outputOptions: [],
  overlayTraceOptions: [],
  fitOverlayOptions: [],
  paramsOutputOptions: [],
  binStatsXLabel: "",
  bin2dXLabel: "",
  bin2dYLabel: "",
};

type PanelSettingsBodyProps = {
  panel: PlotPanelState;
  /** Re-seeds the typed drafts each time the surface is opened. */
  opened: boolean;
  streamWorkspaceOptions: ReadonlyArray<SelectOption>;
  streamTargetOptions: ReadonlyArray<SelectOption>;
  options: PanelSettingsOptions;
  yAxisDraftMin: string | number;
  yAxisDraftMax: string | number;
  onYAxisDraftMinChange: (value: string | number) => void;
  onYAxisDraftMaxChange: (value: string | number) => void;
  yAxisAutoRange: { min: number; max: number } | null;
  yAxisDraftInvalid: boolean;
  /** Every live counter, unconditionally — the card shows a filtered view. */
  statusRows: ReadonlyArray<[string, string]>;
  /**
   * Raised while a combobox popup is open, so the shell can stop
   * treating a click on one of its options as a click outside itself.
   */
  onComboboxOpenChange?: (open: boolean) => void;
  telemetryNumericTraceCount: number;
  telemetryOffset: number | null;
  telemetryOffsetLabel: string;
  telemetryOffsetFullLabel: string | null;
  handlers: PanelsGridHandlers;
};

type PanelSettingsProps = PanelSettingsBodyProps & {
  onToggle: () => void;
  onClose: () => void;
};

function SectionLabel({ children }: { children: string }) {
  return (
    <Text size="xs" fw={600} c="dimmed">
      {children}
    </Text>
  );
}

export function PanelSettingsBody({
  panel,
  opened,
  streamWorkspaceOptions,
  streamTargetOptions,
  options,
  yAxisDraftMin,
  yAxisDraftMax,
  onYAxisDraftMinChange,
  onYAxisDraftMaxChange,
  yAxisAutoRange,
  yAxisDraftInvalid,
  statusRows,
  telemetryNumericTraceCount,
  telemetryOffset,
  telemetryOffsetLabel,
  telemetryOffsetFullLabel,
  onComboboxOpenChange,
  handlers,
}: PanelSettingsBodyProps) {
  const {
    setPanelLayout,
    setPanelTimeWindow,
    applyPlotOptionsAxis,
    setPlotOptionsAxisMode,
    setTelemetryYDisplayMode,
    setTelemetryYOffsetMode,
    setTelemetrySmoothingMode,
    setTelemetrySmoothingWindow,
    setStreamAnalysisPanelWorkspace,
    setStreamAnalysisPanelOutput,
    setStreamTracePanelSourceMode,
    setStreamTracePanelWorkspace,
    setStreamTracePanelOutput,
    setStreamTracePanelOverlayOutputs,
    setStreamPanelTargetFromKey,
    setStreamPanelChannelIndex,
    setStreamPanelChannels,
    setStreamPanelOverlayCount,
    setStreamPanelRollingWindow,
    setStreamPanelAverageMode,
    setStreamPanelTraceDecimator,
    setStreamPanelTraceMaxPoints,
    setStreamPanelTraceMaxFps,
    setStreamParamsPanelOutputs,
    setStreamBinStatsOverlayOutputs,
    setStreamBinStatsFitOverlayOutputs,
    setStreamBinStatsUncertainty,
    setStreamBinStatsShowBinMarkers,
    setStreamBinStatsXAxisTransform,
    setStreamBin2dReducer,
  } = handlers;

  const [advancedOpen, setAdvancedOpen] = useState(false);

  // Every combobox reports its popup state. Mantine's Popover closes on
  // any mousedown outside its own two nodes, and a combobox popup is
  // portalled to the body — so without this, picking an option shuts the
  // settings surface, and a multi-select can never take a second value.
  const dropdownEvents = {
    onDropdownOpen: () => onComboboxOpenChange?.(true),
    onDropdownClose: () => onComboboxOpenChange?.(false),
  };

  // The x transform is typed, not stepped: a free-text draft lets a
  // half-written "1e" or "-" exist without the panel re-scaling on every
  // keystroke. Only a parseable value is pushed through.
  const [xOffsetDraft, setXOffsetDraft] = useState("0");
  const [xScaleDraft, setXScaleDraft] = useState("1");
  useEffect(() => {
    if (isStreamBinStatsPanel(panel)) {
      setXOffsetDraft(String(panel.xOffset));
      setXScaleDraft(String(panel.xScale));
    }
    // Re-seeding on open is what keeps the draft honest after the value
    // was changed elsewhere (a duplicated panel, a profile import).
  }, [panel.id, opened]);

  const isTrace = isStreamTracePanel(panel);
  const isWaterfall = isStreamWaterfallPanel(panel);
  const channelCount = isTrace
    ? inferChannelCountFromShape(panel.stream?.shape)
    : 0;
  const selectedRawTargetKey =
    isTrace && panel.stream != null
      ? streamTargetKey(panel.stream.deviceId, panel.stream.stream)
      : null;

  const handleXOffsetChange = (event: ChangeEvent<HTMLInputElement>) => {
    const raw = event.currentTarget.value;
    setXOffsetDraft(raw);
    if (!isStreamBinStatsPanel(panel)) return;
    const parsed = parseNumberInput(raw);
    if (parsed === null) return;
    setStreamBinStatsXAxisTransform(panel.id, parsed, panel.xScale);
  };

  const handleXScaleChange = (event: ChangeEvent<HTMLInputElement>) => {
    const raw = event.currentTarget.value;
    setXScaleDraft(raw);
    if (!isStreamBinStatsPanel(panel)) return;
    const parsed = parseNumberInput(raw);
    // A zero scale collapses the axis; refuse it rather than blank the plot.
    if (parsed === null || parsed === 0) return;
    setStreamBinStatsXAxisTransform(panel.id, panel.xOffset, parsed);
  };

  const autoRangeText = yAxisAutoRange
    ? `auto: ${yAxisAutoRange.min.toFixed(4)} .. ${yAxisAutoRange.max.toFixed(4)}`
    : "auto range unavailable";

  // --- Source --------------------------------------------------------
  const workspaceSelect = (
    onChange: (panelId: string, workspaceId: string | null) => void
  ) => (
    <Select
      size="xs"
      searchable
      label="Workspace"
      placeholder="Select workspace"
      comboboxProps={{ zIndex: 800 }}
      {...dropdownEvents}
      data={streamWorkspaceOptions as SelectOption[]}
      value={"workspaceId" in panel ? panel.workspaceId : null}
      onChange={(value) => onChange(panel.id, value)}
    />
  );

  let sourceSection: React.ReactNode = null;
  if (isTrace) {
    sourceSection = (
      <Stack gap={6}>
        <SectionLabel>Source</SectionLabel>
        <Group justify="space-between" align="center">
          <Text size="xs" c="dimmed">
            Mode
          </Text>
          <SegmentedControl
            size="xs"
            value={panel.sourceMode}
            onChange={(value) =>
              setStreamTracePanelSourceMode(
                panel.id,
                value === "dag" ? "dag" : "raw"
              )
            }
            data={[
              { value: "raw", label: "Raw" },
              { value: "dag", label: "DAG" },
            ]}
          />
        </Group>
        {panel.sourceMode === "raw" ? (
          <>
            <Select
              size="xs"
              searchable
              clearable
              label="Stream"
              placeholder="Select stream"
              comboboxProps={{ zIndex: 800 }}
        {...dropdownEvents}
              data={streamTargetOptions as SelectOption[]}
              value={selectedRawTargetKey}
              onChange={(value) => setStreamPanelTargetFromKey(panel.id, value)}
            />
            {channelCount <= 1 ? (
              <Text size="xs" c="dimmed">
                Single-channel stream
              </Text>
            ) : isStreamRawPanel(panel) ? (
              <MultiSelect
                size="xs"
                searchable
                label="Channels"
                placeholder="Select channels"
                comboboxProps={{ zIndex: 800 }}
        {...dropdownEvents}
                data={Array.from({ length: channelCount }, (_v, i) => ({
                  value: String(i),
                  label: `ch ${i}`,
                }))}
                value={[
                  String(panel.channelIndex),
                  ...(panel.extraChannelIndices ?? []).map(String),
                ]}
                onChange={(values) =>
                  setStreamPanelChannels(panel.id, values.map(Number))
                }
              />
            ) : (
              <NumberInput
                size="xs"
                label="Channel"
                min={0}
                max={Math.max(0, channelCount - 1)}
                value={panel.channelIndex}
                onChange={(value) =>
                  setStreamPanelChannelIndex(panel.id, Number(value))
                }
              />
            )}
          </>
        ) : (
          <>
            {workspaceSelect(setStreamTracePanelWorkspace)}
            <Select
              size="xs"
              searchable
              clearable
              label="Output"
              placeholder="Select trace output"
              comboboxProps={{ zIndex: 800 }}
        {...dropdownEvents}
              data={options.outputOptions}
              value={panel.outputId}
              onChange={(value) => setStreamTracePanelOutput(panel.id, value)}
            />
            {isStreamRawPanel(panel) ? (
              <MultiSelect
                size="xs"
                searchable
                clearable
                label="Overlay outputs"
                placeholder="Optional overlay outputs"
                comboboxProps={{ zIndex: 800 }}
        {...dropdownEvents}
                data={options.overlayTraceOptions}
                value={panel.overlayOutputIds}
                onChange={(value) =>
                  setStreamTracePanelOverlayOutputs(panel.id, value)
                }
              />
            ) : null}
          </>
        )}
      </Stack>
    );
  } else if (isStreamParamsPanel(panel)) {
    sourceSection = (
      <Stack gap={6}>
        <SectionLabel>Source</SectionLabel>
        {workspaceSelect(setStreamAnalysisPanelWorkspace)}
        <MultiSelect
          size="xs"
          searchable
          clearable
          label="Outputs"
          placeholder="Select outputs"
          comboboxProps={{ zIndex: 800 }}
        {...dropdownEvents}
          data={options.paramsOutputOptions}
          value={panel.outputIds}
          onChange={(value) => setStreamParamsPanelOutputs(panel.id, value)}
        />
      </Stack>
    );
  } else if (
    isStreamScalarPanel(panel) ||
    isStreamBinStatsPanel(panel) ||
    isStreamBin2dPanel(panel)
  ) {
    sourceSection = (
      <Stack gap={6}>
        <SectionLabel>Source</SectionLabel>
        {workspaceSelect(setStreamAnalysisPanelWorkspace)}
        <Select
          size="xs"
          searchable
          clearable
          label="Output"
          placeholder="Select output"
          comboboxProps={{ zIndex: 800 }}
        {...dropdownEvents}
          data={options.outputOptions}
          value={panel.outputId}
          onChange={(value) => setStreamAnalysisPanelOutput(panel.id, value)}
        />
        {isStreamBinStatsPanel(panel) ? (
          <>
            <MultiSelect
              size="xs"
              searchable
              clearable
              label="Overlay traces"
              placeholder="Optional overlay trace outputs"
              comboboxProps={{ zIndex: 800 }}
        {...dropdownEvents}
              data={options.overlayTraceOptions}
              value={panel.overlayOutputIds}
              onChange={(value) =>
                setStreamBinStatsOverlayOutputs(panel.id, value)
              }
            />
            <MultiSelect
              size="xs"
              searchable
              clearable
              label="Overlay fits"
              placeholder="Optional overlay fit outputs"
              comboboxProps={{ zIndex: 800 }}
        {...dropdownEvents}
              data={options.fitOverlayOptions}
              value={panel.fitOverlayOutputIds}
              onChange={(value) =>
                setStreamBinStatsFitOverlayOutputs(panel.id, value)
              }
            />
          </>
        ) : null}
      </Stack>
    );
  }

  // --- Advanced ------------------------------------------------------
  //
  // The rule is "do you reach for this while watching the data, or set it
  // once and forget": decimation, the point and frame-rate ceilings and
  // the x calibration are all chosen once from the shape of the signal.
  // Overlay depth and averaging are not — those sit in Display, where
  // they are reached for constantly.
  const advancedItems: ReactNode[] = [];
  if (isTrace) {
    advancedItems.push(
      <Select
        key="decimator"
        size="xs"
        label="Decimator"
        comboboxProps={{ zIndex: 800 }}
        {...dropdownEvents}
        data={[
          { value: "stride", label: "Stride" },
          { value: "mean", label: "Mean" },
          { value: "minmax", label: "Min-Max" },
          { value: "m4", label: "M4" },
        ]}
        value={panel.traceDecimator}
        onChange={(value) =>
          setStreamPanelTraceDecimator(
            panel.id,
            (value as StreamTraceDecimator) ?? DEFAULT_TRACE_DECIMATOR
          )
        }
      />,
      <Group key="caps" grow>
        <NumberInput
          size="xs"
          label="Max points"
          min={32}
          max={20000}
          value={panel.traceMaxPoints}
          onChange={(value) =>
            setStreamPanelTraceMaxPoints(panel.id, Number(value))
          }
        />
        <NumberInput
          size="xs"
          label="Max Hz"
          min={0.5}
          max={120}
          step={0.5}
          decimalScale={1}
          value={panel.traceMaxFps}
          onChange={(value) =>
            setStreamPanelTraceMaxFps(panel.id, Number(value))
          }
        />
      </Group>
    );
  }
  if (isStreamBinStatsPanel(panel)) {
    advancedItems.push(
      <Group key="x-transform" grow>
        <TextInput
          size="xs"
          label="x offset"
          type="number"
          step="any"
          value={xOffsetDraft}
          onChange={handleXOffsetChange}
        />
        <TextInput
          size="xs"
          label="x scale"
          type="number"
          step="any"
          value={xScaleDraft}
          onChange={handleXScaleChange}
        />
      </Group>
    );
  }

  const advancedSection =
    advancedItems.length > 0 ? (
      <Stack gap={6}>
        <UnstyledButton
          onClick={() => setAdvancedOpen((value) => !value)}
          aria-expanded={advancedOpen}
        >
          <Group gap={4} align="center">
            {advancedOpen ? (
              <IconChevronDown size={13} />
            ) : (
              <IconChevronRight size={13} />
            )}
            <SectionLabel>Advanced</SectionLabel>
          </Group>
        </UnstyledButton>
        <Collapse in={advancedOpen}>
          <Stack gap={6}>{advancedItems}</Stack>
        </Collapse>
      </Stack>
    ) : null;

  return (
    <Stack gap="sm">
      {sourceSection}

      {!isStreamParamsPanel(panel) ? (
        <Stack gap={6}>
          <SectionLabel>Display</SectionLabel>
          <Group justify="space-between" align="center">
            <Text size="xs" c="dimmed">
              {(isStreamWaterfallPanel(panel) || isStreamBin2dPanel(panel)
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
                  onChange={onYAxisDraftMinChange}
                />
                <NumberInput
                  size="xs"
                  label="Max"
                  value={yAxisDraftMax}
                  onChange={onYAxisDraftMaxChange}
                />
              </Group>
              <Group justify="space-between" align="center">
                <Text size="xs" c="dimmed">
                  {autoRangeText}
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
              {autoRangeText}
            </Text>
          )}

          {isStreamBinStatsPanel(panel) ? (
            <>
              <Group justify="space-between" align="center">
                <Text size="xs" c="dimmed">
                  Uncertainty
                </Text>
                <SegmentedControl
                  size="xs"
                  value={panel.uncertaintyMode}
                  onChange={(value) =>
                    setStreamBinStatsUncertainty(
                      panel.id,
                      value as UncertaintyMode,
                      panel.uncertaintyScale
                    )
                  }
                  data={[
                    { value: "std", label: "+/-k*std" },
                    { value: "sem", label: "+/-k*sem" },
                  ]}
                />
              </Group>
              <NumberInput
                size="xs"
                label="k"
                min={0}
                step={0.1}
                value={panel.uncertaintyScale}
                onChange={(value) => {
                  const next = parseNumberInput(value);
                  if (next === null) return;
                  setStreamBinStatsUncertainty(
                    panel.id,
                    panel.uncertaintyMode,
                    next
                  );
                }}
              />
              <Switch
                size="xs"
                label="Show sampled bins"
                checked={panel.showBinMarkers}
                onChange={(event) =>
                  setStreamBinStatsShowBinMarkers(
                    panel.id,
                    event.currentTarget.checked
                  )
                }
              />
              <Text size="xs" c="dimmed">
                x axis: {options.binStatsXLabel}
              </Text>
            </>
          ) : null}

          {isStreamBin2dPanel(panel) ? (
            <>
              <Group justify="space-between" align="center">
                <Text size="xs" c="dimmed">
                  Reducer
                </Text>
                <SegmentedControl
                  size="xs"
                  value={panel.reducer}
                  onChange={(value) =>
                    setStreamBin2dReducer(
                      panel.id,
                      (value as Bin2dReducer) ?? DEFAULT_BIN2D_REDUCER
                    )
                  }
                  data={[
                    { value: "mean", label: "Mean" },
                    { value: "max", label: "Max" },
                    { value: "min", label: "Min" },
                    { value: "count", label: "Count" },
                    { value: "std", label: "Std" },
                    { value: "sem", label: "Sem" },
                    { value: "sum", label: "Sum" },
                  ]}
                />
              </Group>
              <Text size="xs" c="dimmed">
                x axis: {options.bin2dXLabel} · y axis: {options.bin2dYLabel}
              </Text>
            </>
          ) : null}

          {isTrace ? (
            <>
              <Group grow>
                <NumberInput
                  size="xs"
                  label={isWaterfall ? "Rows" : "Overlay N"}
                  min={1}
                  max={isWaterfall ? 600 : 80}
                  value={panel.overlayCount}
                  onChange={(value) =>
                    setStreamPanelOverlayCount(panel.id, Number(value))
                  }
                />
                <NumberInput
                  size="xs"
                  label="Average N"
                  min={1}
                  max={200}
                  value={panel.rollingWindow}
                  onChange={(value) =>
                    setStreamPanelRollingWindow(panel.id, Number(value))
                  }
                />
              </Group>
              <Group justify="space-between" align="center">
                <Text size="xs" c="dimmed">
                  Average
                </Text>
                <SegmentedControl
                  size="xs"
                  value={panel.averageMode}
                  onChange={(value) =>
                    setStreamPanelAverageMode(
                      panel.id,
                      (value as StreamTraceAverageMode) ??
                        DEFAULT_TRACE_AVERAGE_MODE
                    )
                  }
                  data={[
                    { value: "block", label: "Block" },
                    { value: "rolling", label: "Rolling" },
                  ]}
                />
              </Group>
            </>
          ) : null}

          {isStreamScalarPanel(panel) ? (
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
          ) : null}

          {isTelemetryPanel(panel) ? (
            <>
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
            </>
          ) : null}
        </Stack>
      ) : null}

      <Divider />
      <Stack gap={6}>
        <SectionLabel>Card</SectionLabel>
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

      {advancedSection ? (
        <>
          <Divider />
          {advancedSection}
        </>
      ) : null}

      {statusRows.length > 0 ? (
        <>
          <Divider />
          <Stack gap={2}>
            <SectionLabel>Status</SectionLabel>
            {statusRows.map(([label, value]) => (
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
    </Stack>
);
}

/**
 * The gear and the popover that holds the body above.
 *
 * Kept apart from `PanelSettingsBody` so the composition can be tested
 * without paying for floating-ui's positioning work, which is what the
 * popover contributes and Mantine already covers.
 */
export function PanelSettings({
  onToggle,
  onClose,
  ...body
}: PanelSettingsProps) {
  // Counted rather than a boolean: open/close events from two comboboxes
  // can interleave, and a stray close must not re-arm dismissal while
  // another popup is still up.
  const [openCombobox, setOpenCombobox] = useState(0);
  return (
    <Popover
      opened={body.opened}
      onChange={(next) => {
        if (!next && body.opened) {
          onClose();
        }
      }}
      position="bottom-end"
      withArrow
      shadow="md"
      withinPortal
      zIndex={700}
      width={460}
      // No fade: this is a control surface, not a reveal.
      transitionProps={{ duration: 0 }}
      closeOnClickOutside={openCombobox === 0}
    >
      <Popover.Target>
        <ActionIcon
          size="sm"
          variant="subtle"
          color="gray"
          aria-label="Panel settings"
          title="Settings"
          onClick={onToggle}
        >
          <IconSettings size={15} />
        </ActionIcon>
      </Popover.Target>
      <Popover.Dropdown className="panel-settings-dropdown">
        <PanelSettingsBody
          {...body}
          onComboboxOpenChange={(open) =>
            setOpenCombobox((count) => Math.max(0, count + (open ? 1 : -1)))
          }
        />
      </Popover.Dropdown>
    </Popover>
  );
}
