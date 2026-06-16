import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type ReactNode,
  type SetStateAction,
} from "react";

import type {
  StreamAnalysisWorkspaceConfig,
  StreamDagNodeConfig,
  StreamDagOutputConfig,
  StreamWorkspaceStoreStatus,
} from "../stream/types";
import {
  nextWorkspaceCounter,
  normalizeStreamWorkspaceRecord,
  normalizeWorkspaceStoreStatus,
} from "../stream/workspace";

/**
 * Shared stream-analysis (DAQ workspace) state container.
 *
 * App.tsx historically held the entire DAQ editor state — saved
 * workspaces, draft buffer for the in-progress edit, modal open/close,
 * persistence-layer status, plus the DOM refs the focus-into-view
 * machinery needs. All of those moved here so future panel / device-card
 * extractions don't have to thread DAQ state through props.
 *
 * **Scope choices for the first cut** (mirrors the TelemetryContext
 * extraction in round 8):
 *
 * - The Provider owns the **state + actions** for workspaces, drafts,
 *   modal, and persistence status.
 * - Complex network-side handlers (load all workspaces from the
 *   stream-analysis RPC, persist drafts, reset nodes) **stay in
 *   App.tsx** for now — they call into `_drain_pending_to_file`,
 *   `_apply_overlay_helpers`, etc. that haven't been extracted yet.
 *   Those move when their dependencies do.
 * - Panel-loop derivations that *read* workspace state (the long memo
 *   tree around App.tsx line ~983) stay in App.tsx — those move when
 *   panel state moves.
 *
 * No downstream centrex instance UI imports any DAQ-related hook
 * (verified via grep against the instances' instance_ui app src dirs),
 * so adding this context has zero downstream impact.
 */

export interface StreamAnalysisContextValue {
  // -----------------------------------------------------------------
  // Source-of-truth workspace state (server-mirrored)
  // -----------------------------------------------------------------
  streamWorkspaces: Record<string, StreamAnalysisWorkspaceConfig>;
  setStreamWorkspaces: Dispatch<
    SetStateAction<Record<string, StreamAnalysisWorkspaceConfig>>
  >;
  streamWorkspaceRevisions: Record<string, number>;
  setStreamWorkspaceRevisions: Dispatch<
    SetStateAction<Record<string, number>>
  >;
  workspaceStoreStatus: StreamWorkspaceStoreStatus;
  setWorkspaceStoreStatus: Dispatch<SetStateAction<StreamWorkspaceStoreStatus>>;
  workspaceStoreBusyAction: "save" | "reload" | null;
  setWorkspaceStoreBusyAction: Dispatch<
    SetStateAction<"save" | "reload" | null>
  >;

  // -----------------------------------------------------------------
  // DAQ editor (modal) — draft buffer and focus state
  // -----------------------------------------------------------------
  daqOpen: boolean;
  setDaqOpen: Dispatch<SetStateAction<boolean>>;
  daqWorkspaceId: string | null;
  setDaqWorkspaceId: Dispatch<SetStateAction<string | null>>;
  daqDraftName: string;
  setDaqDraftName: Dispatch<SetStateAction<string>>;
  daqDraftNodes: StreamDagNodeConfig[];
  setDaqDraftNodes: Dispatch<SetStateAction<StreamDagNodeConfig[]>>;
  daqDraftOutputs: StreamDagOutputConfig[];
  setDaqDraftOutputs: Dispatch<SetStateAction<StreamDagOutputConfig[]>>;
  daqDraftEnabled: boolean;
  setDaqDraftEnabled: Dispatch<SetStateAction<boolean>>;
  daqResetNodeBusyId: string | null;
  setDaqResetNodeBusyId: Dispatch<SetStateAction<string | null>>;
  daqFocusedNodeId: string | null;
  setDaqFocusedNodeId: Dispatch<SetStateAction<string | null>>;

  // -----------------------------------------------------------------
  // Refs (mutated from async callbacks / event handlers that need
  // to read current state without re-rendering)
  // -----------------------------------------------------------------
  /** Next id counter for newly-created workspaces. */
  streamWorkspaceIdRef: MutableRefObject<number>;
  /** Mirror of `streamWorkspaces` for async-callback reads. */
  streamWorkspacesRef: MutableRefObject<
    Record<string, StreamAnalysisWorkspaceConfig>
  >;
  /** Mirror of `streamWorkspaceRevisions` for async-callback reads. */
  streamWorkspaceRevisionsRef: MutableRefObject<Record<string, number>>;
  /** DOM refs for each node card in the editor, used by focus-into-view. */
  daqNodeCardRefs: MutableRefObject<Map<string, HTMLDivElement>>;
  /** Window timer id for the focus-into-view debounce. */
  daqNodeFocusTimeoutRef: MutableRefObject<number | null>;
  /** Whether the stream_analysis RPC is ready (mutated by App.tsx). */
  streamAnalysisReadyRef: MutableRefObject<boolean>;
}

const StreamAnalysisContext = createContext<StreamAnalysisContextValue | null>(
  null
);

function loadInitialStreamWorkspaceState() {
  try {
    const raw = localStorage.getItem("ecui.streamWorkspaces");
    const workspaces = normalizeStreamWorkspaceRecord(
      raw ? JSON.parse(raw) : null
    );
    return {
      workspaces,
      nextId: nextWorkspaceCounter(workspaces),
    };
  } catch {
    return {
      workspaces: {} as Record<string, StreamAnalysisWorkspaceConfig>,
      nextId: 1,
    };
  }
}

export function StreamAnalysisProvider({ children }: { children: ReactNode }) {
  // localStorage-rehydrated seed used for both the state and the ref so
  // they start in sync. Computed once at mount; lives inside the
  // Provider so other consumers don't accidentally duplicate the read.
  const initial = useMemo(() => loadInitialStreamWorkspaceState(), []);

  const [streamWorkspaces, setStreamWorkspaces] = useState<
    Record<string, StreamAnalysisWorkspaceConfig>
  >(initial.workspaces);
  const [streamWorkspaceRevisions, setStreamWorkspaceRevisions] = useState<
    Record<string, number>
  >({});
  const [workspaceStoreStatus, setWorkspaceStoreStatus] =
    useState<StreamWorkspaceStoreStatus>(() =>
      normalizeWorkspaceStoreStatus(null)
    );
  const [workspaceStoreBusyAction, setWorkspaceStoreBusyAction] =
    useState<"save" | "reload" | null>(null);

  const [daqOpen, setDaqOpen] = useState(false);
  const [daqWorkspaceId, setDaqWorkspaceId] = useState<string | null>(
    Object.keys(initial.workspaces)[0] ?? null
  );
  const [daqDraftName, setDaqDraftName] = useState("");
  const [daqDraftNodes, setDaqDraftNodes] = useState<StreamDagNodeConfig[]>([]);
  const [daqDraftOutputs, setDaqDraftOutputs] = useState<
    StreamDagOutputConfig[]
  >([]);
  const [daqDraftEnabled, setDaqDraftEnabled] = useState(true);
  const [daqResetNodeBusyId, setDaqResetNodeBusyId] = useState<string | null>(
    null
  );
  const [daqFocusedNodeId, setDaqFocusedNodeId] = useState<string | null>(null);

  const streamWorkspaceIdRef = useRef<number>(initial.nextId);
  const streamWorkspacesRef = useRef<
    Record<string, StreamAnalysisWorkspaceConfig>
  >(initial.workspaces);
  const streamWorkspaceRevisionsRef = useRef<Record<string, number>>({});
  const daqNodeCardRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const daqNodeFocusTimeoutRef = useRef<number | null>(null);
  const streamAnalysisReadyRef = useRef<boolean>(false);

  // Keep the two state-mirror refs in sync with their state. App.tsx used
  // to maintain these inline (see commits prior to round 9); centralising
  // the sync here means consumers can rely on the ref being correct
  // immediately after they call the corresponding setter and the next
  // render flushes.
  useEffect(() => {
    streamWorkspacesRef.current = streamWorkspaces;
  }, [streamWorkspaces]);

  useEffect(() => {
    streamWorkspaceRevisionsRef.current = streamWorkspaceRevisions;
  }, [streamWorkspaceRevisions]);

  // One-shot cleanup of the focus-timeout on unmount so we don't leave
  // a stale timer dangling if the Provider tears down.
  useEffect(() => {
    return () => {
      if (daqNodeFocusTimeoutRef.current !== null) {
        window.clearTimeout(daqNodeFocusTimeoutRef.current);
        daqNodeFocusTimeoutRef.current = null;
      }
    };
  }, []);

  const value = useMemo<StreamAnalysisContextValue>(
    () => ({
      streamWorkspaces,
      setStreamWorkspaces,
      streamWorkspaceRevisions,
      setStreamWorkspaceRevisions,
      workspaceStoreStatus,
      setWorkspaceStoreStatus,
      workspaceStoreBusyAction,
      setWorkspaceStoreBusyAction,
      daqOpen,
      setDaqOpen,
      daqWorkspaceId,
      setDaqWorkspaceId,
      daqDraftName,
      setDaqDraftName,
      daqDraftNodes,
      setDaqDraftNodes,
      daqDraftOutputs,
      setDaqDraftOutputs,
      daqDraftEnabled,
      setDaqDraftEnabled,
      daqResetNodeBusyId,
      setDaqResetNodeBusyId,
      daqFocusedNodeId,
      setDaqFocusedNodeId,
      streamWorkspaceIdRef,
      streamWorkspacesRef,
      streamWorkspaceRevisionsRef,
      daqNodeCardRefs,
      daqNodeFocusTimeoutRef,
      streamAnalysisReadyRef,
    }),
    [
      streamWorkspaces,
      streamWorkspaceRevisions,
      workspaceStoreStatus,
      workspaceStoreBusyAction,
      daqOpen,
      daqWorkspaceId,
      daqDraftName,
      daqDraftNodes,
      daqDraftOutputs,
      daqDraftEnabled,
      daqResetNodeBusyId,
      daqFocusedNodeId,
    ]
  );

  return (
    <StreamAnalysisContext.Provider value={value}>
      {children}
    </StreamAnalysisContext.Provider>
  );
}

export function useStreamAnalysis(): StreamAnalysisContextValue {
  const ctx = useContext(StreamAnalysisContext);
  if (ctx === null) {
    throw new Error(
      "useStreamAnalysis must be called inside a <StreamAnalysisProvider>"
    );
  }
  return ctx;
}
