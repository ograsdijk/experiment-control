export type PerfSnapshot = {
  enabled: boolean;
  elapsedSeconds: number;
  counters: Record<string, number>;
  rates: Record<string, number>;
  timings: Record<string, { count: number; totalMs: number; meanMs: number; maxMs: number }>;
};

type Timing = { count: number; totalMs: number; maxMs: number };

const enabled =
  typeof window !== "undefined" &&
  new URLSearchParams(window.location.search).get("perf") === "1";
const counters = new Map<string, number>();
const timings = new Map<string, Timing>();
let startedAt = enabled ? performance.now() : 0;

export function perfEnabled(): boolean {
  return enabled;
}

export function perfCount(name: string, amount = 1): void {
  if (!enabled) return;
  counters.set(name, (counters.get(name) ?? 0) + amount);
}

/** Records a current-value counter without allocating when instrumentation is off. */
export function perfSet(name: string, value: number): void {
  if (!enabled) return;
  counters.set(name, value);
}

export function perfCountScoped(
  name: string,
  scope: string | undefined,
  amount = 1,
  suffix = ""
): void {
  if (!enabled || !scope) return;
  perfCount(`${name}.${scope}${suffix}`, amount);
}

export function perfCountEndpoint(name: string, path: string): void {
  if (!enabled) return;
  perfCount(`${name}.${path.split("?")[0]}`);
}

export function perfMeasure<T>(name: string, operation: () => T): T {
  if (!enabled) return operation();
  const start = performance.now();
  try {
    return operation();
  } finally {
    const elapsed = performance.now() - start;
    const current = timings.get(name) ?? { count: 0, totalMs: 0, maxMs: 0 };
    current.count += 1;
    current.totalMs += elapsed;
    current.maxMs = Math.max(current.maxMs, elapsed);
    timings.set(name, current);
  }
}

export function resetPerf(): void {
  if (!enabled) return;
  counters.clear();
  timings.clear();
  startedAt = performance.now();
}

export function snapshotPerf(): PerfSnapshot {
  const elapsedSeconds = enabled
    ? Math.max(0.001, (performance.now() - startedAt) / 1000)
    : 0;
  const counterObject = Object.fromEntries(counters);
  const rateNames = [
    "telemetry.messages",
    "telemetry.signal_values",
    "raw_stream.frames",
    "stream_analysis.messages",
  ];
  const rates = Object.fromEntries(
    rateNames.map((name) => [`${name}_per_second`, (counters.get(name) ?? 0) / elapsedSeconds])
  );
  const timingObject = Object.fromEntries(
    [...timings].map(([name, timing]) => [
      name,
      {
        ...timing,
        meanMs: timing.count > 0 ? timing.totalMs / timing.count : 0,
      },
    ])
  );
  return { enabled, elapsedSeconds, counters: counterObject, rates, timings: timingObject };
}

if (enabled && typeof window !== "undefined") {
  const api = {
    reset: resetPerf,
    snapshot: snapshotPerf,
    table: () => {
      const snapshot = snapshotPerf();
      console.table({ ...snapshot.counters, ...snapshot.rates });
      console.table(snapshot.timings);
      return snapshot;
    },
  };
  Object.defineProperty(window, "__EC_PERF__", { value: api, configurable: true });
  console.info("experiment-control performance counters enabled: window.__EC_PERF__.table()");
}

declare global {
  interface Window {
    __EC_PERF__?: {
      reset: () => void;
      snapshot: () => PerfSnapshot;
      table: () => PerfSnapshot;
    };
  }
}
