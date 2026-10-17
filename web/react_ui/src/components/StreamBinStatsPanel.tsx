import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";
import {
  perfCount,
  perfCountScoped,
  perfMeasure,
} from "../features/performance/perfInstrumentation";

/**
 * Series palette. Exported so the card legend can colour its entries to
 * match the plot — the legend is rendered outside the canvas, so it has
 * no other way to know which stroke belongs to which series.
 */
export const BIN_STATS_OVERLAY_COLORS = [
  "#df6bff",
  "#4dc4ff",
  "#ff9f43",
  "#8bc34a",
  "#ff6b6b",
];
export const BIN_STATS_FIT_OVERLAY_COLORS = [
  "#ff8a5b",
  "#7ed957",
  "#d36fff",
  "#3fc5ff",
  "#f4c542",
];

// Histogram overlays get their own palette so a "condition A vs B" panel
// does not reuse the trace-overlay colours. The band is the same colour at
// low alpha (8-digit hex).
export const BIN_STATS_HIST_OVERLAY_COLORS = [
  "#e8590c",
  "#2f9e44",
  "#c2255c",
  "#7048e8",
  "#0c8599",
];

export function binStatsHistOverlayColor(idx: number): string {
  return BIN_STATS_HIST_OVERLAY_COLORS[idx % BIN_STATS_HIST_OVERLAY_COLORS.length];
}

export function binStatsMeanStroke(isDark: boolean): string {
  return isDark ? "#86c1ff" : "#1f5bbf";
}

export type UncertaintyMode = "std" | "sem";

export type StreamBinStatsSeries = {
  xBins: number[];
  mean: number[];
  std: number[];
  sem: number[];
  count: number[];
};

export type StreamBinStatsFitOverlay = {
  label: string;
  x: number[];
  y: number[];
};

/**
 * Another histogram drawn on the primary's axes with its own bins and
 * uncertainty band. `series` is null until the first update arrives; the
 * slot is kept so the plot topology and colours do not shift.
 */
export type StreamBinStatsHistOverlay = {
  label: string;
  series: StreamBinStatsSeries | null;
};

type StreamBinStatsPanelProps = {
  panelId?: string;
  series: StreamBinStatsSeries | null;
  overlaySeries?: Array<{ label: string; values: number[] }>;
  histOverlays?: StreamBinStatsHistOverlay[];
  fitOverlays?: StreamBinStatsFitOverlay[];
  xLabel: string;
  uncertaintyMode: UncertaintyMode;
  uncertaintyScale: number;
  showBinMarkers?: boolean;
  xOffset?: number;
  xScale?: number;
  tick: number;
  colorScheme: "light" | "dark";
  plotHeight?: number;
  yScaleMode?: "auto" | "manual";
  yMin?: number | null;
  yMax?: number | null;
};

function asFiniteList(raw: unknown): number[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const out: number[] = [];
  for (const item of raw) {
    const value = Number(item);
    if (!Number.isFinite(value)) {
      return [];
    }
    out.push(value);
  }
  return out;
}

function asNumberList(raw: unknown): number[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.map((item) => Number(item));
}

function sanitizeSeries(input: StreamBinStatsSeries | null): StreamBinStatsSeries | null {
  if (!input) {
    return null;
  }
  const xBins = asFiniteList(input.xBins);
  // Missing values are represented as NaN internally. Preserve their bin
  // positions so one unavailable uncertainty does not discard the full series.
  const mean = asNumberList(input.mean);
  const std = asNumberList(input.std);
  const sem = asNumberList(input.sem);
  const count = asFiniteList(input.count);
  const n = Math.min(xBins.length, mean.length, std.length, sem.length, count.length);
  if (!Number.isFinite(n) || n <= 0) {
    return null;
  }
  return {
    xBins: xBins.slice(0, n),
    mean: mean.slice(0, n),
    std: std.slice(0, n),
    sem: sem.slice(0, n),
    count: count.slice(0, n),
  };
}

function clampScale(value: number): number {
  if (!Number.isFinite(value)) {
    return 1;
  }
  return Math.max(0, value);
}

function normalizeXScale(value: number): number {
  if (!Number.isFinite(value) || value === 0) {
    return 1;
  }
  return value;
}

function normalizeXOffset(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return value;
}

export function buildBandData(
  input: StreamBinStatsSeries | null,
  mode: UncertaintyMode,
  scaleRaw: number,
  xOffsetRaw = 0,
  xScaleRaw = 1
): number[][] {
  const series = sanitizeSeries(input);
  if (!series) {
    return [[], [], [], []];
  }
  const scale = clampScale(scaleRaw);
  const xOffset = normalizeXOffset(xOffsetRaw);
  const xScale = normalizeXScale(xScaleRaw);
  const spreadSource = mode === "std" ? series.std : series.sem;
  const lower: number[] = [];
  const upper: number[] = [];
  const mean: number[] = [];
  const x: number[] = [];

  for (let i = 0; i < series.xBins.length; i += 1) {
    const xv = series.xBins[i];
    const mv = series.mean[i];
    const spread = spreadSource[i];
    const c = series.count[i];
    if (!Number.isFinite(xv) || !Number.isFinite(c) || c <= 0) {
      continue;
    }
    if (!Number.isFinite(mv) || !Number.isFinite(spread)) {
      continue;
    }
    const band = spread * scale;
    x.push(xv * xScale + xOffset);
    mean.push(mv);
    lower.push(mv - band);
    upper.push(mv + band);
  }

  return [x, lower, upper, mean];
}

function resampleByIndex(valuesRaw: number[], targetLength: number): number[] {
  const values = valuesRaw.filter((v) => Number.isFinite(v));
  const n = Math.max(0, Math.trunc(targetLength));
  if (n <= 0 || values.length <= 0) {
    return [];
  }
  if (values.length === n) {
    return values.slice();
  }
  if (values.length === 1) {
    return Array.from({ length: n }, () => values[0]);
  }
  const m = values.length;
  const out: number[] = new Array(n);
  for (let i = 0; i < n; i += 1) {
    const pos = (i * (m - 1)) / Math.max(1, n - 1);
    const lo = Math.floor(pos);
    const hi = Math.min(m - 1, lo + 1);
    const frac = pos - lo;
    out[i] = values[lo] * (1 - frac) + values[hi] * frac;
  }
  return out;
}

function buildOverlayData(
  x: number[],
  overlays: Array<{ label: string; values: number[] }>
): Array<{ label: string; values: number[] }> {
  if (overlays.length <= 0) {
    return [];
  }
  const n = x.length;
  return overlays.map((overlay) => {
    const label = String(overlay.label ?? "").trim() || "overlay";
    const values = resampleByIndex(overlay.values, n);
    return {
      label,
      values: values.length === n ? values : new Array(n).fill(Number.NaN),
    };
  });
}

export function buildFitOverlayData(
  overlays: StreamBinStatsFitOverlay[],
  xOffsetRaw = 0,
  xScaleRaw = 1
): StreamBinStatsFitOverlay[] {
  if (overlays.length <= 0) {
    return [];
  }
  const xOffset = normalizeXOffset(xOffsetRaw);
  const xScale = normalizeXScale(xScaleRaw);
  const out: StreamBinStatsFitOverlay[] = [];
  for (const overlay of overlays) {
    const label = String(overlay.label ?? "").trim() || "fit";
    const x = asFiniteList(overlay.x);
    const y = asFiniteList(overlay.y);
    const n = Math.min(x.length, y.length);
    if (!Number.isFinite(n) || n <= 1) {
      continue;
    }
    out.push({
      label,
      x: x.slice(0, n).map((xv) => xv * xScale + xOffset),
      y: y.slice(0, n),
    });
  }
  return out;
}

type PlotColumn = Array<number | null>;

export type BinStatsPlotData = {
  /** uPlot aligned data: x, lower, upper, mean, trace overlays, then
   * lower/upper/mean per histogram overlay. */
  columns: PlotColumn[];
  traceOverlayLabels: string[];
  histOverlayLabels: string[];
};

/**
 * Put the primary histogram, its trace overlays and any histogram
 * overlays on one x axis. Histograms with different bins (e.g. the same
 * scan split by a shot condition, where one half has not reached a scan
 * point yet) are merged onto the sorted union of their x values; a series
 * is null where it has no bin. Trace overlays stay index-aligned to the
 * primary's bins, as before.
 */
export function buildBinStatsPlotData(
  primary: StreamBinStatsSeries | null,
  traceOverlays: Array<{ label: string; values: number[] }>,
  histOverlays: StreamBinStatsHistOverlay[],
  mode: UncertaintyMode,
  scale: number,
  xOffset = 0,
  xScale = 1
): BinStatsPlotData {
  const primaryBand = buildBandData(primary, mode, scale, xOffset, xScale);
  const traceBuilt = buildOverlayData(primaryBand[0], traceOverlays);
  const histBands = histOverlays.map((overlay) =>
    buildBandData(overlay.series, mode, scale, xOffset, xScale)
  );
  const xs = new Set<number>(primaryBand[0]);
  for (const band of histBands) {
    for (const xv of band[0]) {
      xs.add(xv);
    }
  }
  const unionX = Array.from(xs).sort((a, b) => a - b);
  const position = new Map<number, number>();
  unionX.forEach((xv, idx) => position.set(xv, idx));
  const place = (sourceX: number[], values: number[]): PlotColumn => {
    const out: PlotColumn = new Array(unionX.length).fill(null);
    for (let i = 0; i < sourceX.length; i += 1) {
      const idx = position.get(sourceX[i]);
      const v = values[i];
      if (idx !== undefined && Number.isFinite(v)) {
        out[idx] = v;
      }
    }
    return out;
  };
  const columns: PlotColumn[] = [
    unionX,
    place(primaryBand[0], primaryBand[1]),
    place(primaryBand[0], primaryBand[2]),
    place(primaryBand[0], primaryBand[3]),
    ...traceBuilt.map((entry) => place(primaryBand[0], entry.values)),
  ];
  for (const band of histBands) {
    columns.push(
      place(band[0], band[1]),
      place(band[0], band[2]),
      place(band[0], band[3])
    );
  }
  return {
    columns,
    traceOverlayLabels: traceBuilt.map((entry) => entry.label),
    histOverlayLabels: histOverlays.map(
      (overlay) => String(overlay.label ?? "").trim() || "histogram"
    ),
  };
}

export function computeStreamBinStatsAutoYRange(
  input: StreamBinStatsSeries | null,
  mode: UncertaintyMode,
  scale: number,
  overlays: Array<{ label: string; values: number[] }> = [],
  fitOverlays: StreamBinStatsFitOverlay[] = [],
  histOverlays: StreamBinStatsHistOverlay[] = []
): { min: number; max: number } | null {
  const built = buildBinStatsPlotData(input, overlays, histOverlays, mode, scale);
  if (built.columns[0].length <= 0) {
    return null;
  }
  const fitOverlayBuilt = buildFitOverlayData(fitOverlays);
  let minY = Number.POSITIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (let si = 1; si < built.columns.length; si += 1) {
    for (const value of built.columns[si]) {
      if (value === null || !Number.isFinite(value)) {
        continue;
      }
      if (value < minY) {
        minY = value;
      }
      if (value > maxY) {
        maxY = value;
      }
    }
  }
  for (const overlay of fitOverlayBuilt) {
    for (const value of overlay.y) {
      if (!Number.isFinite(value)) {
        continue;
      }
      if (value < minY) {
        minY = value;
      }
      if (value > maxY) {
        maxY = value;
      }
    }
  }
  if (!Number.isFinite(minY) || !Number.isFinite(maxY)) {
    return null;
  }
  if (minY === maxY) {
    const pad = Math.abs(minY) > 0 ? Math.abs(minY) * 0.05 : 1;
    return { min: minY - pad, max: maxY + pad };
  }
  return { min: minY, max: maxY };
}

export function StreamBinStatsPanel({
  panelId,
  series,
  overlaySeries = [],
  histOverlays = [],
  fitOverlays = [],
  xLabel,
  uncertaintyMode,
  uncertaintyScale,
  showBinMarkers = false,
  xOffset = 0,
  xScale = 1,
  tick,
  colorScheme,
  plotHeight = 320,
  yScaleMode = "auto",
  yMin = null,
  yMax = null,
}: StreamBinStatsPanelProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);
  const fitOverlayDataRef = useRef<StreamBinStatsFitOverlay[]>([]);
  const overlayLabelsRef = useRef<string[]>([]);
  const histOverlayLabelsRef = useRef<string[]>([]);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const isDark = colorScheme === "dark";

  const formatNumber = useMemo(() => {
    return (value: number | Date | null | undefined) => {
      if (typeof value !== "number" || !Number.isFinite(value)) {
        return "";
      }
      const abs = Math.abs(value);
      if (abs > 0 && (abs >= 1e4 || abs < 1e-3)) {
        return value.toExponential(3);
      }
      const fixed = value.toFixed(3);
      return fixed.replace(/\.?0+$/, "");
    };
  }, []);

  const plotData = useMemo(
    () =>
      perfMeasure("bin_stats.conversion_ms", () =>
        buildBinStatsPlotData(
          series,
          overlaySeries,
          histOverlays,
          uncertaintyMode,
          uncertaintyScale,
          xOffset,
          xScale
        )
      ),
    [
      series,
      overlaySeries,
      histOverlays,
      uncertaintyMode,
      uncertaintyScale,
      xOffset,
      xScale,
      tick,
    ]
  );
  const fitOverlayData = useMemo(
    () => buildFitOverlayData(fitOverlays, xOffset, xScale),
    [fitOverlays, xOffset, xScale, tick]
  );
  const fullData = plotData.columns;
  const plotDataRef = useRef<uPlot.AlignedData>(fullData as uPlot.AlignedData);
  plotDataRef.current = fullData as uPlot.AlignedData;
  const overlaySeriesCount = overlaySeries.length;
  const histOverlayCount = histOverlays.length;
  fitOverlayDataRef.current = fitOverlayData;
  overlayLabelsRef.current = plotData.traceOverlayLabels;
  histOverlayLabelsRef.current = plotData.histOverlayLabels;

  const hasManualY = useMemo(() => {
    if (yScaleMode !== "manual") {
      return false;
    }
    if (
      typeof yMin !== "number" ||
      typeof yMax !== "number" ||
      !Number.isFinite(yMin) ||
      !Number.isFinite(yMax)
    ) {
      return false;
    }
    return yMin < yMax;
  }, [yScaleMode, yMin, yMax]);

  useEffect(() => {
    if (!hostRef.current) {
      return;
    }
    plotRef.current?.destroy();
    plotRef.current = null;

    const axisStroke = isDark ? "#e8e2d7" : "#3c372f";
    const gridStroke = isDark ? "rgba(255, 255, 255, 0.12)" : "rgba(0, 0, 0, 0.08)";
    const tickStroke = isDark ? "rgba(255, 255, 255, 0.35)" : "rgba(0, 0, 0, 0.25)";
    const bandFill = isDark ? "rgba(110, 183, 255, 0.22)" : "rgba(34, 108, 216, 0.16)";
    const bandStroke = isDark ? "rgba(110, 183, 255, 0.32)" : "rgba(34, 108, 216, 0.24)";
    const meanStroke = binStatsMeanStroke(isDark);
    const meanPointFill = isDark ? "#d8ecff" : "#ffffff";

    const width = hostRef.current.clientWidth || 600;
    const overlayColors = BIN_STATS_OVERLAY_COLORS;
    const fitOverlayColors = BIN_STATS_FIT_OVERLAY_COLORS;
    const overlaySeriesDefs = Array.from({ length: overlaySeriesCount }, (_value, idx) => ({
      // Tooltip labels are read from overlayLabelsRef; the hidden uPlot legend
      // only needs a stable structural label.
      label: `overlay ${idx + 1}`,
      stroke: overlayColors[idx % overlayColors.length],
      width: 1.6,
      points: { show: false },
      value: (_u: uPlot, v: number | Date | null) => formatNumber(v),
    }));
    // Each histogram overlay is lower, upper, mean: the same shape as the
    // primary. spanGaps joins across x values only the other histograms
    // have.
    const histOverlayFirstIdx = 4 + overlaySeriesCount;
    const histOverlaySeriesDefs: uPlot.Series[] = [];
    const histOverlayBands: uPlot.Band[] = [];
    for (let idx = 0; idx < histOverlayCount; idx += 1) {
      const color = binStatsHistOverlayColor(idx);
      const bandEdge = (): uPlot.Series => ({
        label: `histogram ${idx + 1} band`,
        stroke: `${color}55`,
        width: 1,
        spanGaps: true,
        points: { show: false },
      });
      histOverlaySeriesDefs.push(bandEdge(), bandEdge(), {
        label: `histogram ${idx + 1}`,
        stroke: color,
        width: 2,
        spanGaps: true,
        points: {
          show: showBinMarkers,
          size: 6,
          width: 2,
          stroke: color,
          fill: meanPointFill,
        },
        value: (_u: uPlot, v: number | Date | null) => formatNumber(v),
      });
      const lowerIdx = histOverlayFirstIdx + idx * 3;
      histOverlayBands.push({
        series: [lowerIdx + 1, lowerIdx],
        fill: `${color}29`,
      });
    }
    const fitOverlayPlugin: uPlot.Plugin = {
      hooks: {
        draw: [
          (u) => {
            const overlays = fitOverlayDataRef.current;
            if (overlays.length <= 0) {
              return;
            }
            const ctx = u.ctx;
            const { left, top, width: boxWidth, height: boxHeight } = u.bbox;
            ctx.save();
            ctx.beginPath();
            ctx.rect(left, top, boxWidth, boxHeight);
            ctx.clip();
            overlays.forEach((overlay, idx) => {
              const stroke = fitOverlayColors[idx % fitOverlayColors.length];
              ctx.beginPath();
              ctx.lineWidth = 1.8;
              ctx.strokeStyle = stroke;
              let started = false;
              for (let i = 0; i < overlay.x.length; i += 1) {
                const xv = overlay.x[i];
                const yv = overlay.y[i];
                if (!Number.isFinite(xv) || !Number.isFinite(yv)) {
                  continue;
                }
                const px = u.valToPos(xv, "x", true);
                const py = u.valToPos(yv, "y", true);
                if (!Number.isFinite(px) || !Number.isFinite(py)) {
                  continue;
                }
                if (!started) {
                  ctx.moveTo(px, py);
                  started = true;
                } else {
                  ctx.lineTo(px, py);
                }
              }
              if (started) {
                ctx.stroke();
              }
            });
            ctx.restore();
          },
        ],
      },
    };
    const hideTooltip = () => {
      const el = tooltipRef.current;
      if (el) {
        el.style.display = "none";
      }
    };

    const updateTooltip = (u: uPlot) => {
      const el = tooltipRef.current;
      if (!el) {
        return;
      }
      const idx = u.cursor.idx;
      if (idx == null || u.cursor.left == null || u.cursor.top == null) {
        hideTooltip();
        return;
      }
      const xv = u.data[0]?.[idx];
      const lowerV = u.data[1]?.[idx];
      const upperV = u.data[2]?.[idx];
      const meanV = u.data[3]?.[idx];
      if (!Number.isFinite(xv as number)) {
        hideTooltip();
        return;
      }
      const lines = [`x: ${formatNumber(xv as number)}`];
      // The merged x axis can hold points only a histogram overlay has.
      if (Number.isFinite(meanV as number)) {
        lines.push(`mean: ${formatNumber(meanV as number)}`);
        if (Number.isFinite(lowerV as number) && Number.isFinite(upperV as number)) {
          lines.push(
            `range: ${formatNumber(lowerV as number)} to ${formatNumber(upperV as number)}`
          );
        }
      }
      overlayLabelsRef.current.forEach((label, i) => {
        const v = u.data[4 + i]?.[idx];
        if (Number.isFinite(v as number)) {
          lines.push(`${label}: ${formatNumber(v as number)}`);
        }
      });
      const histFirst = 4 + overlayLabelsRef.current.length;
      histOverlayLabelsRef.current.forEach((label, i) => {
        const lo = u.data[histFirst + i * 3]?.[idx];
        const hi = u.data[histFirst + i * 3 + 1]?.[idx];
        const v = u.data[histFirst + i * 3 + 2]?.[idx];
        if (!Number.isFinite(v as number)) {
          return;
        }
        const spread =
          Number.isFinite(lo as number) && Number.isFinite(hi as number)
            ? ` ± ${formatNumber(((hi as number) - (lo as number)) / 2)}`
            : "";
        lines.push(`${label}: ${formatNumber(v as number)}${spread}`);
      });
      if (lines.length <= 1) {
        hideTooltip();
        return;
      }
      el.textContent = "";
      lines.forEach((line) => {
        const row = document.createElement("div");
        row.textContent = line;
        el.appendChild(row);
      });

      const dpr = window.devicePixelRatio || 1;
      const plotOffsetLeft = u.bbox.left / dpr;
      const plotOffsetTop = u.bbox.top / dpr;
      const margin = 12;
      const cursorLeft = plotOffsetLeft + u.cursor.left;
      const cursorTop = plotOffsetTop + u.cursor.top;
      const elWidth = el.offsetWidth || 120;
      const elHeight = el.offsetHeight || 60;
      let left = cursorLeft + margin;
      let top = cursorTop + margin;
      if (left + elWidth > u.width) {
        left = cursorLeft - elWidth - margin;
      }
      if (top + elHeight > u.height) {
        top = cursorTop - elHeight - margin;
      }
      el.style.left = `${Math.max(0, left)}px`;
      el.style.top = `${Math.max(0, top)}px`;
      el.style.display = "block";
    };

    const opts: uPlot.Options = {
      width,
      height: plotHeight,
      cursor: {
        points: { show: true },
      },
      hooks: {
        setCursor: [updateTooltip],
      },
      series: [
        { label: "x" },
        {
          label: "lower",
          stroke: bandStroke,
          width: 1,
          spanGaps: true,
          points: { show: false },
        },
        {
          label: "upper",
          stroke: bandStroke,
          width: 1,
          spanGaps: true,
          points: { show: false },
        },
        {
          label: "mean",
          stroke: meanStroke,
          width: 2,
          spanGaps: true,
          points: {
            show: showBinMarkers,
            size: 6,
            width: 2,
            stroke: meanStroke,
            fill: meanPointFill,
          },
          value: (_u: uPlot, v: number | Date | null) => formatNumber(v),
        },
        ...overlaySeriesDefs,
        ...histOverlaySeriesDefs,
      ],
      bands: [{ series: [2, 1], fill: bandFill }, ...histOverlayBands],
      legend: { show: false, live: false },
      scales: {
        x: { time: false },
        y: hasManualY
          ? {
              auto: false,
              min: Number(yMin),
              max: Number(yMax),
            }
          : { auto: true },
      },
      axes: [
        {
          label: xLabel.trim() || "context x",
          stroke: axisStroke,
          grid: { stroke: gridStroke },
          ticks: { stroke: tickStroke },
          values: (_u, vals) => vals.map((v) => formatNumber(Number(v))),
        },
        {
          label: "integral",
          stroke: axisStroke,
          grid: { stroke: gridStroke },
          ticks: { stroke: tickStroke },
          values: (_u, vals) => vals.map((v) => formatNumber(Number(v))),
        },
      ],
      plugins: [fitOverlayPlugin],
    };

    plotRef.current = new uPlot(opts, plotDataRef.current, hostRef.current);
    const resize = new ResizeObserver(() => {
      if (!hostRef.current || !plotRef.current) {
        return;
      }
      plotRef.current.setSize({
        width: hostRef.current.clientWidth,
        height: plotHeight,
      });
    });
    resize.observe(hostRef.current);
    return () => {
      resize.disconnect();
      plotRef.current?.destroy();
      plotRef.current = null;
      hideTooltip();
    };
  }, [
    formatNumber,
    hasManualY,
    histOverlayCount,
    isDark,
    overlaySeriesCount,
    plotHeight,
    showBinMarkers,
    xLabel,
    yMax,
    yMin,
  ]);

  useEffect(() => {
    if (!plotRef.current) {
      return;
    }
    // setData redraws the plot, including the fit-overlay plugin after its ref
    // has been refreshed above. No uPlot reconstruction is needed for live data.
    perfCount("plot.uplot_setData");
    perfCount("plot.bin_stats.uplot_setData");
    perfCountScoped("plot.uplot_setData", panelId);
    plotRef.current.setData(fullData as uPlot.AlignedData);
  }, [tick, fullData, fitOverlayData, panelId]);

  return (
    <div style={{ position: "relative" }}>
      <div className="plot-panel" ref={hostRef} />
      <div
        ref={tooltipRef}
        style={{
          position: "absolute",
          display: "none",
          pointerEvents: "none",
          zIndex: 10,
          padding: "6px 8px",
          borderRadius: 6,
          fontSize: 12,
          lineHeight: 1.4,
          whiteSpace: "nowrap",
          background: isDark ? "rgba(30, 28, 24, 0.92)" : "rgba(255, 255, 255, 0.95)",
          border: `1px solid ${isDark ? "rgba(255, 255, 255, 0.18)" : "rgba(0, 0, 0, 0.12)"}`,
          color: isDark ? "#e8e2d7" : "#3c372f",
        }}
      />
    </div>
  );
}


