// Option lists for the sequencer step editors' device/action/field selectors.
// These feed free-text Autocompletes, so they are suggestions only — any typed
// value is still allowed (offline/federated devices, ${template} names).
import type { CapabilityMember, DeviceStatus } from "../../types";

function sortedUnique(names: ReadonlyArray<string>): string[] {
  return Array.from(
    new Set(names.filter((name) => typeof name === "string" && name.trim().length > 0))
  ).sort((a, b) => a.localeCompare(b));
}

/** All known device ids (includes remote/federated devices). */
export function deviceNames(devices: ReadonlyArray<DeviceStatus>): string[] {
  return sortedUnique(devices.map((device) => device.device_id));
}

/**
 * Callable actions for a `call` step: methods (discovery exposes `stream__*`
 * stream calls as methods too). Read-only attributes/properties are excluded —
 * setting a property uses the `set` step.
 */
export function callableActionNames(
  members: ReadonlyArray<CapabilityMember> | null | undefined
): string[] {
  if (!members) {
    return [];
  }
  return sortedUnique(
    members
      .filter((member) => member.kind === "method" || member.name.startsWith("stream__"))
      .map((member) => member.name)
  );
}

/** Settable members for a `set` step's field name. */
export function settableMemberNames(
  members: ReadonlyArray<CapabilityMember> | null | undefined
): string[] {
  if (!members) {
    return [];
  }
  return sortedUnique(
    members.filter((member) => member.settable === true).map((member) => member.name)
  );
}
