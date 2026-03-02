import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";

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

type StreamBinStatsPanelProps = {
  series: StreamBinStatsSeries | null;
  overlaySeries?: Array<{ label: string; values: number[] }>;
  fitOverlays?: StreamBinStatsFitOverlay[];
  xLabel: string;
  uncertaintyMode: UncertaintyMode;
  uncertaintyScale: number;
  showBinMarkers?: boolean;
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

function sanitizeSeries(input: StreamBinStatsSeries | null): StreamBinStatsSeries | null {
  if (!input) {
    return null;
  }
  const xBins = asFiniteList(input.xBins);
  const mean = asFiniteList(input.mean);
  const std = asFiniteList(input.std);
  const sem = asFiniteList(input.sem);
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

function buildBandData(
  input: StreamBinStatsSeries | null,
  mode: UncertaintyMode,
  scaleRaw: number
): number[][] {
  const series = sanitizeSeries(input);
  if (!series) {
    return [[], [], [], []];
  }
  const scale = clampScale(scaleRaw);
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
    x.push(xv);
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
  if (x.length <= 0 || overlays.length <= 0) {
    return [];
  }
  const n = x.length;
  const out: Array<{ label: string; values: number[] }> = [];
  for (const overlay of overlays) {
    const label = String(overlay.label ?? "").trim() || "overlay";
    const values = resampleByIndex(overlay.values, n);
    if (values.length !== n) {
      continue;
    }
    out.push({ label, values });
  }
  return out;
}

function buildFitOverlayData(
  overlays: StreamBinStatsFitOverlay[]
): StreamBinStatsFitOverlay[] {
  if (overlays.length <= 0) {
    return [];
  }
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
      x: x.slice(0, n),
      y: y.slice(0, n),
    });
  }
  return out;
}

export function computeStreamBinStatsAutoYRange(
  input: StreamBinStatsSeries | null,
  mode: UncertaintyMode,
  scale: number,
  overlays: Array<{ label: string; values: number[] }> = [],
  fitOverlays: StreamBinStatsFitOverlay[] = []
): { min: number; max: number } | null {
  const built = buildBandData(input, mode, scale);
  if (built[0].length <= 0) {
    return null;
  }
  const overlayBuilt = buildOverlayData(built[0], overlays);
  const fitOverlayBuilt = buildFitOverlayData(fitOverlays);
  let minY = Number.POSITIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (let si = 1; si < built.length; si += 1) {
    const series = built[si];
    for (const value of series) {
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
  for (const overlay of overlayBuilt) {
    for (const value of overlay.values) {
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
  series,
  overlaySeries = [],
  fitOverlays = [],
  xLabel,
  uncertaintyMode,
  uncertaintyScale,
  showBinMarkers = false,
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

  const data = useMemo(
    () => buildBandData(series, uncertaintyMode, uncertaintyScale),
    [series, uncertaintyMode, uncertaintyScale, tick]
  );
  const overlayData = useMemo(
    () => buildOverlayData(data[0] ?? [], overlaySeries),
    [data, overlaySeries, tick]
  );
  const fitOverlayData = useMemo(
    () => buildFitOverlayData(fitOverlays),
    [fitOverlays, tick]
  );
  fitOverlayDataRef.current = fitOverlayData;

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
    const meanStroke = isDark ? "#86c1ff" : "#1f5bbf";
    const meanPointFill = isDark ? "#d8ecff" : "#ffffff";

    const width = hostRef.current.clientWidth || 600;
    const overlayColors = ["#df6bff", "#4dc4ff", "#ff9f43", "#8bc34a", "#ff6b6b"];
    const fitOverlayColors = ["#ff8a5b", "#7ed957", "#d36fff", "#3fc5ff", "#f4c542"];
    const overlaySeriesDefs = overlayData.map((entry, idx) => ({
      label: entry.label,
      stroke: overlayColors[idx % overlayColors.length],
      width: 1.6,
      points: { show: false },
      value: (_u: uPlot, v: number | Date | null) => formatNumber(v),
    }));
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
    const opts: uPlot.Options = {
      width,
      height: plotHeight,
      series: [
        { label: "x" },
        {
          label: "lower",
          stroke: bandStroke,
          width: 1,
          points: { show: false },
        },
        {
          label: "upper",
          stroke: bandStroke,
          width: 1,
          points: { show: false },
        },
        {
          label: "mean",
          stroke: meanStroke,
          width: 2,
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
      ],
      bands: [{ series: [2, 1], fill: bandFill }],
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

    const fullData = [
      data[0],
      data[1],
      data[2],
      data[3],
      ...overlayData.map((entry) => entry.values),
    ];
    plotRef.current = new uPlot(opts, fullData, hostRef.current);
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
    };
  }, [
    data,
    overlayData,
    formatNumber,
    hasManualY,
    isDark,
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
    plotRef.current.setData([
      data[0],
      data[1],
      data[2],
      data[3],
      ...overlayData.map((entry) => entry.values),
    ]);
  }, [tick, data, overlayData]);

  return <div className="plot-panel" ref={hostRef} />;
}
