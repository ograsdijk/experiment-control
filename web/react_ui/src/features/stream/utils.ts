import type { Bin2dReducer } from "../../components/StreamBin2dPanel";
import type { TraceKey } from "../../types";
import type {
  StreamTraceAverageMode,
  StreamTraceDecimator,
  YScaleMode,
} from "./types";

export const DEFAULT_STREAM_CONTEXT_FIELD = "freq_hz";
export const DEFAULT_BIN_COUNT = 30;
export const DEFAULT_BIN_X_MIN = -15_000_000;
export const DEFAULT_BIN_X_MAX = 15_000_000;
export const DEFAULT_UNCERTAINTY_SCALE = 1;
export const DEFAULT_INTEGRAL_OUTPUT_ID = "integral";
export const DEFAULT_BIN_OUTPUT_ID = "bin_stats";
export const DEFAULT_BIN2D_OUTPUT_ID = "bin2d_stats";
export const DEFAULT_TRACE_DECIMATOR: StreamTraceDecimator = "minmax";
export const DEFAULT_TRACE_MAX_POINTS = 1200;
export const DEFAULT_TRACE_MAX_FPS = 10;
export const DEFAULT_TRACE_ROLLING_WINDOW = 1;
export const DEFAULT_TRACE_AVERAGE_MODE: StreamTraceAverageMode = "block";
export const DEFAULT_WATERFALL_ROWS = 120;
export const DEFAULT_STREAM_OVERLAY_COUNT = 1;
export const DEFAULT_BIN2D_REDUCER: Bin2dReducer = "mean";

export function traceKeyId(trace: TraceKey) {
  return `${trace.deviceId}:${trace.signal}`;
}

export function streamTargetKey(deviceId: string, stream: string) {
  return `${deviceId}|${stream}`;
}

export function normalizeShape(raw: unknown): number[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value) && value > 0)
    .map((value) => Math.trunc(value));
}

export function inferChannelCountFromShape(
  shape: number[] | null | undefined
): number {
  if (!shape || shape.length <= 1) {
    return 1;
  }
  if (shape.length === 2) {
    const a = Math.max(1, Math.trunc(shape[0]));
    const b = Math.max(1, Math.trunc(shape[1]));
    return Math.max(1, Math.min(a, b));
  }
  return 1;
}

export function normalizeYScaleMode(value: unknown): YScaleMode {
  return value === "manual" ? "manual" : "auto";
}

export function normalizeYBound(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  return value;
}

export function parseNumberInput(value: string | number): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim().length > 0) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return null;
}

export function normalizeTraceDecimator(value: unknown): StreamTraceDecimator {
  const raw = String(value ?? "").trim().toLowerCase();
  if (raw === "stride" || raw === "mean" || raw === "m4") {
    return raw;
  }
  return "minmax";
}

export function normalizeTraceMaxPoints(value: unknown): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return DEFAULT_TRACE_MAX_POINTS;
  }
  return Math.max(32, Math.min(20000, Math.trunc(parsed)));
}

export function normalizeTraceMaxFps(value: unknown): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return DEFAULT_TRACE_MAX_FPS;
  }
  return Math.max(0.5, Math.min(120, parsed));
}

export function normalizeTraceRollingWindow(value: unknown): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return DEFAULT_TRACE_ROLLING_WINDOW;
  }
  return Math.max(1, Math.min(200, Math.trunc(parsed)));
}

export function normalizeTraceAverageMode(
  value: unknown
): StreamTraceAverageMode {
  const raw = String(value ?? "").trim().toLowerCase();
  if (raw === "rolling") {
    return "rolling";
  }
  return "block";
}

export function dagOutputKindColor(kind: string | null | undefined): string {
  if (kind === "trace") {
    return "blue";
  }
  if (kind === "scalar") {
    return "green";
  }
  if (kind === "hist_agg") {
    return "orange";
  }
  if (kind === "hist2d") {
    return "grape";
  }
  if (kind === "fit_1d") {
    return "violet";
  }
  if (kind === "params_map") {
    return "cyan";
  }
  return "gray";
}

export function normalizeNonNegativeInt(value: unknown, fallback: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return Math.max(0, Math.trunc(fallback));
  }
  return Math.max(0, Math.trunc(parsed));
}

export function normalizePositiveInt(value: unknown, fallback: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return Math.max(1, Math.trunc(fallback));
  }
  return Math.max(1, Math.trunc(parsed));
}
