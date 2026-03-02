import type {
  StreamAnalysisMessage,
  StreamFrameMessage,
  TelemetrySignal,
} from "../../types";
import type {
  StreamFitParamsMap,
  StreamBin2dSnapshot,
  StreamBinStatsSnapshot,
  StreamFitCurveSnapshot,
} from "./types";

export function normalizeTime(signal: TelemetrySignal, fallback?: number) {
  return signal.ts?.t_wall ?? fallback ?? Date.now() / 1000;
}

export function normalizeStreamFrameMessage(msg: StreamFrameMessage): {
  deviceId: string;
  stream: string;
  seq: number;
  shape: number[];
  values: unknown;
} | null {
  if (msg.topic !== "manager.stream_frame") {
    return null;
  }
  const payload = msg.payload;
  const deviceId = String(payload?.device_id ?? "").trim();
  const stream = String(payload?.stream ?? "").trim();
  if (!deviceId || !stream) {
    return null;
  }
  const seqRaw = payload?.seq;
  const seq =
    typeof seqRaw === "number" && Number.isFinite(seqRaw) ? Math.trunc(seqRaw) : -1;
  if (seq < 0) {
    return null;
  }
  const shapeRaw = Array.isArray(payload?.shape) ? payload.shape : [];
  const shape = shapeRaw
    .map((v) => Number(v))
    .filter((v) => Number.isFinite(v) && v > 0)
    .map((v) => Math.trunc(v));
  return {
    deviceId,
    stream,
    seq,
    shape,
    values: payload?.values,
  };
}

export type NormalizedStreamAnalysisOutput = {
  workspaceId: string;
  outputId: string;
  kind: string;
  seq: number | null;
  value: unknown;
  tWallS: number;
  contextFields: Record<string, unknown> | null;
};

export function normalizeStreamAnalysisOutputMessage(
  msg: StreamAnalysisMessage
): NormalizedStreamAnalysisOutput | null {
  if (msg.topic !== "manager.stream_analysis.output") {
    return null;
  }
  const payload = msg.payload;
  const workspaceId = String(payload?.workspace_id ?? "").trim();
  const outputId = String(payload?.output_id ?? "").trim();
  const kind = String(payload?.kind ?? "").trim();
  if (!workspaceId || !outputId || !kind) {
    return null;
  }
  const t0WallNsRaw = payload?.t0_wall_ns;
  const tWallS =
    typeof t0WallNsRaw === "number" && Number.isFinite(t0WallNsRaw)
      ? t0WallNsRaw / 1e9
      : Date.now() / 1000;
  const contextFieldsRaw = payload?.context_fields;
  const contextFields =
    contextFieldsRaw && typeof contextFieldsRaw === "object"
      ? (contextFieldsRaw as Record<string, unknown>)
      : null;
  const seqRaw = payload?.seq;
  const seq =
    typeof seqRaw === "number" && Number.isFinite(seqRaw)
      ? Math.trunc(seqRaw)
      : null;
  return {
    workspaceId,
    outputId,
    kind,
    seq,
    value: payload?.value,
    tWallS,
    contextFields,
  };
}

export function normalizeTraceValues(raw: unknown): number[] | null {
  if (!Array.isArray(raw)) {
    return null;
  }
  const out: number[] = [];
  for (const item of raw) {
    const value = Number(item);
    if (!Number.isFinite(value)) {
      return null;
    }
    out.push(value);
  }
  return out;
}

export function normalizeFitParamsMapValue(raw: unknown): StreamFitParamsMap | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  const out: StreamFitParamsMap = {};
  for (const [nameRaw, entryRaw] of Object.entries(obj)) {
    const name = String(nameRaw ?? "").trim();
    if (!name) {
      continue;
    }
    if (typeof entryRaw === "number" && Number.isFinite(entryRaw)) {
      out[name] = { value: Number(entryRaw), stderr: null };
      continue;
    }
    if (!entryRaw || typeof entryRaw !== "object" || Array.isArray(entryRaw)) {
      continue;
    }
    const entry = entryRaw as Record<string, unknown>;
    const valueRaw = Number(entry.value);
    const stderrRaw = Number(entry.stderr);
    const value = Number.isFinite(valueRaw) ? valueRaw : null;
    const stderr = Number.isFinite(stderrRaw) ? stderrRaw : null;
    if (value === null && stderr === null) {
      continue;
    }
    out[name] = { value, stderr };
  }
  return Object.keys(out).length > 0 ? out : null;
}

export function normalizeHistAggValue(raw: unknown): StreamBinStatsSnapshot | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const payload = raw as Record<string, unknown>;
  const xBinsRaw = Array.isArray(payload.x_bins) ? payload.x_bins : [];
  const meanRaw = Array.isArray(payload.mean) ? payload.mean : [];
  const stdRaw = Array.isArray(payload.std) ? payload.std : [];
  const semRaw = Array.isArray(payload.sem) ? payload.sem : [];
  const countRaw = Array.isArray(payload.count) ? payload.count : [];
  const xBins = xBinsRaw.map((v) => Number(v));
  const mean = meanRaw.map((v) => Number(v));
  const std = stdRaw.map((v) => Number(v));
  const sem = semRaw.map((v) => Number(v));
  const count = countRaw.map((v) => Number(v));
  const n = Math.min(xBins.length, mean.length, std.length, sem.length, count.length);
  if (!Number.isFinite(n) || n < 0) {
    return null;
  }
  const activeBinCountRaw = Number(payload.active_bin_count);
  const populatedBinCountRaw = Number(payload.populated_bin_count);
  const maxBinCountRaw = Number(payload.max_bin_count ?? payload.bin_count);
  const xMinRaw = Number(payload.x_min);
  const xMaxRaw = Number(payload.x_max);
  return {
    series: {
      xBins: xBins.slice(0, n),
      mean: mean.slice(0, n),
      std: std.slice(0, n),
      sem: sem.slice(0, n),
      count: count.slice(0, n),
    },
    activeBinCount:
      Number.isFinite(activeBinCountRaw) && activeBinCountRaw >= 0
        ? Math.trunc(activeBinCountRaw)
        : null,
    populatedBinCount:
      Number.isFinite(populatedBinCountRaw) && populatedBinCountRaw >= 0
        ? Math.trunc(populatedBinCountRaw)
        : null,
    maxBinCount:
      Number.isFinite(maxBinCountRaw) && maxBinCountRaw >= 0
        ? Math.trunc(maxBinCountRaw)
        : null,
    xMin: Number.isFinite(xMinRaw) ? xMinRaw : null,
    xMax: Number.isFinite(xMaxRaw) ? xMaxRaw : null,
    autoRange:
      typeof payload.auto_range === "boolean"
        ? payload.auto_range
        : null,
  };
}

export function normalizeFitCurveValue(raw: unknown): StreamFitCurveSnapshot | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const payload = raw as Record<string, unknown>;
  const x = Array.isArray(payload.x) ? payload.x.map((v) => Number(v)) : [];
  const yhat = Array.isArray(payload.yhat) ? payload.yhat.map((v) => Number(v)) : [];
  const n = Math.min(x.length, yhat.length);
  if (!Number.isFinite(n) || n <= 0) {
    return null;
  }
  const xDenseRaw = Array.isArray(payload.x_dense)
    ? payload.x_dense.map((v) => Number(v))
    : [];
  const yhatDenseRaw = Array.isArray(payload.yhat_dense)
    ? payload.yhat_dense.map((v) => Number(v))
    : [];
  const nDense = Math.min(xDenseRaw.length, yhatDenseRaw.length);
  return {
    x: x.slice(0, n),
    yhat: yhat.slice(0, n),
    xDense: nDense > 0 ? xDenseRaw.slice(0, nDense) : null,
    yhatDense: nDense > 0 ? yhatDenseRaw.slice(0, nDense) : null,
  };
}

export function normalizeHist2dGrid(raw: unknown): number[][] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const out: number[][] = [];
  for (const rowRaw of raw) {
    if (!Array.isArray(rowRaw)) {
      return [];
    }
    const row: number[] = [];
    for (const cell of rowRaw) {
      if (cell === null || cell === undefined) {
        row.push(Number.NaN);
        continue;
      }
      const value = Number(cell);
      row.push(Number.isFinite(value) ? value : Number.NaN);
    }
    out.push(row);
  }
  return out;
}

export function normalizeHist2dValue(raw: unknown): StreamBin2dSnapshot | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const payload = raw as Record<string, unknown>;
  const xBins = Array.isArray(payload.x_bins) ? payload.x_bins.map((v) => Number(v)) : [];
  const yBins = Array.isArray(payload.y_bins) ? payload.y_bins.map((v) => Number(v)) : [];
  const count = normalizeHist2dGrid(payload.count);
  const sum = normalizeHist2dGrid(payload.sum);
  const mean = normalizeHist2dGrid(payload.mean);
  const std = normalizeHist2dGrid(payload.std);
  const sem = normalizeHist2dGrid(payload.sem);
  const min = normalizeHist2dGrid(payload.min);
  const max = normalizeHist2dGrid(payload.max);
  const nx = Math.min(
    xBins.length,
    count.length,
    sum.length,
    mean.length,
    std.length,
    sem.length,
    min.length,
    max.length
  );
  if (!Number.isFinite(nx) || nx < 0) {
    return null;
  }
  let ny = yBins.length;
  for (let xi = 0; xi < nx; xi += 1) {
    ny = Math.min(
      ny,
      count[xi]?.length ?? 0,
      sum[xi]?.length ?? 0,
      mean[xi]?.length ?? 0,
      std[xi]?.length ?? 0,
      sem[xi]?.length ?? 0,
      min[xi]?.length ?? 0,
      max[xi]?.length ?? 0
    );
  }
  const trim = (grid: number[][]): number[][] =>
    grid.slice(0, nx).map((row) => row.slice(0, ny));

  const xActiveRaw = Number(payload.x_active_bin_count);
  const yActiveRaw = Number(payload.y_active_bin_count);
  const xMaxRaw = Number(payload.x_max_bin_count ?? payload.x_bin_count);
  const yMaxRaw = Number(payload.y_max_bin_count ?? payload.y_bin_count);
  const populatedRaw = Number(payload.populated_bin_count);
  const xMinRaw = Number(payload.x_min);
  const xMaxRangeRaw = Number(payload.x_max);
  const yMinRaw = Number(payload.y_min);
  const yMaxRangeRaw = Number(payload.y_max);
  const droppedRaw = Number(payload.dropped_samples);

  return {
    series: {
      xBins: xBins.slice(0, nx),
      yBins: yBins.slice(0, ny),
      count: trim(count),
      sum: trim(sum),
      mean: trim(mean),
      std: trim(std),
      sem: trim(sem),
      min: trim(min),
      max: trim(max),
    },
    xActiveBinCount: Number.isFinite(xActiveRaw) && xActiveRaw >= 0 ? Math.trunc(xActiveRaw) : null,
    yActiveBinCount: Number.isFinite(yActiveRaw) && yActiveRaw >= 0 ? Math.trunc(yActiveRaw) : null,
    xMaxBinCount: Number.isFinite(xMaxRaw) && xMaxRaw >= 0 ? Math.trunc(xMaxRaw) : null,
    yMaxBinCount: Number.isFinite(yMaxRaw) && yMaxRaw >= 0 ? Math.trunc(yMaxRaw) : null,
    populatedBinCount:
      Number.isFinite(populatedRaw) && populatedRaw >= 0 ? Math.trunc(populatedRaw) : null,
    xMin: Number.isFinite(xMinRaw) ? xMinRaw : null,
    xMax: Number.isFinite(xMaxRangeRaw) ? xMaxRangeRaw : null,
    yMin: Number.isFinite(yMinRaw) ? yMinRaw : null,
    yMax: Number.isFinite(yMaxRangeRaw) ? yMaxRangeRaw : null,
    xAutoRange: typeof payload.x_auto_range === "boolean" ? payload.x_auto_range : null,
    yAutoRange: typeof payload.y_auto_range === "boolean" ? payload.y_auto_range : null,
    droppedSamples:
      Number.isFinite(droppedRaw) && droppedRaw >= 0 ? Math.trunc(droppedRaw) : null,
  };
}
