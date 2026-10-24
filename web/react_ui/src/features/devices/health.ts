import type { DeviceStatus, ProcessStatus } from "../../types";
import { AGE_BUCKET_BOUNDS_S } from "../polling/pollEquality";

/**
 * Derived resource health, mirroring the TUI navigator
 * (`_tui/app.py: ManagerTUI._device_health` / `_process_health` /
 * `_resource_has_issue`). Keep the two in sync so operators see the same
 * verdict in both UIs.
 */
export type ResourceHealth =
  | "healthy"
  | "degraded"
  | "stale"
  | "failed"
  | "neutral"
  | "transition"
  | "unknown";

export type ResourceSortMode = "custom" | "name" | "health";

type DriverProcessInfo = {
  state?: unknown;
  pid?: unknown;
  restart_count?: unknown;
  last_exit_code?: unknown;
  last_error?: unknown;
};

export function stateName(value: unknown): string {
  const text = String(value ?? "").toUpperCase();
  const dot = text.lastIndexOf(".");
  return dot >= 0 ? text.slice(dot + 1) : text;
}

/**
 * How a list view reads a resource. Devices and processes each provide one;
 * filtering, sorting and the summary strip are written once against it.
 */
export type ResourceAdapter<T> = {
  id: (item: T) => string;
  health: (item: T) => ResourceHealth;
  /** Remote link state (federated resources only), e.g. ONLINE/OFFLINE/STALE. */
  remoteLink: (item: T) => string | null;
  searchText: (item: T) => string;
};

export function resourceHasIssue<T>(item: T, adapter: ResourceAdapter<T>): boolean {
  const health = adapter.health(item);
  if (health === "failed" || health === "stale" || health === "degraded") {
    return true;
  }
  const link = adapter.remoteLink(item);
  return link === "OFFLINE" || link === "STALE";
}

function resourceIsFailedIssue<T>(item: T, adapter: ResourceAdapter<T>): boolean {
  return adapter.health(item) === "failed" || adapter.remoteLink(item) === "OFFLINE";
}

export function resourceMatchesQuery<T>(
  item: T,
  adapter: ResourceAdapter<T>,
  query: string
): boolean {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) {
    return true;
  }
  const haystack = adapter.searchText(item);
  return terms.every((term) => haystack.includes(term));
}

export type ResourceHealthSummary = {
  total: number;
  healthy: number;
  issues: number;
  failed: number;
  remote: number;
};

export function summarizeResources<T>(
  items: readonly T[],
  adapter: ResourceAdapter<T>
): ResourceHealthSummary {
  let healthy = 0;
  let issues = 0;
  let failed = 0;
  let remote = 0;
  for (const item of items) {
    if (adapter.health(item) === "healthy") {
      healthy += 1;
    }
    if (resourceHasIssue(item, adapter)) {
      issues += 1;
      if (resourceIsFailedIssue(item, adapter)) {
        failed += 1;
      }
    }
    if (adapter.remoteLink(item) !== null) {
      remote += 1;
    }
  }
  return { total: items.length, healthy, issues, failed, remote };
}

const HEALTH_RANK: Record<ResourceHealth, number> = {
  failed: 0,
  stale: 1,
  degraded: 2,
  transition: 3,
  unknown: 4,
  neutral: 5,
  healthy: 6,
};

export function resourceHealthRank<T>(item: T, adapter: ResourceAdapter<T>): number {
  // Remote OFFLINE/STALE resources are issues even when their own health is
  // neutral; rank them with the stale group so they surface near the top.
  const rank = HEALTH_RANK[adapter.health(item)];
  return resourceHasIssue(item, adapter) ? Math.min(rank, HEALTH_RANK.stale) : rank;
}

export type ResourceViewOptions = {
  query: string;
  issuesOnly: boolean;
  sort: ResourceSortMode;
};

/** Filter + sort; `custom` keeps the caller's order. */
export function selectVisibleResources<T>(
  items: readonly T[],
  adapter: ResourceAdapter<T>,
  { query, issuesOnly, sort }: ResourceViewOptions
): T[] {
  const filtered = items.filter(
    (item) =>
      (!issuesOnly || resourceHasIssue(item, adapter)) &&
      resourceMatchesQuery(item, adapter, query)
  );
  if (sort === "custom") {
    return filtered;
  }
  const byName = (a: T, b: T) =>
    adapter.id(a).localeCompare(adapter.id(b), undefined, { sensitivity: "base" });
  if (sort === "name") {
    return [...filtered].sort(byName);
  }
  return [...filtered].sort(
    (a, b) =>
      resourceHealthRank(a, adapter) - resourceHealthRank(b, adapter) || byName(a, b)
  );
}

export function isResourceViewFiltered({ query, issuesOnly, sort }: ResourceViewOptions) {
  return query.trim().length > 0 || issuesOnly || sort !== "custom";
}

// ---------------------------------------------------------------------------
// Devices
// ---------------------------------------------------------------------------

export function deviceDriverProcess(device: DeviceStatus): DriverProcessInfo {
  const proc = (device as DeviceStatus & { driver_process?: DriverProcessInfo | null })
    .driver_process;
  return proc ?? {};
}

export function deviceHealth(device: DeviceStatus): ResourceHealth {
  const processState = stateName(deviceDriverProcess(device).state);
  const driverState = stateName(device.driver_state);
  const deviceState = stateName(device.device_state);
  const liveness = stateName(device.liveness);
  if (processState === "FAILED" || processState === "CRASHLOOP") {
    return "failed";
  }
  if (deviceState === "FAULT" || driverState === "FAULT") {
    return "failed";
  }
  if (liveness === "STALE") {
    return "stale";
  }
  if (deviceState === "DEGRADED" || driverState === "DEGRADED") {
    return "degraded";
  }
  if (processState === "STARTING" || processState === "STOPPING") {
    return "transition";
  }
  if (liveness === "ONLINE" && deviceState === "OK") {
    return "healthy";
  }
  if (deviceState === "DISCONNECTED" && liveness === "DISCONNECTED") {
    return "neutral";
  }
  if (processState === "STOPPED" || processState === "EXITED" || liveness === "OFFLINE") {
    return "neutral";
  }
  return "unknown";
}

/** Most relevant error text: heartbeat error first, then the driver process. */
export function deviceErrorText(device: DeviceStatus): string {
  const hbError = String(device.last_error ?? "").trim();
  if (hbError) {
    return hbError;
  }
  return String(deviceDriverProcess(device).last_error ?? "").trim();
}

export function deviceSearchText(device: DeviceStatus): string {
  return [
    device.device_id,
    deviceHealth(device),
    stateName(device.liveness),
    stateName(device.device_state),
    stateName(device.driver_state),
    stateName(deviceDriverProcess(device).state),
    device.is_remote ? "remote" : "",
    device.owner_peer_id ?? "",
    deviceErrorText(device),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

export const deviceAdapter: ResourceAdapter<DeviceStatus> = {
  id: (device) => device.device_id,
  health: deviceHealth,
  remoteLink: (device) => (device.is_remote ? stateName(device.liveness) : null),
  searchText: deviceSearchText,
};

export const deviceHasIssue = (device: DeviceStatus) =>
  resourceHasIssue(device, deviceAdapter);
export const deviceMatchesQuery = (device: DeviceStatus, query: string) =>
  resourceMatchesQuery(device, deviceAdapter, query);
export const summarizeDeviceHealth = (devices: DeviceStatus[]) =>
  summarizeResources(devices, deviceAdapter);
export const deviceHealthRank = (device: DeviceStatus) =>
  resourceHealthRank(device, deviceAdapter);
export const selectVisibleDevices = (
  devices: DeviceStatus[],
  options: ResourceViewOptions
) => selectVisibleResources(devices, deviceAdapter, options);

// ---------------------------------------------------------------------------
// Processes
// ---------------------------------------------------------------------------

export function isRemoteProcess(process: ProcessStatus): boolean {
  return Boolean(process.is_remote) || stateName(process.source_kind) === "FEDERATED";
}

export function processHealth(process: ProcessStatus): ResourceHealth {
  const state = stateName(process.state);
  if (state === "FAILED" || state === "CRASHLOOP") {
    return "failed";
  }
  if (state === "STARTING" || state === "STOPPING") {
    return "transition";
  }
  if (state === "RUNNING") {
    return "healthy";
  }
  return "neutral";
}

export function processSearchText(process: ProcessStatus): string {
  return [
    process.process_id,
    processHealth(process),
    stateName(process.state),
    isRemoteProcess(process) ? "remote" : "",
    stateName(process.liveness),
    process.owner_peer_id ?? "",
    process.restart_policy ?? "",
    process.last_error ?? "",
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

export const processAdapter: ResourceAdapter<ProcessStatus> = {
  id: (process) => process.process_id,
  health: processHealth,
  remoteLink: (process) =>
    isRemoteProcess(process) ? stateName(process.liveness) || "UNKNOWN" : null,
  searchText: processSearchText,
};

// ---------------------------------------------------------------------------
// Presentation helpers
// ---------------------------------------------------------------------------

export function formatAge(ageS: number | null | undefined): string {
  if (ageS === null || ageS === undefined || !Number.isFinite(ageS)) {
    return "—";
  }
  if (ageS < 10) {
    return `${ageS.toFixed(1)}s`;
  }
  if (ageS < 60) {
    return `${ageS.toFixed(0)}s`;
  }
  return `${(ageS / 60).toFixed(1)}m`;
}

/**
 * Age label matching the coarse poll buckets (`ageBucket`). Use it where the
 * age comes from a coarse-compared poll, so the label never shows a precise
 * value that is no longer being refreshed.
 */
export function formatCoarseAge(ageS: number | null | undefined): string {
  if (ageS === null || ageS === undefined || !Number.isFinite(ageS)) {
    return "n/a";
  }
  let lower = 0;
  for (const bound of AGE_BUCKET_BOUNDS_S) {
    if (ageS < bound) {
      return lower === 0 ? `<${bound} s` : `${lower}–${bound} s`;
    }
    lower = bound;
  }
  return `${Math.floor(ageS / 60)}+ min`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes <= 0) {
    return "—";
  }
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`;
}
