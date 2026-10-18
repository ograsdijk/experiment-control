import {
  isRemoteProcess,
  processHealth,
  stateName,
  type ResourceAdapter,
  type ResourceHealth,
} from "../devices/health";
import {
  statusHasError,
  type StateMachineProcessRow,
} from "./useStateMachinesController";

/**
 * One verdict per state-machine row, combining the process lifecycle with
 * the machine's own status: an ERROR state or status error is "failed",
 * a stale status is "stale", otherwise the process health applies.
 */
export function stateMachineHealth(row: StateMachineProcessRow): ResourceHealth {
  if (statusHasError(row.process, row.status, row.statusError)) {
    return "failed";
  }
  if (row.stale) {
    return "stale";
  }
  return processHealth(row.process);
}

export function stateMachineErrorText(row: StateMachineProcessRow): string {
  return (
    String(row.statusError ?? "").trim() ||
    String(row.status?.last_error ?? "").trim() ||
    String(row.process.last_error ?? "").trim()
  );
}

export const stateMachineAdapter: ResourceAdapter<StateMachineProcessRow> = {
  id: (row) => row.process.process_id,
  health: stateMachineHealth,
  remoteLink: (row) =>
    isRemoteProcess(row.process) ? stateName(row.process.liveness) || "UNKNOWN" : null,
  searchText: (row) =>
    [
      row.process.process_id,
      row.binding.namespace,
      stateMachineHealth(row),
      stateName(row.status?.state),
      stateName(row.process.state),
      stateMachineErrorText(row),
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase(),
};
