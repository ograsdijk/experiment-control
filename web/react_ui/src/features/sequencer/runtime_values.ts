/**
 * Values the sequencer controller derives from status on every poll. Kept
 * apart from resolved_values (which needs the yaml package) so the header's
 * eager bundle does not pull in the YAML stack.
 */

import { asRecord } from "./records";

/** Scalars of the runtime env; save_as responses and the like are left out. */
export function scalarEnv(raw: unknown): Record<string, unknown> | null {
  const rec = asRecord(raw);
  if (!rec) {
    return null;
  }
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(rec)) {
    if (
      typeof value === "number" ||
      typeof value === "boolean" ||
      (typeof value === "string" && value.length <= 80)
    ) {
      out[key] = value;
    }
  }
  return out;
}

/** Running overrides apply to the running sequence; sidebar rows wait for the next start. */
export function overridesForDisplay(
  running: boolean,
  activeOverrides: Record<string, unknown> | null,
  sidebarOverrides: Record<string, unknown>
): { overrides: Record<string, unknown>; nextRun: Record<string, unknown> | null } {
  if (!running) {
    return { overrides: sidebarOverrides, nextRun: null };
  }
  const active = activeOverrides ?? {};
  const nextRun: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(sidebarOverrides)) {
    if (JSON.stringify(active[name]) !== JSON.stringify(value)) {
      nextRun[name] = value;
    }
  }
  return {
    overrides: active,
    nextRun: Object.keys(nextRun).length > 0 ? nextRun : null,
  };
}
