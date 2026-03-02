import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";
import { traceColorAt } from "../utils/traceColors";

export type StreamFrame = {
  seq: number;
  shape: number[];
  values: unknown;
};

export type StreamExtraSeries = {
  label: string;
  values: number[];
};

type StreamRawPanelProps = {
  frames: StreamFrame[];
  overlayCount: number;
  channelIndex: number;
  tick: number;
  colorScheme: "light" | "dark";
  plotHeight?: number;
  units?: string | null;
  extraSeries?: StreamExtraSeries[];
  yScaleMode?: "auto" | "manual";
  yMin?: number | null;
  yMax?: number | null;
};

function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "boolean") {
    return value ? 1 : 0;
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return null;
}

function toNumericList(raw: unknown): number[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const out: number[] = [];
  for (const item of raw) {
    const value = asNumber(item);
    if (value === null) {
      return [];
    }
    out.push(value);
  }
  return out;
}

function extractTrace(
  frame: StreamFrame,
  channelIndex: number
): { y: number[]; channelCount: number } {
  const shape = Array.isArray(frame.shape) ? frame.shape.map((v) => Number(v)) : [];
  const values = frame.values;
  if (shape.length <= 1) {
    return { y: toNumericList(values), channelCount: 1 };
  }
  if (shape.length !== 2 || !Array.isArray(values)) {
    return { y: [], channelCount: 1 };
  }

  const rows = values
    .map((row) => toNumericList(row))
    .filter((row) => row.length > 0);
  if (rows.length === 0) {
    return { y: [], channelCount: 1 };
  }
  const rowCount = rows.length;
  const colCount = rows[0].length;
  if (colCount <= 0) {
    return { y: [], channelCount: 1 };
  }

  // Heuristic: shorter axis is usually channel, longer axis is usually time.
  if (rowCount <= colCount) {
    const channel = Math.max(0, Math.min(Math.trunc(channelIndex), rowCount - 1));
    return {
      y: rows[channel],
      channelCount: Math.max(1, rowCount),
    };
  }
  const channel = Math.max(0, Math.min(Math.trunc(channelIndex), colCount - 1));
  return {
    y: rows.map((row) => row[channel]).filter((value) => Number.isFinite(value)),
    channelCount: Math.max(1, colCount),
  };
}

export function buildStreamRawData(
  frames: StreamFrame[],
  overlayCount: number,
  channelIndex: number,
  extraSeries: StreamExtraSeries[] = []
): { data: number[][]; labels: string[] } {
  if (frames.length === 0) {
    if (extraSeries.length <= 0) {
      return { data: [[], []], labels: ["sample", "trace"] };
    }
    const nonEmpty = extraSeries
      .map((item) => ({
        label: item.label,
        values: item.values.filter((value) => Number.isFinite(value)),
      }))
      .filter((item) => item.values.length > 0);
    if (nonEmpty.length <= 0) {
      return { data: [[], []], labels: ["sample", "trace"] };
    }
    const minLen = Math.min(...nonEmpty.map((item) => item.values.length));
    if (!Number.isFinite(minLen) || minLen <= 0) {
      return { data: [[], []], labels: ["sample", "trace"] };
    }
    const x = Array.from({ length: minLen }, (_v, idx) => idx);
    const ySeries = nonEmpty.map((item) => item.values.slice(-minLen));
    return {
      data: [x, ...ySeries],
      labels: ["sample", ...nonEmpty.map((item) => item.label)],
    };
  }
  const n = Math.max(1, Math.trunc(overlayCount));
  const selected = frames.slice(-n);
  const traces = selected.map((frame) => {
    const extracted = extractTrace(frame, channelIndex);
    return {
      seq: frame.seq,
      y: extracted.y,
      channelCount: extracted.channelCount,
    };
  });
  const nonEmpty = traces.filter((trace) => trace.y.length > 0);
  if (nonEmpty.length === 0) {
    return { data: [[], []], labels: ["sample", "trace"] };
  }

  const overlays = nonEmpty.slice(-n);
  const minLen = Math.min(...overlays.map((trace) => trace.y.length));
  if (!Number.isFinite(minLen) || minLen <= 0) {
    return { data: [[], []], labels: ["sample", "trace"] };
  }
  const x = Array.from({ length: minLen }, (_v, idx) => idx);
  const ySeries = overlays.map((trace) => trace.y.slice(-minLen));
  const maxChannelCount = Math.max(...overlays.map((trace) => trace.channelCount ?? 1));
  const prefix =
    maxChannelCount > 1 ? `ch ${Math.max(0, Math.trunc(channelIndex))} ` : "";
  const labels = overlays.map((trace) => `${prefix}seq ${trace.seq}`);
  const cleanExtra = extraSeries
    .map((item) => ({
      label: String(item.label ?? "").trim() || "overlay",
      values: item.values.filter((value) => Number.isFinite(value)),
    }))
    .filter((item) => item.values.length > 0)
    .map((item) => ({
      label: item.label,
      values: item.values.slice(-minLen),
    }));
  return {
    data: [x, ...ySeries, ...cleanExtra.map((item) => item.values)],
    labels: ["sample", ...labels, ...cleanExtra.map((item) => item.label)],
  };
}

export function computeStreamRawAutoYRange(
  frames: StreamFrame[],
  overlayCount: number,
  channelIndex: number,
  extraSeries: StreamExtraSeries[] = []
): { min: number; max: number } | null {
  const built = buildStreamRawData(frames, overlayCount, channelIndex, extraSeries);
  if (built.data.length <= 1) {
    return null;
  }
  let minY = Number.POSITIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (let si = 1; si < built.data.length; si += 1) {
    const series = built.data[si];
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
  if (!Number.isFinite(minY) || !Number.isFinite(maxY)) {
    return null;
  }
  if (minY === maxY) {
    const pad = Math.abs(minY) > 0 ? Math.abs(minY) * 0.05 : 1;
    return { min: minY - pad, max: maxY + pad };
  }
  return { min: minY, max: maxY };
}

export function StreamRawPanel({
  frames,
  overlayCount,
  channelIndex,
  tick,
  colorScheme,
  plotHeight = 320,
  units,
  extraSeries = [],
  yScaleMode = "auto",
  yMin = null,
  yMax = null,
}: StreamRawPanelProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);
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

  const built = useMemo(
    () => buildStreamRawData(frames, overlayCount, channelIndex, extraSeries),
    [frames, overlayCount, channelIndex, extraSeries, tick]
  );

  const series = useMemo(() => {
    return [
      { label: "sample" },
      ...built.labels.slice(1).map((label, idx) => ({
        label,
        stroke: traceColorAt(idx),
        width: 1.6,
        value: (_u: uPlot, v: number | Date | null) => formatNumber(v),
      })),
    ];
  }, [built.labels, formatNumber]);

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
    const width = hostRef.current.clientWidth || 600;
    const opts: uPlot.Options = {
      width,
      height: plotHeight,
      series,
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
      legend: { show: false, live: false },
      axes: [
        {
          label: "sample index",
          stroke: axisStroke,
          grid: { stroke: gridStroke },
          ticks: { stroke: tickStroke },
          values: (_u, vals) => vals.map((v) => String(Math.trunc(Number(v)))),
        },
        {
          label: units ?? "",
          stroke: axisStroke,
          grid: { stroke: gridStroke },
          ticks: { stroke: tickStroke },
          values: (_u, vals) => vals.map((v) => formatNumber(Number(v))),
        },
      ],
    };
    plotRef.current = new uPlot(opts, built.data, hostRef.current);
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
    built.data,
    isDark,
    units,
    formatNumber,
    hasManualY,
    plotHeight,
    yMin,
    yMax,
  ]);

  useEffect(() => {
    if (!plotRef.current) {
      return;
    }
    plotRef.current.setData(built.data);
  }, [tick, built.data]);

  return <div className="plot-panel" ref={hostRef} />;
}
