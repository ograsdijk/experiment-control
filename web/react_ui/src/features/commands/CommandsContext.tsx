import {
  createContext,
  useContext,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type ReactNode,
  type SetStateAction,
} from "react";

import type { CommandDeckEntry } from "../../types";
import type {
  PinnedCommandMap,
  PinnedParamDrafts,
} from "../profile/types";
import {
  normalizeCommandDeck,
  normalizePinnedCommands,
} from "../profile/utils";

/**
 * Shared state container for pinned commands + the command deck.
 *
 * App.tsx historically held the localStorage-persisted lists of pinned
 * commands per device, their in-flight param drafts, the running
 * busy-flag map keyed by pin, plus the entire command-deck data
 * structure (entries, group-collapse state, busy-by-id map, and the
 * id counter ref). All of those moved here so future extractions of
 * the device card render loop or the command deck modal can subscribe
 * directly via `useCommands()` instead of receiving the state as props.
 *
 * **Scope choices** (mirrors the round-8/9/10 Context shape):
 *
 * - The Provider owns the **state container only**. CRUD handlers
 *   (pin / unpin / set draft / open command modal / add deck entry /
 *   ...) stay in App.tsx for now — they call into device + process
 *   command controllers that haven't been extracted, and into the
 *   command history controller that already has its own hook.
 * - localStorage rehydration moves out of App.tsx's inline initializers
 *   into the Provider.
 * - `commandDeckIdRef` (the next-id counter for newly-created deck
 *   entries) moves into the Provider so consumers can mint IDs without
 *   threading the ref through props.
 *
 * **Downstream compatibility**: no centrex instance UI imports any
 * pinned-command or command-deck hook from `features/commands/`
 * (verified). This Provider is upstream-only and downstream-safe.
 */

export interface CommandsContextValue {
  // -----------------------------------------------------------------
  // Pinned commands (per-device)
  // -----------------------------------------------------------------
  pinnedCommands: PinnedCommandMap;
  setPinnedCommands: Dispatch<SetStateAction<PinnedCommandMap>>;
  pinnedParamDrafts: PinnedParamDrafts;
  setPinnedParamDrafts: Dispatch<SetStateAction<PinnedParamDrafts>>;
  pinnedBusyByKey: Record<string, boolean>;
  setPinnedBusyByKey: Dispatch<SetStateAction<Record<string, boolean>>>;

  // -----------------------------------------------------------------
  // Command deck (cross-device list of pinned commands + telemetry
  // readouts shown in a dedicated panel)
  // -----------------------------------------------------------------
  commandDeck: CommandDeckEntry[];
  setCommandDeck: Dispatch<SetStateAction<CommandDeckEntry[]>>;
  commandDeckCollapsedByGroup: Record<string, boolean>;
  setCommandDeckCollapsedByGroup: Dispatch<
    SetStateAction<Record<string, boolean>>
  >;
  commandDeckBusyById: Record<string, boolean>;
  setCommandDeckBusyById: Dispatch<SetStateAction<Record<string, boolean>>>;
  /** Monotonic counter used when minting new deck entry IDs. */
  commandDeckIdRef: MutableRefObject<number>;
}

const CommandsContext = createContext<CommandsContextValue | null>(null);

function loadPinnedCommands(): PinnedCommandMap {
  try {
    const raw = localStorage.getItem("ecui.pinnedCommands");
    if (!raw) return {};
    return normalizePinnedCommands(JSON.parse(raw));
  } catch {
    return {};
  }
}

function loadCommandDeck(): CommandDeckEntry[] {
  try {
    const raw = localStorage.getItem("ecui.commandDeck");
    if (!raw) return [];
    return normalizeCommandDeck(JSON.parse(raw));
  } catch {
    return [];
  }
}

function loadCommandDeckCollapsed(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem("ecui.commandDeck.collapsedByGroup");
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    const next: Record<string, boolean> = {};
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof value === "boolean") {
        next[key] = value;
      }
    }
    return next;
  } catch {
    return {};
  }
}

export function CommandsProvider({ children }: { children: ReactNode }) {
  const [pinnedCommands, setPinnedCommands] = useState<PinnedCommandMap>(
    loadPinnedCommands
  );
  const [pinnedParamDrafts, setPinnedParamDrafts] = useState<PinnedParamDrafts>(
    {}
  );
  const [pinnedBusyByKey, setPinnedBusyByKey] = useState<
    Record<string, boolean>
  >({});

  const [commandDeck, setCommandDeck] = useState<CommandDeckEntry[]>(
    loadCommandDeck
  );
  const [commandDeckCollapsedByGroup, setCommandDeckCollapsedByGroup] =
    useState<Record<string, boolean>>(loadCommandDeckCollapsed);
  const [commandDeckBusyById, setCommandDeckBusyById] = useState<
    Record<string, boolean>
  >({});
  const commandDeckIdRef = useRef<number>(1);

  const value = useMemo<CommandsContextValue>(
    () => ({
      pinnedCommands,
      setPinnedCommands,
      pinnedParamDrafts,
      setPinnedParamDrafts,
      pinnedBusyByKey,
      setPinnedBusyByKey,
      commandDeck,
      setCommandDeck,
      commandDeckCollapsedByGroup,
      setCommandDeckCollapsedByGroup,
      commandDeckBusyById,
      setCommandDeckBusyById,
      commandDeckIdRef,
    }),
    [
      pinnedCommands,
      pinnedParamDrafts,
      pinnedBusyByKey,
      commandDeck,
      commandDeckCollapsedByGroup,
      commandDeckBusyById,
    ]
  );

  return (
    <CommandsContext.Provider value={value}>
      {children}
    </CommandsContext.Provider>
  );
}

export function useCommands(): CommandsContextValue {
  const ctx = useContext(CommandsContext);
  if (ctx === null) {
    throw new Error("useCommands must be called inside a <CommandsProvider>");
  }
  return ctx;
}
