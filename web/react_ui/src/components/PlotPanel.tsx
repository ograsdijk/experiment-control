import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";
import type { TelemetrySmoothingMode } from "../features/stream/types";
import { buildTelemetrySmoothingOverlays } from "../features/stream/telemetry_smoothing";
import { RingBuffer } from "../utils/ringBuffer";
import { colorWithAlpha, traceColorAt } from "../utils/traceColors";
import { TraceKey } from "../types";

type PlotPanelProps = {
  traces: TraceKey[];
  buffers: Map<string, RingBuffer>;
  tick: number;
  timeWindowS: number;
  colorScheme: "light" | "dark";
  plotHeight?: number;
  yScaleMode?: "auto" | "manual";
  yMin?: number | null;
  yMax?: number | null;
  yDisplayMode?: "absolute" | "delta";
  yOffset?: number | null;
  smoothingMode?: TelemetrySmoothingMode;
  smoothingWindowS?: number;
};

type PanelSeriesEntry = {
  trace: TraceKey;
  traceIndex: number;
  isOverlay: boolean;
  label: string;
};

function traceKeyToId(trace: TraceKey) {
  return `${trace.deviceId}:${trace.signal}`;
}

export function buildTelemetryData(
  traces: TraceKey[],
  buffers: Map<string, RingBuffer>
) {
  if (traces.length === 0) {
    return [[]];
  }
  const seriesData = traces.map((trace) => {
    const key = traceKeyToId(trace);
    const buffer = buffers.get(key);
    return buffer ? buffer.toArrays() : [[], []];
  });
  const lengths = seriesData.map((pair) => pair[0].length).filter((len) => len > 0);
  const minLen = lengths.length > 0 ? Math.min(...lengths) : 0;
  if (!Number.isFinite(minLen) || minLen <= 0) {
    return [[], ...traces.map(() => [])];
  }
  const [time] = seriesData[0];
  const t = time.slice(-minLen);
  const values = seriesData.map(([, v]) => v.slice(-minLen));
  return [t, ...values];
}

export function computeTelemetryAutoYRange(
  traces: TraceKey[],
  buffers: Map<string, RingBuffer>,
  timeWindowS: number
): { min: number; max: number } | null {
  const data = buildTelemetryData(traces, buffers);
  if (data.length <= 1) {
    return null;
  }
  const x = data[0];
  if (!x || x.length === 0) {
    return null;
  }
  let startIdx = 0;
  if (Number.isFinite(timeWindowS) && timeWindowS > 0) {
    const latest = x[x.length - 1];
    if (Number.isFinite(latest)) {
      const minX = latest - timeWindowS;
      for (let idx = x.length - 1; idx >= 0; idx -= 1) {
        if (x[idx] < minX) {
          startIdx = Math.min(x.length - 1, idx + 1);
          break;
        }
      }
    }
  }
  let minY = Number.POSITIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (let si = 1; si < data.length; si += 1) {
    const series = data[si];
    for (let i = startIdx; i < series.length; i += 1) {
      const value = series[i];
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

function applyTelemetryDisplayTransform(
  data: number[][],
  seriesEntries: readonly PanelSeriesEntry[],
  yDisplayMode: "absolute" | "delta",
  yOffset: number | null
): number[][] {
  if (
    yDisplayMode !== "delta" ||
    typeof yOffset !== "number" ||
    !Number.isFinite(yOffset) ||
    data.length <= 1
  ) {
    return data;
  }
  const out: number[][] = [data[0]];
  for (let si = 1; si < data.length; si += 1) {
    const entry = seriesEntries[si - 1];
    const series = data[si] ?? [];
    if (entry?.trace.valueKind === "boolean") {
      out.push(series);
      continue;
    }
    out.push(
      series.map((value) =>
        Number.isFinite(value) ? value - yOffset : value
      )
    );
  }
  return out;
}

function latestTimestamp(data: number[][]): number | null {
  if (data.length === 0) {
    return null;
  }
  const t = data[0];
  if (!t || t.length === 0) {
    return null;
  }
  const last = t[t.length - 1];
  return Number.isFinite(last) ? last : null;
}

function applyTimeWindow(
  plot: uPlot,
  data: number[][],
  timeWindowS: number
) {
  const tmax = latestTimestamp(data);
  if (!tmax || !Number.isFinite(timeWindowS) || timeWindowS <= 0) {
    return;
  }
  plot.setScale("x", { min: tmax - timeWindowS, max: tmax });
}

function latestSeriesSample(u: uPlot, seriesIndex: number): number | null {
  const values = u.data[seriesIndex] as ArrayLike<number> | undefined;
  if (!values || values.length === 0) {
    return null;
  }
  const raw = Number(values[values.length - 1]);
  return Number.isFinite(raw) ? raw : null;
}

function legendNumericValue(
  u: uPlot,
  rawValue: number | Date | null | undefined,
  seriesIndex: number,
  dataIndex: number | null | undefined
): number | null {
  if (typeof rawValue === "number" && Number.isFinite(rawValue)) {
    return rawValue;
  }
  if (rawValue instanceof Date) {
    const asSeconds = rawValue.getTime() / 1000;
    return Number.isFinite(asSeconds) ? asSeconds : null;
  }
  if (typeof dataIndex === "number" && dataIndex >= 0) {
    const values = u.data[seriesIndex] as ArrayLike<number> | undefined;
    if (values && dataIndex < values.length) {
      const atIndex = Number(values[dataIndex]);
      if (Number.isFinite(atIndex)) {
        return atIndex;
      }
    }
  }
  return latestSeriesSample(u, seriesIndex);
}

export function PlotPanel({
  traces,
  buffers,
  tick,
  timeWindowS,
  colorScheme,
  plotHeight = 320,
  yScaleMode = "auto",
  yMin = null,
  yMax = null,
  yDisplayMode = "absolute",
  yOffset = null,
  smoothingMode = "none",
  smoothingWindowS = 5,
}: PlotPanelProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);
  const isDark = colorScheme === "dark";

  const formatNumber = useMemo(() => {
    return (value: number) => {
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

  const timeFormatter = useMemo(() => {
    return (v: number | Date) => {
      let date: Date | null = null;
      if (v instanceof Date) {
        date = v;
      } else if (typeof v === "number" && Number.isFinite(v)) {
        date = new Date(v * 1000);
      }
      if (!date || Number.isNaN(date.getTime())) {
        return "";
      }
      return date.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      });
    };
  }, []);

  const smoothingModeNormalized: TelemetrySmoothingMode =
    smoothingMode === "sma" || smoothingMode === "ema" ? smoothingMode : "none";
  const smoothingWindowNormalized = Number.isFinite(smoothingWindowS)
    ? Math.max(1, Math.min(300, smoothingWindowS))
    : 5;
  const smoothingLabel = useMemo(() => {
    if (smoothingModeNormalized === "none") {
      return null;
    }
    const prettyWindow =
      Math.abs(smoothingWindowNormalized - Math.round(smoothingWindowNormalized)) < 1e-6
        ? String(Math.round(smoothingWindowNormalized))
        : smoothingWindowNormalized.toFixed(1).replace(/\.?0+$/, "");
    return `${smoothingModeNormalized.toUpperCase()} ${prettyWindow}s`;
  }, [smoothingModeNormalized, smoothingWindowNormalized]);

  const seriesEntries = useMemo<PanelSeriesEntry[]>(() => {
    const out: PanelSeriesEntry[] = traces.map((trace, idx) => ({
      trace,
      traceIndex: idx,
      isOverlay: false,
      label: `${trace.deviceId}.${trace.signal}`,
    }));
    if (smoothingModeNormalized !== "none" && smoothingLabel) {
      for (let idx = 0; idx < traces.length; idx += 1) {
        const trace = traces[idx];
        if (trace.valueKind === "boolean") {
          continue;
        }
        out.push({
          trace,
          traceIndex: idx,
          isOverlay: true,
          label: `${trace.deviceId}.${trace.signal} (${smoothingLabel})`,
        });
      }
    }
    return out;
  }, [traces, smoothingModeNormalized, smoothingLabel]);

  const buildPanelData = useMemo(
    () => (): number[][] => {
      const rawData = buildTelemetryData(traces, buffers);
      const time = rawData[0] ?? [];
      const rawSeries = rawData.slice(1);
      const combined: number[][] = [time, ...rawSeries];
      if (smoothingModeNormalized !== "none" && time.length > 0 && traces.length > 0) {
        const overlays = buildTelemetrySmoothingOverlays(
          time,
          traces,
          rawSeries,
          smoothingModeNormalized,
          smoothingWindowNormalized
        );
        const overlaysByTrace = new Map<number, number[]>(
          overlays.map((entry) => [entry.traceIndex, entry.values])
        );
        for (const entry of seriesEntries) {
          if (!entry.isOverlay) {
            continue;
          }
          const overlay = overlaysByTrace.get(entry.traceIndex);
          if (overlay && overlay.length === time.length) {
            combined.push(overlay);
          } else {
            combined.push(new Array(time.length).fill(Number.NaN));
          }
        }
      }
      return applyTelemetryDisplayTransform(
        combined,
        seriesEntries,
        yDisplayMode,
        yOffset
      );
    },
    [
      traces,
      buffers,
      seriesEntries,
      smoothingModeNormalized,
      smoothingWindowNormalized,
      yDisplayMode,
      yOffset,
    ]
  );

  const series = useMemo(() => {
    const smoothingActive = smoothingModeNormalized !== "none";
    return [
      {
        label: "time",
        value: (
          u: uPlot,
          v: number | Date | null,
          si: number,
          idx: number | null
        ) => {
          const numeric = legendNumericValue(u, v, si, idx);
          if (numeric === null) {
            return "";
          }
          return timeFormatter(numeric);
        },
      },
      ...seriesEntries.map((entry) => ({
        label: entry.label,
        stroke: entry.isOverlay
          ? traceColorAt(entry.traceIndex)
          : smoothingActive && entry.trace.valueKind !== "boolean"
          ? colorWithAlpha(traceColorAt(entry.traceIndex), isDark ? 0.52 : 0.6)
          : traceColorAt(entry.traceIndex),
        width: entry.isOverlay
          ? 2.4
          : smoothingActive && entry.trace.valueKind !== "boolean"
          ? 1.2
          : 2,
        dash:
          !entry.isOverlay && smoothingActive && entry.trace.valueKind !== "boolean"
            ? [6, 4]
            : undefined,
        value: (
          u: uPlot,
          v: number | Date | null,
          si: number,
          idx: number | null
        ) => {
          const numeric = legendNumericValue(u, v, si, idx);
          if (numeric === null) {
            return "";
          }
          if (entry.trace.valueKind === "boolean") {
            return numeric >= 0.5 ? "true" : "false";
          }
          return formatNumber(numeric);
        },
      })),
    ];
  }, [seriesEntries, timeFormatter, formatNumber, isDark, smoothingModeNormalized]);

  const booleanOnly = useMemo(() => {
    if (traces.length === 0) {
      return false;
    }
    return traces.every((trace) => trace.valueKind === "boolean");
  }, [traces]);

  const yAxisLabel = useMemo(() => {
    if (traces.length === 0) {
      return "";
    }
    if (booleanOnly) {
      return "state";
    }
    const deltaPrefix = yDisplayMode === "delta" ? "Δ " : "";
    const units = traces
      .map((trace) => trace.units)
      .filter((unit): unit is string => Boolean(unit));
    if (units.length === 0) {
      return yDisplayMode === "delta" ? "Δ" : "";
    }
    const unique = new Set(units);
    if (unique.size === 1) {
      return `${deltaPrefix}${[...unique][0]}`;
    }
    return yDisplayMode === "delta" ? "Δ" : "";
  }, [traces, booleanOnly, yDisplayMode]);

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
    if (plotRef.current) {
      plotRef.current.destroy();
      plotRef.current = null;
    }
    const width = hostRef.current.clientWidth || 600;
    const axisStroke = isDark ? "#e8e2d7" : "#3c372f";
    const gridStroke = isDark ? "rgba(255, 255, 255, 0.12)" : "rgba(0, 0, 0, 0.08)";
    const tickStroke = isDark ? "rgba(255, 255, 255, 0.35)" : "rgba(0, 0, 0, 0.25)";
    const opts: uPlot.Options = {
      width,
      height: plotHeight,
      series,
      scales: {
        x: { time: true },
        y: hasManualY
          ? {
              auto: false,
              min: Number(yMin),
              max: Number(yMax),
            }
          : { auto: true },
      },
      legend: {
        show: true,
        live: true,
      },
      axes: [
        {
          label: "time",
          space: 90,
          stroke: axisStroke,
          grid: { stroke: gridStroke },
          ticks: { stroke: tickStroke },
          values: (_u, vals) => vals.map(timeFormatter),
        },
        {
          label: yAxisLabel,
          size: 72,
          labelGap: 6,
          stroke: axisStroke,
          grid: { stroke: gridStroke },
          ticks: { stroke: tickStroke },
          values: (_u, vals) =>
            booleanOnly
              ? vals.map((v) => {
                  if (v <= 0.5) {
                    return "false";
                  }
                  if (v >= 0.5) {
                    return "true";
                  }
                  return "";
                })
              : vals.map(formatNumber),
        },
      ],
    };
    const data = buildPanelData();
    plotRef.current = new uPlot(opts, data as uPlot.AlignedData, hostRef.current);
    applyTimeWindow(plotRef.current, data, timeWindowS);

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
    series,
    timeWindowS,
    yAxisLabel,
    booleanOnly,
    timeFormatter,
    formatNumber,
    isDark,
    plotHeight,
    hasManualY,
    yMin,
    yMax,
    buildPanelData,
  ]);

  useEffect(() => {
    if (!plotRef.current) {
      return;
    }
    const data = buildPanelData();
    plotRef.current.setData(data as uPlot.AlignedData);
    applyTimeWindow(plotRef.current, data, timeWindowS);
  }, [tick, timeWindowS, buildPanelData]);

  return <div className="plot-panel" ref={hostRef} />;
}
