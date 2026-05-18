import type {
  CommandDeckCommandEntry,
  CommandDeckEntry,
  CommandDeckTelemetryEntry,
} from "../../types";
import { useCommands } from "./CommandsContext";
import {
  isCommandDeckCommandEntry,
  isCommandDeckTelemetryEntry,
  normalizeDeckGroup,
} from "./utils";

/**
 * Command-deck pure state mutators.
 *
 * Seven handlers that only touch the deck array in `CommandsContext` —
 * no API calls, no app-level state. The runner + the add-from-modal
 * + the create helpers stay in App.tsx because they need command-modal
 * draft state and `runCommandDeckEntry` couples into processes,
 * device capabilities, HDF/Influx refresh, etc.
 *
 * **Handlers**:
 *
 * - `updateCommandDeckCommandEntry(entryId, patch)` — patches one
 *   command entry's targetKind/targetId/action/label/group/paramsDraft,
 *   with input-shape coercion (trim, normalise group, default
 *   paramsDraft to {}).
 * - `updateCommandDeckTelemetryEntry(entryId, patch)` — patches one
 *   telemetry entry's deviceId/signal/format/decimals/label/group,
 *   clamping decimals into [0, 12] and constraining format to the
 *   `"auto" | "fixed" | "scientific"` triple.
 * - `removeCommandDeckEntry(entryId)` — drops an entry by id.
 * - `moveCommandDeckEntryWithinGroup(entryId, direction)` — swaps an
 *   entry with the nearest neighbour in the same group (-1 = up,
 *   +1 = down). No-op when at the edge.
 * - `reorderCommandDeckEntryWithinGroup(source, target)` — moves
 *   `source` to `target`'s index (within the same group only).
 * - `setCommandDeckEntryGroup(entryId, nextGroupRaw)` — reassigns an
 *   entry's group and repositions it so it stays adjacent to the
 *   rest of its new group.
 * - `setCommandDeckGroupEntries(fromGroup, toGroupRaw)` — bulk
 *   re-tags every entry currently in `fromGroup` to `toGroupRaw`.
 */
export function useCommandDeckMutations() {
  const { setCommandDeck } = useCommands();

  const updateCommandDeckCommandEntry = (
    entryId: string,
    patch: Partial<
      Pick<
        CommandDeckCommandEntry,
        "targetKind" | "targetId" | "action" | "label" | "group" | "paramsDraft"
      >
    >
  ) => {
    setCommandDeck((prev) =>
      prev.map((entry) => {
        if (entry.id !== entryId || !isCommandDeckCommandEntry(entry)) {
          return entry;
        }
        const nextTargetKind =
          patch.targetKind !== undefined ? patch.targetKind : entry.targetKind;
        const nextTargetId =
          patch.targetId !== undefined
            ? String(patch.targetId).trim()
            : entry.targetId;
        const nextAction =
          patch.action !== undefined
            ? String(patch.action).trim()
            : entry.action;
        const nextLabel =
          patch.label !== undefined
            ? (() => {
                const raw = String(patch.label);
                return raw.trim().length > 0 ? raw : undefined;
              })()
            : entry.label ?? undefined;
        const nextGroup =
          patch.group !== undefined
            ? normalizeDeckGroup(String(patch.group))
            : normalizeDeckGroup(entry.group);
        const nextParamsDraft =
          patch.paramsDraft !== undefined
            ? { ...patch.paramsDraft }
            : { ...(entry.paramsDraft ?? {}) };
        return {
          ...entry,
          targetKind: nextTargetKind,
          targetId: nextTargetId,
          action: nextAction,
          label: nextLabel,
          group: nextGroup,
          paramsDraft: nextParamsDraft,
        };
      })
    );
  };

  const updateCommandDeckTelemetryEntry = (
    entryId: string,
    patch: Partial<
      Pick<
        CommandDeckTelemetryEntry,
        "deviceId" | "signal" | "format" | "decimals" | "label" | "group"
      >
    >
  ) => {
    setCommandDeck((prev) =>
      prev.map((entry) => {
        if (entry.id !== entryId || !isCommandDeckTelemetryEntry(entry)) {
          return entry;
        }
        const nextDeviceId =
          patch.deviceId !== undefined
            ? String(patch.deviceId).trim()
            : entry.deviceId;
        const nextSignal =
          patch.signal !== undefined
            ? String(patch.signal).trim()
            : entry.signal;
        const formatRaw =
          patch.format !== undefined
            ? String(patch.format).trim().toLowerCase()
            : entry.format;
        const nextFormat =
          formatRaw === "fixed" || formatRaw === "scientific"
            ? formatRaw
            : "auto";
        const decimalsCandidate =
          patch.decimals !== undefined ? patch.decimals : entry.decimals;
        const nextDecimals =
          typeof decimalsCandidate === "number" &&
          Number.isFinite(decimalsCandidate)
            ? Math.max(0, Math.min(12, Math.trunc(decimalsCandidate)))
            : null;
        const nextLabel =
          patch.label !== undefined
            ? (() => {
                const raw = String(patch.label);
                return raw.trim().length > 0 ? raw : undefined;
              })()
            : entry.label ?? undefined;
        const nextGroup =
          patch.group !== undefined
            ? normalizeDeckGroup(String(patch.group))
            : normalizeDeckGroup(entry.group);
        return {
          ...entry,
          kind: "telemetry" as const,
          deviceId: nextDeviceId,
          signal: nextSignal,
          format: nextFormat,
          decimals: nextDecimals,
          label: nextLabel,
          group: nextGroup,
        };
      })
    );
  };

  const removeCommandDeckEntry = (entryId: string) => {
    setCommandDeck((prev) => prev.filter((entry) => entry.id !== entryId));
  };

  const moveCommandDeckEntryWithinGroup = (
    entryId: string,
    direction: -1 | 1
  ) => {
    setCommandDeck((prev) => {
      const index = prev.findIndex((entry) => entry.id === entryId);
      if (index < 0) {
        return prev;
      }
      const currentGroup =
        normalizeDeckGroup(prev[index].group) ?? "Ungrouped";
      const step = direction > 0 ? 1 : -1;
      let targetIndex = -1;
      for (let idx = index + step; idx >= 0 && idx < prev.length; idx += step) {
        const group = normalizeDeckGroup(prev[idx].group) ?? "Ungrouped";
        if (group === currentGroup) {
          targetIndex = idx;
          break;
        }
      }
      if (targetIndex < 0) {
        return prev;
      }
      const next = [...prev];
      const current = next[index];
      next[index] = next[targetIndex];
      next[targetIndex] = current;
      return next;
    });
  };

  const reorderCommandDeckEntryWithinGroup = (
    entryId: string,
    targetEntryId: string
  ) => {
    if (!entryId || !targetEntryId || entryId === targetEntryId) {
      return;
    }
    setCommandDeck((prev) => {
      const sourceIndex = prev.findIndex((entry) => entry.id === entryId);
      const targetIndex = prev.findIndex((entry) => entry.id === targetEntryId);
      if (sourceIndex < 0 || targetIndex < 0) {
        return prev;
      }
      const sourceGroup =
        normalizeDeckGroup(prev[sourceIndex].group) ?? "Ungrouped";
      const targetGroup =
        normalizeDeckGroup(prev[targetIndex].group) ?? "Ungrouped";
      if (sourceGroup !== targetGroup) {
        return prev;
      }
      const next = [...prev];
      const [sourceEntry] = next.splice(sourceIndex, 1);
      const insertIndex = Math.max(0, Math.min(next.length, targetIndex));
      next.splice(insertIndex, 0, sourceEntry);
      return next;
    });
  };

  const setCommandDeckEntryGroup = (entryId: string, nextGroupRaw: string) => {
    const nextGroup = normalizeDeckGroup(nextGroupRaw);
    setCommandDeck((prev) => {
      const index = prev.findIndex((entry) => entry.id === entryId);
      if (index < 0) {
        return prev;
      }
      const current = prev[index];
      const currentGroup = normalizeDeckGroup(current.group);
      if (currentGroup === nextGroup) {
        return prev;
      }
      const remaining = [...prev.slice(0, index), ...prev.slice(index + 1)];
      const updated: CommandDeckEntry = { ...current, group: nextGroup };
      const normalizedTarget = nextGroup ?? "Ungrouped";
      let insertAt = remaining.length;
      for (let idx = remaining.length - 1; idx >= 0; idx -= 1) {
        const group =
          normalizeDeckGroup(remaining[idx].group) ?? "Ungrouped";
        if (group === normalizedTarget) {
          insertAt = idx + 1;
          break;
        }
      }
      const next = [...remaining];
      next.splice(insertAt, 0, updated);
      return next;
    });
  };

  const setCommandDeckGroupEntries = (
    fromGroup: string,
    toGroupRaw: string
  ) => {
    const sourceGroup = String(fromGroup ?? "").trim() || "Ungrouped";
    const nextGroup = normalizeDeckGroup(toGroupRaw);
    const targetGroup = nextGroup ?? "Ungrouped";
    if (sourceGroup === targetGroup) {
      return;
    }
    setCommandDeck((prev) => {
      let changed = false;
      const next = prev.map((entry) => {
        const entryGroup = normalizeDeckGroup(entry.group) ?? "Ungrouped";
        if (entryGroup !== sourceGroup) {
          return entry;
        }
        changed = true;
        return { ...entry, group: nextGroup };
      });
      return changed ? next : prev;
    });
  };

  return {
    updateCommandDeckCommandEntry,
    updateCommandDeckTelemetryEntry,
    removeCommandDeckEntry,
    moveCommandDeckEntryWithinGroup,
    reorderCommandDeckEntryWithinGroup,
    setCommandDeckEntryGroup,
    setCommandDeckGroupEntries,
  };
}
