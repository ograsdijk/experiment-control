import { useEffect, useMemo, useRef } from "react";

export type Bin2dReducer = "mean" | "max" | "min" | "count" | "std" | "sem" | "sum";

export type StreamBin2dSeries = {
  xBins: number[];
  yBins: number[];
  count: number[][];
  sum: number[][];
  mean: number[][];
  std: number[][];
  sem: number[][];
  min: number[][];
  max: number[][];
};

type StreamBin2dPanelProps = {
  series: StreamBin2dSeries | null;
  reducer: Bin2dReducer;
  tick: number;
  colorScheme: "light" | "dark";
  zScaleMode?: "auto" | "manual";
  zMin?: number | null;
  zMax?: number | null;
};

type Grid = {
  nx: number;
  ny: number;
  values: Float32Array;
  xMin: number | null;
  xMax: number | null;
  yMin: number | null;
  yMax: number | null;
};

function clamp01(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  if (value <= 0) {
    return 0;
  }
  if (value >= 1) {
    return 1;
  }
  return value;
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function colorAt(tRaw: number): [number, number, number] {
  const t = clamp01(tRaw);
  const stops: Array<[number, number, number, number]> = [
    [0.0, 10, 12, 36],
    [0.25, 51, 85, 137],
    [0.5, 28, 170, 156],
    [0.75, 168, 220, 50],
    [1.0, 255, 243, 156],
  ];
  for (let i = 0; i < stops.length - 1; i += 1) {
    const a = stops[i];
    const b = stops[i + 1];
    if (t < a[0] || t > b[0]) {
      continue;
    }
    const local = (t - a[0]) / Math.max(1e-9, b[0] - a[0]);
    return [
      Math.trunc(lerp(a[1], b[1], local)),
      Math.trunc(lerp(a[2], b[2], local)),
      Math.trunc(lerp(a[3], b[3], local)),
    ];
  }
  const tail = stops[stops.length - 1];
  return [tail[1], tail[2], tail[3]];
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

function pickGrid(series: StreamBin2dSeries, reducer: Bin2dReducer): number[][] {
  if (reducer === "count") {
    return series.count;
  }
  if (reducer === "sum") {
    return series.sum;
  }
  if (reducer === "std") {
    return series.std;
  }
  if (reducer === "sem") {
    return series.sem;
  }
  if (reducer === "min") {
    return series.min;
  }
  if (reducer === "max") {
    return series.max;
  }
  return series.mean;
}

function buildGrid(series: StreamBin2dSeries | null, reducer: Bin2dReducer): Grid {
  if (!series) {
    return {
      nx: 0,
      ny: 0,
      values: new Float32Array(0),
      xMin: null,
      xMax: null,
      yMin: null,
      yMax: null,
    };
  }
  const xBins = Array.isArray(series.xBins) ? series.xBins : [];
  const yBins = Array.isArray(series.yBins) ? series.yBins : [];
  const matrix = pickGrid(series, reducer);
  if (!Array.isArray(matrix) || matrix.length <= 0 || xBins.length <= 0 || yBins.length <= 0) {
    return {
      nx: 0,
      ny: 0,
      values: new Float32Array(0),
      xMin: null,
      xMax: null,
      yMin: null,
      yMax: null,
    };
  }
  const nx = Math.min(xBins.length, matrix.length);
  let ny = yBins.length;
  for (let xi = 0; xi < nx; xi += 1) {
    const row = Array.isArray(matrix[xi]) ? matrix[xi] : [];
    ny = Math.min(ny, row.length);
  }
  if (nx <= 0 || ny <= 0) {
    return {
      nx: 0,
      ny: 0,
      values: new Float32Array(0),
      xMin: null,
      xMax: null,
      yMin: null,
      yMax: null,
    };
  }

  const values = new Float32Array(nx * ny);
  for (let yi = 0; yi < ny; yi += 1) {
    const srcY = ny - 1 - yi;
    for (let xi = 0; xi < nx; xi += 1) {
      const row = Array.isArray(matrix[xi]) ? matrix[xi] : [];
      const raw = Number(row[srcY]);
      values[yi * nx + xi] = Number.isFinite(raw) ? raw : Number.NaN;
    }
  }

  const xMin = Number(xBins[0]);
  const xMax = Number(xBins[nx - 1]);
  const yMin = Number(yBins[0]);
  const yMax = Number(yBins[ny - 1]);
  return {
    nx,
    ny,
    values,
    xMin: Number.isFinite(xMin) ? xMin : null,
    xMax: Number.isFinite(xMax) ? xMax : null,
    yMin: Number.isFinite(yMin) ? yMin : null,
    yMax: Number.isFinite(yMax) ? yMax : null,
  };
}

export function computeStreamBin2dAutoZRange(
  series: StreamBin2dSeries | null,
  reducer: Bin2dReducer
): { min: number; max: number } | null {
  const grid = buildGrid(series, reducer);
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

export function StreamBin2dPanel({
  series,
  reducer,
  tick,
  colorScheme,
  zScaleMode = "auto",
  zMin = null,
  zMax = null,
}: StreamBin2dPanelProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const heatmapRef = useRef<HTMLCanvasElement | null>(null);
  const isDark = colorScheme === "dark";

  const grid = useMemo(() => buildGrid(series, reducer), [series, reducer, tick]);

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
    const auto = computeStreamBin2dAutoZRange(series, reducer);
    return { min: auto?.min ?? 0, max: auto?.max ?? 1, manual: false };
  }, [zScaleMode, zMin, zMax, series, reducer]);

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
      const height = 340;
      const dpr = Math.max(1, Number(window.devicePixelRatio || 1));
      const canvas = canvasRef.current;
      canvas.width = Math.max(1, Math.trunc(width * dpr));
      canvas.height = Math.max(1, Math.trunc(height * dpr));
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        return;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);

      const fg = isDark ? "#ebe8e2" : "#2d302d";
      const frameStroke = isDark ? "rgba(235, 232, 226, 0.35)" : "rgba(45, 48, 45, 0.25)";
      const bg = isDark ? "#1a1a1a" : "#f6f6f3";
      const left = 62;
      const top = 18;
      const right = 16;
      const bottom = 34;
      const plotW = Math.max(1, width - left - right);
      const plotH = Math.max(1, height - top - bottom);

      ctx.fillStyle = bg;
      ctx.fillRect(left, top, plotW, plotH);
      ctx.strokeStyle = frameStroke;
      ctx.strokeRect(left + 0.5, top + 0.5, Math.max(0, plotW - 1), Math.max(0, plotH - 1));

      if (grid.nx > 0 && grid.ny > 0 && grid.values.length > 0) {
        if (!heatmapRef.current) {
          heatmapRef.current = document.createElement("canvas");
        }
        const hm = heatmapRef.current;
        hm.width = grid.nx;
        hm.height = grid.ny;
        const hmCtx = hm.getContext("2d");
        if (hmCtx) {
          const img = hmCtx.createImageData(grid.nx, grid.ny);
          const span = Math.max(1e-12, zRange.max - zRange.min);
          for (let yi = 0; yi < grid.ny; yi += 1) {
            for (let xi = 0; xi < grid.nx; xi += 1) {
              const raw = Number(grid.values[yi * grid.nx + xi]);
              const idx = (yi * grid.nx + xi) * 4;
              if (!Number.isFinite(raw)) {
                img.data[idx] = 0;
                img.data[idx + 1] = 0;
                img.data[idx + 2] = 0;
                img.data[idx + 3] = 0;
                continue;
              }
              const t = clamp01((raw - zRange.min) / span);
              const [cr, cg, cb] = colorAt(t);
              img.data[idx] = cr;
              img.data[idx + 1] = cg;
              img.data[idx + 2] = cb;
              img.data[idx + 3] = 255;
            }
          }
          hmCtx.putImageData(img, 0, 0);
          ctx.imageSmoothingEnabled = false;
          ctx.drawImage(hm, left, top, plotW, plotH);
        }
      } else {
        ctx.fillStyle = isDark ? "rgba(235, 232, 226, 0.72)" : "rgba(45, 48, 45, 0.72)";
        ctx.font = "12px sans-serif";
        ctx.fillText("No binned data yet", left + 8, top + 18);
      }

      ctx.fillStyle = fg;
      ctx.font = "12px sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(formatValue(grid.xMin), left, height - 10);
      ctx.textAlign = "right";
      ctx.fillText(formatValue(grid.xMax), left + plotW, height - 10);
      ctx.textAlign = "center";
      ctx.fillText("x", left + plotW * 0.5, height - 10);

      ctx.save();
      ctx.translate(16, top + plotH * 0.5);
      ctx.rotate(-Math.PI * 0.5);
      ctx.textAlign = "center";
      ctx.fillText("y", 0, 0);
      ctx.restore();

      ctx.textAlign = "left";
      ctx.fillText(formatValue(grid.yMin), 4, top + plotH - 4);
      ctx.fillText(formatValue(grid.yMax), 4, top + 12);

      ctx.textAlign = "right";
      ctx.fillText(
        `z(${reducer}) ${formatValue(zRange.min)} .. ${formatValue(zRange.max)}${
          zRange.manual ? " (manual)" : ""
        }`,
        left + plotW - 4,
        top + 14
      );
    };

    const schedule = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(draw);
    };
    schedule();

    const resize = new ResizeObserver(() => {
      schedule();
    });
    resize.observe(hostRef.current);

    return () => {
      resize.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [grid, zRange, isDark, reducer]);

  return (
    <div className="plot-panel" ref={hostRef}>
      <canvas ref={canvasRef} />
    </div>
  );
}

