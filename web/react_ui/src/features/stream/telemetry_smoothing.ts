import type { TraceKey } from "../../types";
import type { TelemetrySmoothingMode } from "./types";

export type TelemetrySmoothingOverlay = {
  traceIndex: number;
  values: number[];
};

export type SmoothingWorkCounter = { operations: number };

export type SmoothingCacheStats = { rebuilds: number; processedSamples: number };

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function smoothTelemetrySeriesSma(
  time: readonly number[],
  values: readonly number[],
  windowS: number,
  target: number[] = [],
  work?: SmoothingWorkCounter
): number[] {
  const length = Math.min(time.length, values.length);
  target.length = length;
  target.fill(Number.NaN);
  const windowWidth = Math.max(1e-6, Number(windowS));
  let monotonic = true;
  let previousFiniteTime: number | null = null;
  for (let i = 0; i < length; i += 1) {
    work && (work.operations += 1);
    const ti = time[i];
    if (!isFiniteNumber(ti)) {
      continue;
    }
    if (previousFiniteTime !== null && ti < previousFiniteTime) {
      monotonic = false;
      break;
    }
    previousFiniteTime = ti;
  }
  if (!monotonic) {
    return smoothTelemetrySeriesSmaNonMonotonic(
      time,
      values,
      windowWidth,
      target,
      length,
      work
    );
  }

  let left = 0;
  let sum = 0;
  let count = 0;
  for (let right = 0; right < length; right += 1) {
    work && (work.operations += 1);
    const ti = time[right];
    if (!isFiniteNumber(ti)) {
      continue;
    }
    const value = values[right];
    if (isFiniteNumber(value)) {
      sum += value;
      count += 1;
    }
    while (left <= right) {
      work && (work.operations += 1);
      const tj = time[left];
      if (!isFiniteNumber(tj)) {
        left += 1;
        continue;
      }
      if (ti - tj > windowWidth) {
        const expired = values[left];
        if (isFiniteNumber(expired)) {
          sum -= expired;
          count -= 1;
        }
        left += 1;
        continue;
      }
      break;
    }
    if (count > 0) {
      target[right] = sum / count;
    }
  }
  return target;
}

function lowerBound(sorted: readonly number[], value: number): number {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (sorted[mid] < value) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  return lo;
}

/** Exact legacy window boundaries for rare non-monotonic input, without O(N²) scans. */
function smoothTelemetrySeriesSmaNonMonotonic(
  time: readonly number[],
  values: readonly number[],
  windowWidth: number,
  target: number[],
  length: number,
  work?: SmoothingWorkCounter
): number[] {
  const sortedTimes = Array.from(
    new Set(time.slice(0, length).filter(isFiniteNumber))
  ).sort((a, b) => a - b);
  const tree = new Array<number>(sortedTimes.length + 1).fill(-1);
  const prefixSum = new Array<number>(length + 1).fill(0);
  const prefixCount = new Array<number>(length + 1).fill(0);
  const queryMaxIndex = (endExclusive: number) => {
    let result = -1;
    for (let index = endExclusive; index > 0; index -= index & -index) {
      work && (work.operations += 1);
      result = Math.max(result, tree[index]);
    }
    return result;
  };
  const recordIndex = (coordinate: number, sampleIndex: number) => {
    for (
      let index = coordinate + 1;
      index < tree.length;
      index += index & -index
    ) {
      work && (work.operations += 1);
      tree[index] = Math.max(tree[index], sampleIndex);
    }
  };

  for (let i = 0; i < length; i += 1) {
    work && (work.operations += 1);
    const ti = time[i];
    const value = values[i];
    const validSample = isFiniteNumber(ti) && isFiniteNumber(value);
    prefixSum[i + 1] = prefixSum[i] + (validSample ? value : 0);
    prefixCount[i + 1] = prefixCount[i] + (validSample ? 1 : 0);
    if (!isFiniteNumber(ti)) {
      continue;
    }
    const boundary = queryMaxIndex(lowerBound(sortedTimes, ti - windowWidth));
    const count = prefixCount[i + 1] - prefixCount[boundary + 1];
    if (count > 0) {
      target[i] = (prefixSum[i + 1] - prefixSum[boundary + 1]) / count;
    }
    recordIndex(lowerBound(sortedTimes, ti), i);
  }
  return target;
}

export function smoothTelemetrySeriesEma(
  time: readonly number[],
  values: readonly number[],
  windowS: number,
  target: number[] = []
): number[] {
  const length = Math.min(time.length, values.length);
  target.length = length;
  target.fill(Number.NaN);
  const tau = Math.max(1e-6, Number(windowS));
  const maxGap = tau * 4;
  let ema: number | null = null;
  let prevT: number | null = null;
  for (let i = 0; i < length; i += 1) {
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
      target[i] = ema;
      continue;
    }
    const dt = Math.max(0, ti - prevT);
    const alpha = dt <= 0 ? 1 : 1 - Math.exp(-dt / tau);
    ema = ema + alpha * (value - ema);
    prevT = ti;
    target[i] = ema;
  }
  return target;
}

export class TelemetrySmoothingCache {
  readonly values: number[] = [];
  readonly stats: SmoothingCacheStats = { rebuilds: 0, processedSamples: 0 };

  private mode: TelemetrySmoothingMode = "none";
  private windowS = 0;
  private inputLength = 0;
  private timeSequence = -1;
  private valueSequence = -1;
  private timeStructure = -1;
  private valueStructure = -1;
  private canAppend = false;
  private ema: number | null = null;
  private previousTime: number | null = null;
  private smaLeft = 0;
  private smaSum = 0;
  private smaCount = 0;

  update(
    time: readonly number[],
    input: readonly number[],
    mode: TelemetrySmoothingMode,
    windowS: number,
    timeSequence: number,
    valueSequence: number,
    timeStructure: number,
    valueStructure: number
  ): number[] {
    const length = Math.min(time.length, input.length);
    const normalizedMode = mode === "sma" || mode === "ema" ? mode : "none";
    const normalizedWindow = Math.max(1e-6, Number(windowS));
    const timeDelta = timeSequence - this.timeSequence;
    const valueDelta = valueSequence - this.valueSequence;
    const appendStart = this.inputLength;
    const appendOnly =
      this.canAppend &&
      normalizedMode === this.mode &&
      normalizedWindow === this.windowS &&
      timeStructure === this.timeStructure &&
      valueStructure === this.valueStructure &&
      timeDelta > 0 &&
      timeDelta === valueDelta &&
      length === this.inputLength + timeDelta;

    if (normalizedMode === "none") {
      this.values.length = 0;
      this.canAppend = false;
    } else if (appendOnly) {
      this.values.length = length;
      if (normalizedMode === "sma") {
        this.appendSma(time, input, appendStart, normalizedWindow);
      } else {
        this.appendEma(time, input, appendStart, normalizedWindow);
      }
    } else {
      this.rebuild(time, input, normalizedMode, normalizedWindow);
    }

    this.mode = normalizedMode;
    this.windowS = normalizedWindow;
    this.inputLength = length;
    this.timeSequence = timeSequence;
    this.valueSequence = valueSequence;
    this.timeStructure = timeStructure;
    this.valueStructure = valueStructure;
    return this.values;
  }

  private rebuild(
    time: readonly number[],
    input: readonly number[],
    mode: TelemetrySmoothingMode,
    windowS: number
  ): void {
    this.stats.rebuilds += 1;
    this.stats.processedSamples += Math.min(time.length, input.length);
    this.resetState();
    if (mode === "sma") {
      smoothTelemetrySeriesSma(time, input, windowS, this.values);
      this.restoreSmaState(time, input, windowS);
    } else if (mode === "ema") {
      smoothTelemetrySeriesEma(time, input, windowS, this.values);
      this.restoreEmaState(time, input);
    } else {
      this.values.length = 0;
    }
  }

  private resetState(): void {
    this.ema = null;
    this.previousTime = null;
    this.smaLeft = 0;
    this.smaSum = 0;
    this.smaCount = 0;
    this.canAppend = true;
  }

  private restoreEmaState(time: readonly number[], input: readonly number[]): void {
    for (let index = Math.min(time.length, input.length) - 1; index >= 0; index -= 1) {
      if (isFiniteNumber(time[index]) && isFiniteNumber(input[index])) {
        this.previousTime = time[index];
        this.ema = this.values[index];
        return;
      }
    }
  }

  private restoreSmaState(
    time: readonly number[],
    input: readonly number[],
    windowS: number
  ): void {
    const length = Math.min(time.length, input.length);
    let lastFiniteIndex = -1;
    let lastFiniteTime = 0;
    let previousFiniteTime: number | null = null;
    for (let index = 0; index < length; index += 1) {
      const ti = time[index];
      if (!isFiniteNumber(ti)) {
        continue;
      }
      if (previousFiniteTime !== null && ti < previousFiniteTime) {
        this.canAppend = false;
        return;
      }
      previousFiniteTime = ti;
      lastFiniteIndex = index;
      lastFiniteTime = ti;
    }
    if (lastFiniteIndex < 0) {
      this.smaLeft = length;
      return;
    }
    this.previousTime = lastFiniteTime;
    this.smaLeft = 0;
    while (this.smaLeft <= lastFiniteIndex) {
      const ti = time[this.smaLeft];
      if (!isFiniteNumber(ti) || lastFiniteTime - ti > windowS) {
        this.smaLeft += 1;
        continue;
      }
      break;
    }
    for (let index = this.smaLeft; index <= lastFiniteIndex; index += 1) {
      if (isFiniteNumber(time[index]) && isFiniteNumber(input[index])) {
        this.smaSum += input[index];
        this.smaCount += 1;
      }
    }
  }

  private appendEma(
    time: readonly number[],
    input: readonly number[],
    start: number,
    windowS: number
  ): void {
    const maxGap = windowS * 4;
    for (let index = start; index < this.values.length; index += 1) {
      this.stats.processedSamples += 1;
      const ti = time[index];
      const value = input[index];
      this.values[index] = Number.NaN;
      if (!isFiniteNumber(ti) || !isFiniteNumber(value)) {
        continue;
      }
      if (
        this.ema === null ||
        this.previousTime === null ||
        ti < this.previousTime ||
        ti - this.previousTime > maxGap
      ) {
        this.ema = value;
      } else {
        const dt = Math.max(0, ti - this.previousTime);
        const alpha = dt <= 0 ? 1 : 1 - Math.exp(-dt / windowS);
        this.ema = this.ema + alpha * (value - this.ema);
      }
      this.previousTime = ti;
      this.values[index] = this.ema;
    }
  }

  private appendSma(
    time: readonly number[],
    input: readonly number[],
    start: number,
    windowS: number
  ): void {
    for (let right = start; right < this.values.length; right += 1) {
      this.stats.processedSamples += 1;
      const ti = time[right];
      this.values[right] = Number.NaN;
      if (!isFiniteNumber(ti)) {
        continue;
      }
      if (this.previousTime !== null && ti < this.previousTime) {
        this.rebuild(time, input, "sma", windowS);
        return;
      }
      this.previousTime = ti;
      const value = input[right];
      if (isFiniteNumber(value)) {
        this.smaSum += value;
        this.smaCount += 1;
      }
      while (this.smaLeft <= right) {
        const leftTime = time[this.smaLeft];
        if (!isFiniteNumber(leftTime)) {
          this.smaLeft += 1;
          continue;
        }
        if (ti - leftTime > windowS) {
          const expired = input[this.smaLeft];
          if (isFiniteNumber(expired)) {
            this.smaSum -= expired;
            this.smaCount -= 1;
          }
          this.smaLeft += 1;
          continue;
        }
        break;
      }
      if (this.smaCount > 0) {
        this.values[right] = this.smaSum / this.smaCount;
      }
    }
  }
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
