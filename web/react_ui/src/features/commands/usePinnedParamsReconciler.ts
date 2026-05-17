import { useEffect, type Dispatch, type SetStateAction } from "react";

import { sameStringRecord } from "../common/compare";
import { buildParamDefaults } from "../devices/command_schema";
import { pinnedCommandKey } from "../runtime/helpers";
import type { PinnedCommand } from "../../types";
import type { PinnedParamDrafts } from "../profile/types";

/**
 * Reconciles `pinnedParamDrafts` + `pinnedBusyByKey` with the current
 * `pinnedCommands` / `capabilitiesByDevice` set.
 *
 * On every change to either input, this hook:
 *
 * 1. Walks every pinned (device, action) pair and ensures
 *    `pinnedParamDrafts[key]` exists with one entry per declared
 *    param. Pre-existing user-typed values are preserved; missing
 *    fields fall back to `buildParamDefaults(member)`.
 * 2. Drops drafts whose key no longer corresponds to a pinned command.
 * 3. Same prune for `pinnedBusyByKey`.
 *
 * The setters and the source state are passed in rather than read
 * from context so this hook can move to a dedicated module without
 * pulling in a CommandsContext / DevicesContext dependency.
 */

export interface PinnedParamsReconcilerArgs {
  pinnedCommands: Record<string, PinnedCommand[]>;
  capabilitiesByDevice: Record<string, unknown[]>;
  setPinnedParamDrafts: Dispatch<SetStateAction<PinnedParamDrafts>>;
  setPinnedBusyByKey: Dispatch<SetStateAction<Record<string, boolean>>>;
}

export function usePinnedParamsReconciler({
  pinnedCommands,
  capabilitiesByDevice,
  setPinnedParamDrafts,
  setPinnedBusyByKey,
}: PinnedParamsReconcilerArgs) {
  useEffect(() => {
    setPinnedParamDrafts((prev) => {
      const next: PinnedParamDrafts = { ...prev };
      const validKeys = new Set<string>();
      let changed = false;
      for (const [deviceId, entries] of Object.entries(pinnedCommands)) {
        const capabilities = (capabilitiesByDevice[deviceId] ??
          []) as Array<{ name: string; params?: Array<{ name: string }> }>;
        for (const entry of entries) {
          const key = pinnedCommandKey(deviceId, entry.action);
          validKeys.add(key);
          const member = capabilities.find(
            (capability) => capability.name === entry.action
          );
          const params = member?.params ?? [];
          if (params.length === 0) {
            const current = next[key] ?? {};
            if (Object.keys(current).length > 0) {
              next[key] = {};
              changed = true;
            } else if (!(key in next)) {
              next[key] = {};
              changed = true;
            }
            continue;
          }
          const defaults = buildParamDefaults(member);
          const current = next[key] ?? {};
          const merged: Record<string, string> = {};
          for (const param of params) {
            const paramName = param.name;
            if (typeof current[paramName] === "string") {
              merged[paramName] = current[paramName];
            } else {
              merged[paramName] = defaults[paramName] ?? "";
            }
          }
          if (!sameStringRecord(current, merged)) {
            next[key] = merged;
            changed = true;
          } else if (!(key in next)) {
            next[key] = merged;
            changed = true;
          }
        }
      }
      for (const key of Object.keys(next)) {
        if (!validKeys.has(key)) {
          delete next[key];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
    setPinnedBusyByKey((prev) => {
      const next = { ...prev };
      const valid = new Set<string>();
      for (const [deviceId, entries] of Object.entries(pinnedCommands)) {
        for (const entry of entries) {
          valid.add(pinnedCommandKey(deviceId, entry.action));
        }
      }
      let changed = false;
      for (const key of Object.keys(next)) {
        if (!valid.has(key)) {
          delete next[key];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [pinnedCommands, capabilitiesByDevice]);
}
