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

export function sameDeviceStatuses(left: DeviceStatus[], right: DeviceStatus[]): boolean {
  return sameOrdered(left, right, (a, b) =>
    a.device_id === b.device_id &&
    a.liveness === b.liveness &&
    a.is_remote === b.is_remote &&
    a.source_kind === b.source_kind &&
    a.owner_peer_id === b.owner_peer_id &&
    a.remote_device_id === b.remote_device_id &&
    a.registered === b.registered &&
    a.hb_age_s === b.hb_age_s &&
    a.telemetry_age_s === b.telemetry_age_s &&
    a.driver_state === b.driver_state &&
    a.device_state === b.device_state &&
    a.device_reachable === b.device_reachable &&
    a.last_error === b.last_error
  );
}

export function sameProcessStatuses(left: ProcessStatus[], right: ProcessStatus[]): boolean {
  return sameOrdered(left, right, (a, b) =>
    a.process_id === b.process_id &&
    a.state === b.state &&
    sameOptionalArray(a.argv, b.argv) &&
    a.pid === b.pid &&
    a.rss_bytes === b.rss_bytes &&
    a.hb_age_s === b.hb_age_s &&
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
    a.description === b.description
  );
}
