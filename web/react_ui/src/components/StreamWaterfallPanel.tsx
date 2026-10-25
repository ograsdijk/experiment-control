import { useEffect, useMemo, useRef } from "react";
import type { StreamFrame } from "./StreamRawPanel";
import {
  perfCount,
  perfCountScoped,
  perfMeasure,
} from "../features/performance/perfInstrumentation";
import { writeHeatmapRow } from "../utils/heatmapColormap";

type StreamWaterfallPanelProps = {
  panelId?: string;
  frames: StreamFrame[];
  historyRows: number;
  channelIndex: number;
  // Sample axis, resolved server-side. Labels only: the raster itself is
  // always drawn one column per sample.
  xUnits?: string | null;
  xLabel?: string | null;
  xIncrement?: number | null;
  xOrigin?: number | null;
  tick: number;
  colorScheme: "light" | "dark";
  plotHeight?: number;
  zScaleMode?: "auto" | "manual";
  zMin?: number | null;
  zMax?: number | null;
};

type WaterfallGrid = {
  rows: number;
  cols: number;
  values: Float32Array;
  seqStart: number | null;
  seqEnd: number | null;
  /** Frame seq of each row, oldest first. */
  rowSeqs: number[];
};

/** Coloured rows from the previous redraw, reusable while the scale holds. */
export type WaterfallPixelCache = {
  cols: number;
  zMin: number;
  zMax: number;
  /** Identity of each coloured row: frame seq plus a content fingerprint. */
  rowKeys: string[];
  data: Uint8ClampedArray<ArrayBuffer>;
};

/**
 * A row's identity for pixel reuse. The seq alone is not enough: the same
 * seq carries different data after a channel or source switch or a stream
 * restart, so the key also fingerprints the row's values.
 */
function waterfallRowKey(grid: WaterfallGrid, row: number): string {
  const start = row * grid.cols;
  let sum = 0;
  for (let c = 0; c < grid.cols; c += 1) {
    sum += grid.values[start + c];
  }
  return `${grid.rowSeqs[row]}:${sum}:${grid.values[start]}:${grid.values[start + grid.cols - 1]}`;
}

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
  if (!Array.isArray(raw) && !ArrayBuffer.isView(raw)) {
    return [];
  }
  const out: number[] = [];
  for (const item of Array.from(raw as ArrayLike<unknown>)) {
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
  if (frame.normalizedTrace) {
    return {
      y: frame.normalizedTrace,
      channelCount: Math.max(1, frame.normalizedChannelCount ?? 1),
    };
  }
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

export function buildStreamWaterfallGrid(
  frames: StreamFrame[],
  historyRows: number,
  channelIndex: number
): WaterfallGrid {
  if (frames.length <= 0) {
    return {
      rows: 0,
      cols: 0,
      values: new Float32Array(0),
      seqStart: null,
      seqEnd: null,
      rowSeqs: [],
    };
  }
  const keepRows = Math.max(1, Math.trunc(historyRows));
  const selectedFrames = frames.slice(-keepRows);
  const traces = selectedFrames
    .map((frame) => ({
      seq: frame.seq,
      y: extractTrace(frame, channelIndex).y,
    }))
    .filter((item) => item.y.length > 0);
  if (traces.length <= 0) {
    return {
      rows: 0,
      cols: 0,
      values: new Float32Array(0),
      seqStart: null,
      seqEnd: null,
      rowSeqs: [],
    };
  }

  const rows = traces.length;
  const cols = Math.min(...traces.map((item) => item.y.length));
  if (!Number.isFinite(cols) || cols <= 0) {
    return {
      rows: 0,
      cols: 0,
      values: new Float32Array(0),
      seqStart: null,
      seqEnd: null,
      rowSeqs: [],
    };
  }

  const values = new Float32Array(rows * cols);
  for (let r = 0; r < rows; r += 1) {
    const trace = traces[r].y;
    const start = trace.length - cols;
    for (let c = 0; c < cols; c += 1) {
      values[r * cols + c] = trace[start + c];
    }
  }

  return {
    rows,
    cols,
    values,
    seqStart: traces[0].seq,
    seqEnd: traces[traces.length - 1].seq,
    rowSeqs: traces.map((item) => item.seq),
  };
}

export function computeStreamWaterfallAutoZRange(
  frames: StreamFrame[],
  historyRows: number,
  channelIndex: number
): { min: number; max: number } | null {
  return waterfallGridZRange(buildStreamWaterfallGrid(frames, historyRows, channelIndex));
}

/** Auto z range of an already built grid (min/max, padded when flat). */
export function waterfallGridZRange(
  grid: WaterfallGrid
): { min: number; max: number } | null {
  if (grid.values.length <= 0) {
    return null;
  }
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (let i = 0; i < grid.values.length; i += 1) {
    const value = Number(grid.values[i]);
    if (!Number.isFinite(value)) {
      continue;
    }
    if (value < min) {
      min = value;
    }
    if (value > max) {
      max = value;
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return null;
  }
  if (min === max) {
    const pad = Math.abs(min) > 0 ? Math.abs(min) * 0.05 : 1;
    return { min: min - pad, max: max + pad };
  }
  return { min, max };
}

/**
 * RGBA pixels for the grid. Rows already coloured at the same z range and
 * width are copied from `previous` (matched by seq and content, see
 * waterfallRowKey); only new rows are coloured. With an auto range the scale changes only when a new row sets an
 * extreme or the row holding one scrolls out, so most redraws colour one row.
 */
export function colorWaterfallRows(
  grid: WaterfallGrid,
  zMin: number,
  zMax: number,
  previous: WaterfallPixelCache | null
): { cache: WaterfallPixelCache; coloredRows: number } {
  const rowBytes = grid.cols * 4;
  const data = new Uint8ClampedArray(grid.rows * rowBytes);
  const reusable =
    previous !== null &&
    previous.cols === grid.cols &&
    previous.zMin === zMin &&
    previous.zMax === zMax;
  const previousRowByKey = new Map<string, number>();
  if (reusable) {
    previous.rowKeys.forEach((key, row) => previousRowByKey.set(key, row));
  }
  const rowKeys: string[] = new Array(grid.rows);
  const span = Math.max(1e-12, zMax - zMin);
  let coloredRows = 0;
  for (let r = 0; r < grid.rows; r += 1) {
    rowKeys[r] = waterfallRowKey(grid, r);
    const previousRow = previousRowByKey.get(rowKeys[r]);
    if (reusable && previousRow !== undefined) {
      const from = previousRow * rowBytes;
      data.set(previous.data.subarray(from, from + rowBytes), r * rowBytes);
      continue;
    }
    writeHeatmapRow(data, r * rowBytes, grid.values, r * grid.cols, grid.cols, zMin, span);
    coloredRows += 1;
  }
  return {
    cache: { cols: grid.cols, zMin, zMax, rowKeys, data },
    coloredRows,
  };
}

function formatValue(value: number | null): string {
  if (value === null || !Number.isFinite(value)) {
    return "n/a";
  }
  const abs = Math.abs(value);
  if (abs > 0 && (abs >= 1e4 || abs < 1e-3)) {
    return value.toExponential(3);
  }
  return value.toFixed(3).replace(/\.?0+$/, "");
}

export function StreamWaterfallPanel({
  panelId,
  frames,
  historyRows,
  channelIndex,
  xUnits,
  xLabel,
  xIncrement,
  xOrigin,
  tick,
  colorScheme,
  plotHeight = 340,
  zScaleMode = "auto",
  zMin = null,
  zMax = null,
}: StreamWaterfallPanelProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const heatmapRef = useRef<HTMLCanvasElement | null>(null);
  const pixelCacheRef = useRef<WaterfallPixelCache | null>(null);
  const drawRef = useRef<() => void>(() => undefined);
  const isDark = colorScheme === "dark";

  const grid = useMemo(
    () => perfMeasure("waterfall.conversion_ms", () =>
      buildStreamWaterfallGrid(frames, historyRows, channelIndex)
    ),
    [frames, historyRows, channelIndex, tick]
  );

  const zRange = useMemo(() => {
    const manual =
      zScaleMode === "manual" &&
      typeof zMin === "number" &&
      typeof zMax === "number" &&
      Number.isFinite(zMin) &&
      Number.isFinite(zMax) &&
      zMin < zMax;
    if (manual) {
      return { min: Number(zMin), max: Number(zMax), manual: true };
    }
    const auto = waterfallGridZRange(grid);
    return {
      min: auto?.min ?? 0,
      max: auto?.max ?? 1,
      manual: false,
    };
  }, [zScaleMode, zMin, zMax, grid]);

  useEffect(() => {
    if (!hostRef.current || !canvasRef.current) {
      return;
    }
    let raf = 0;
    const draw = () => {
      if (!hostRef.current || !canvasRef.current) {
        return;
      }
      const width = Math.max(320, Math.trunc(hostRef.current.clientWidth || 600));
        const height = Math.max(240, Math.trunc(plotHeight));
      const dpr = Math.max(1, Number(window.devicePixelRatio || 1));
      const canvas = canvasRef.current;
      // Assigning a canvas size reallocates and clears it; only do it on change.
      const pixelWidth = Math.max(1, Math.trunc(width * dpr));
      const pixelHeight = Math.max(1, Math.trunc(height * dpr));
      if (canvas.width !== pixelWidth) canvas.width = pixelWidth;
      if (canvas.height !== pixelHeight) canvas.height = pixelHeight;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        return;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      ctx.textAlign = "left";

      const fg = isDark ? "#ebe8e2" : "#2d302d";
      const frameStroke = isDark ? "rgba(235, 232, 226, 0.35)" : "rgba(45, 48, 45, 0.25)";
      const bg = isDark ? "#1a1a1a" : "#f6f6f3";
      const left = 56;
      const top = 12;
      const right = 12;
      const bottom = 30;
      const plotW = Math.max(1, width - left - right);
      const plotH = Math.max(1, height - top - bottom);

      ctx.fillStyle = bg;
      ctx.fillRect(left, top, plotW, plotH);
      ctx.strokeStyle = frameStroke;
      ctx.strokeRect(left + 0.5, top + 0.5, Math.max(0, plotW - 1), Math.max(0, plotH - 1));

      if (grid.rows > 0 && grid.cols > 0 && grid.values.length > 0) {
        if (!heatmapRef.current) {
          heatmapRef.current = document.createElement("canvas");
        }
        const hm = heatmapRef.current;
        if (hm.width !== grid.cols) hm.width = grid.cols;
        if (hm.height !== grid.rows) hm.height = grid.rows;
        const hmCtx = hm.getContext("2d");
        if (hmCtx) {
          const { cache } = colorWaterfallRows(
            grid,
            zRange.min,
            zRange.max,
            pixelCacheRef.current
          );
          pixelCacheRef.current = cache;
          hmCtx.putImageData(new ImageData(cache.data, grid.cols, grid.rows), 0, 0);
          ctx.imageSmoothingEnabled = false;
          perfCount("canvas.waterfall_redraws");
          perfCountScoped("canvas.redraws", panelId);
          ctx.drawImage(hm, left, top, plotW, plotH);
        }
      } else {
        ctx.fillStyle = isDark ? "rgba(235, 232, 226, 0.72)" : "rgba(45, 48, 45, 0.72)";
        ctx.font = "12px sans-serif";
        ctx.fillText("No trace rows yet", left + 8, top + 18);
      }

      ctx.fillStyle = fg;
      ctx.font = "12px sans-serif";
      ctx.textAlign = "left";
      const xScale = Number(xIncrement);
      const xOriginValue = Number(xOrigin ?? 0);
      const hasXScale =
        Number.isFinite(xScale) &&
        xScale !== 0 &&
        xScale !== 1 &&
        Number.isFinite(xOriginValue);
      const lastSample = Math.max(0, grid.cols - 1);
      const formatX = (sample: number) =>
        hasXScale
          ? (xOriginValue + xScale * sample).toPrecision(4)
          : String(Math.trunc(sample));
      ctx.fillText(formatX(0), left, height - 10);
      ctx.textAlign = "right";
      ctx.fillText(formatX(lastSample), left + plotW, height - 10);
      ctx.textAlign = "center";
      ctx.fillText(
        hasXScale
          ? xLabel || (xUnits ? `time (${xUnits})` : "time")
          : "sample index",
        left + plotW * 0.5,
        height - 10
      );

      ctx.save();
      ctx.translate(15, top + plotH * 0.5);
      ctx.rotate(-Math.PI * 0.5);
      ctx.textAlign = "center";
      ctx.fillText("trace sequence", 0, 0);
      ctx.restore();

      ctx.textAlign = "left";
      ctx.fillText(
        `seq ${grid.seqStart ?? "n/a"} -> ${grid.seqEnd ?? "n/a"}`,
        left + 4,
        top + 14
      );
      ctx.textAlign = "right";
      ctx.fillText(
        `z ${formatValue(zRange.min)} .. ${formatValue(zRange.max)}${zRange.manual ? " (manual)" : ""}`,
        left + plotW - 4,
        top + 14
      );
    };

    drawRef.current = draw;
    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
    };
  }, [grid, zRange, isDark, plotHeight, panelId]);

  // One observer for the panel's lifetime; it redraws with the latest data.
  useEffect(() => {
    const host = hostRef.current;
    if (!host || typeof ResizeObserver === "undefined") {
      return;
    }
    let raf = 0;
    const resize = new ResizeObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => drawRef.current());
    });
    resize.observe(host);
    return () => {
      resize.disconnect();
      cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <div className="plot-panel" ref={hostRef} style={{ minHeight: plotHeight }}>
      <canvas ref={canvasRef} />
    </div>
  );
}

