import type { DeviceStatus, ProcessStatus, StreamCatalogEntry } from "../../types";

function sameOptionalArray<T>(left: readonly T[] | null | undefined, right: readonly T[] | null | undefined) {
  if (left === right) return true;
  if (!left || !right || left.length !== right.length) return false;
  return left.every((value, index) => value === right[index]);
}

function sameOrdered<T>(
  left: readonly T[],
  right: readonly T[],
  equalItem: (a: T, b: T) => boolean
): boolean {
  return (
    left === right ||
    (left.length === right.length && left.every((value, index) => equalItem(value, right[index])))
  );
}

/**
 * Upper bounds (seconds) of the coarse age buckets. Heartbeat/telemetry ages
 * change on every poll, so exact comparison made every poll a new devices /
 * processes array and re-rendered App and every DeviceCard. Coarse mode only
 * reports an age change when it crosses one of these bounds (then per
 * minute). Liveness/state fields are still compared exactly, so the
 * backend's STALE/DISCONNECTED verdicts propagate on the next poll.
 */
export const AGE_BUCKET_BOUNDS_S = [2, 5, 10, 30, 60] as const;

/** Bucket index for an age in seconds; -1 when missing/non-finite. */
export function ageBucket(ageS: number | null | undefined): number {
  if (typeof ageS !== "number" || !Number.isFinite(ageS)) {
    return -1;
  }
  const index = AGE_BUCKET_BOUNDS_S.findIndex((bound) => ageS < bound);
  return index >= 0
    ? index
    : AGE_BUCKET_BOUNDS_S.length + Math.floor(ageS / 60) - 1;
}

const RSS_BUCKET_BYTES = 8 * 1024 * 1024;

/** Resident-set size rounded to 8 MiB; -1 when missing/non-finite. */
export function rssBucket(bytes: number | null | undefined): number {
  if (typeof bytes !== "number" || !Number.isFinite(bytes)) {
    return -1;
  }
  return Math.round(bytes / RSS_BUCKET_BYTES);
}

/**
 * "exact" compares every field (detail views that display live ages);
 * "coarse" compares ages/RSS by bucket (the always-on dashboard polls).
 */
export type StatusPrecision = "exact" | "coarse";

function sameAge(
  left: number | null | undefined,
  right: number | null | undefined,
  precision: StatusPrecision
): boolean {
  return precision === "exact"
    ? left === right
    : ageBucket(left) === ageBucket(right);
}

export function sameDeviceStatuses(
  left: DeviceStatus[],
  right: DeviceStatus[],
  precision: StatusPrecision = "exact"
): boolean {
  return sameOrdered(left, right, (a, b) =>
    a.device_id === b.device_id &&
    a.liveness === b.liveness &&
    a.is_remote === b.is_remote &&
    a.source_kind === b.source_kind &&
    a.owner_peer_id === b.owner_peer_id &&
    a.remote_device_id === b.remote_device_id &&
    a.registered === b.registered &&
    sameAge(a.hb_age_s, b.hb_age_s, precision) &&
    sameAge(a.telemetry_age_s, b.telemetry_age_s, precision) &&
    a.driver_state === b.driver_state &&
    a.device_state === b.device_state &&
    a.device_reachable === b.device_reachable &&
    a.last_error === b.last_error
  );
}

export function sameProcessStatuses(
  left: ProcessStatus[],
  right: ProcessStatus[],
  precision: StatusPrecision = "exact"
): boolean {
  return sameOrdered(left, right, (a, b) =>
    a.process_id === b.process_id &&
    a.state === b.state &&
    sameOptionalArray(a.argv, b.argv) &&
    a.pid === b.pid &&
    (precision === "exact"
      ? a.rss_bytes === b.rss_bytes
      : rssBucket(a.rss_bytes) === rssBucket(b.rss_bytes)) &&
    sameAge(a.hb_age_s, b.hb_age_s, precision) &&
    a.last_error === b.last_error &&
    a.restart_policy === b.restart_policy &&
    a.restart_count === b.restart_count &&
    a.last_exit_code === b.last_exit_code &&
    a.rpc_endpoint === b.rpc_endpoint &&
    a.registered === b.registered &&
    a.is_remote === b.is_remote &&
    a.source_kind === b.source_kind &&
    a.owner_peer_id === b.owner_peer_id &&
    a.remote_process_id === b.remote_process_id &&
    a.liveness === b.liveness
  );
}

export function sameDeviceStatusesCoarse(
  left: DeviceStatus[],
  right: DeviceStatus[]
): boolean {
  return sameDeviceStatuses(left, right, "coarse");
}

export function sameProcessStatusesCoarse(
  left: ProcessStatus[],
  right: ProcessStatus[]
): boolean {
  return sameProcessStatuses(left, right, "coarse");
}

export function sameStreamCatalog(
  left: StreamCatalogEntry[],
  right: StreamCatalogEntry[]
): boolean {
  return sameOrdered(left, right, (a, b) =>
    a.device_id === b.device_id &&
    a.stream === b.stream &&
    a.dtype === b.dtype &&
    sameOptionalArray(a.shape, b.shape) &&
    a.units === b.units &&
    a.description === b.description &&
    a.x_units === b.x_units &&
    a.x_label === b.x_label &&
    a.x_increment === b.x_increment &&
    a.x_origin === b.x_origin &&
    a.x_axis_source === b.x_axis_source
  );
}
