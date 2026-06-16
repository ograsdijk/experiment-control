import type { TraceKey } from "../../types";
import type { TelemetrySmoothingMode } from "./types";

export type TelemetrySmoothingOverlay = {
  traceIndex: number;
  values: number[];
};

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function smoothTelemetrySeriesSma(
  time: readonly number[],
  values: readonly number[],
  windowS: number
): number[] {
  const out = new Array<number>(values.length).fill(Number.NaN);
  const windowWidth = Math.max(1e-6, Number(windowS));
  for (let i = 0; i < values.length; i += 1) {
    const ti = time[i];
    if (!isFiniteNumber(ti)) {
      continue;
    }
    let sum = 0;
    let count = 0;
    for (let j = i; j >= 0; j -= 1) {
      const tj = time[j];
      if (!isFiniteNumber(tj)) {
        continue;
      }
      if (ti - tj > windowWidth) {
        break;
      }
      const value = values[j];
      if (!isFiniteNumber(value)) {
        continue;
      }
      sum += value;
      count += 1;
    }
    if (count > 0) {
      out[i] = sum / count;
    }
  }
  return out;
}

export function smoothTelemetrySeriesEma(
  time: readonly number[],
  values: readonly number[],
  windowS: number
): number[] {
  const out = new Array<number>(values.length).fill(Number.NaN);
  const tau = Math.max(1e-6, Number(windowS));
  const maxGap = tau * 4;
  let ema: number | null = null;
  let prevT: number | null = null;
  for (let i = 0; i < values.length; i += 1) {
    const ti = time[i];
    const value = values[i];
    if (!isFiniteNumber(ti) || !isFiniteNumber(value)) {
      continue;
    }
    if (
      ema === null ||
      prevT === null ||
      !isFiniteNumber(prevT) ||
      ti < prevT ||
      ti - prevT > maxGap
    ) {
      ema = value;
      prevT = ti;
      out[i] = ema;
      continue;
    }
    const dt = Math.max(0, ti - prevT);
    const alpha = dt <= 0 ? 1 : 1 - Math.exp(-dt / tau);
    ema = ema + alpha * (value - ema);
    prevT = ti;
    out[i] = ema;
  }
  return out;
}

export function buildTelemetrySmoothingOverlays(
  time: readonly number[],
  traces: readonly TraceKey[],
  valuesByTrace: ReadonlyArray<readonly number[]>,
  mode: TelemetrySmoothingMode,
  windowS: number
): TelemetrySmoothingOverlay[] {
  if (mode !== "sma" && mode !== "ema") {
    return [];
  }
  if (time.length <= 0 || traces.length <= 0) {
    return [];
  }
  const normalizedWindow = Math.max(1, Math.min(300, Number(windowS)));
  const out: TelemetrySmoothingOverlay[] = [];
  for (let traceIndex = 0; traceIndex < traces.length; traceIndex += 1) {
    const trace = traces[traceIndex];
    if (trace.valueKind === "boolean") {
      continue;
    }
    const values = valuesByTrace[traceIndex];
    if (!values || values.length !== time.length) {
      continue;
    }
    const smoothed =
      mode === "sma"
        ? smoothTelemetrySeriesSma(time, values, normalizedWindow)
        : smoothTelemetrySeriesEma(time, values, normalizedWindow);
    out.push({ traceIndex, values: smoothed });
  }
  return out;
}
