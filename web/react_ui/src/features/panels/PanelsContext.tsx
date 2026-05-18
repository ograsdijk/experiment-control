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

import {
  normalizePlotState,
  serializePlotState,
} from "../profile/plot_state";
import type { PlotPanelState } from "../stream/types";

/**
 * Shared state container for the plot panel grid.
 *
 * This is the first of three planned panel/plot extractions:
 *
 * 1. **State container + persistence** ← this Provider
 * 2. Handlers + memo tree + apply-helpers (next round)
 * 3. Render-loop extraction into `<PanelsGrid>` component (final round)
 *
 * App.tsx still owns the ~20 named handlers (`createPanel`,
 * `removePanel`, `addTraceToPanel`, ...), the 18-entry derivation
 * memo tree, the `applyRawStreamFrameToPanels` /
 * `applyStreamAnalysisOutputToPanels` helpers, the subscription
 * derivations, and the panel render loop. They all destructure
 * `panels` / `setPanels` / etc. from this Context and keep working
 * exactly as before.
 *
 * **Scope choices** (mirrors the previous seven Context extractions):
 *
 * - The Provider owns the **state container only**: panels list,
 *   active id, refs, modal-panel-id state, Y-axis editor state,
 *   `plotTick` re-render pulse, and the persistence machinery.
 * - localStorage rehydration (load) + autosave (write-on-change)
 *   both live here so the panel state has a single owner of its
 *   serialisation.
 * - `panelsRef` is kept in sync with `panels` via a useEffect — same
 *   pattern as `StreamAnalysisContext`'s state-mirror refs.
 *
 * **Downstream-compatibility**: no centrex instance UI references
 * panel state — each instance has its own custom layout. The
 * Provider is upstream-only and downstream-safe.
 *
 * **Re-render hygiene**: `plotTick` and `panels` change on every
 * telemetry write — that's the load-bearing UI pulse. `usePanels()`
 * consumers re-render when either changes. This matches App.tsx's
 * current behaviour (a single big component); the Context split
 * doesn't make it worse. If we later observe measurable regressions,
 * splitting `plotTick` into its own narrow Context is a follow-up.
 */

const DEFAULT_WINDOW_S = 60;

export const PANELS_DEFAULT_WINDOW_S = DEFAULT_WINDOW_S;

export interface PanelsContextValue {
  // -----------------------------------------------------------------
  // Source of truth
  // -----------------------------------------------------------------
  panels: PlotPanelState[];
  setPanels: Dispatch<SetStateAction<PlotPanelState[]>>;
  activePanelId: string | null;
  setActivePanelId: Dispatch<SetStateAction<string | null>>;

  // -----------------------------------------------------------------
  // Refs (async-callback-safe reads + id minting)
  // -----------------------------------------------------------------
  /** Mirror of `panels` state for closures that need synchronous
   *  reads (e.g. WS message handlers, animation frames). Synced via
   *  useEffect below. */
  panelsRef: MutableRefObject<PlotPanelState[]>;
  /** Next id counter for newly-created panels. */
  panelIdRef: MutableRefObject<number>;

  // -----------------------------------------------------------------
  // Plot re-render pulse — bumped by telemetry handlers after
  // out-of-band buffer pushes so panel components know to refresh.
  // Moved to features/panels/PlotTickContext.tsx (round 34) so
  // `usePanels()` consumers don't re-render at the WS-sample rate.
  // -----------------------------------------------------------------

  // -----------------------------------------------------------------
  // Modal-panel-id state — which panel currently has each modal open
  // -----------------------------------------------------------------
  plotOptionsPanelId: string | null;
  setPlotOptionsPanelId: Dispatch<SetStateAction<string | null>>;
  expandedPlotPanelId: string | null;
  setExpandedPlotPanelId: Dispatch<SetStateAction<string | null>>;
  streamTraceOptionsPanelId: string | null;
  setStreamTraceOptionsPanelId: Dispatch<SetStateAction<string | null>>;
  streamBinStatsOptionsPanelId: string | null;
  setStreamBinStatsOptionsPanelId: Dispatch<SetStateAction<string | null>>;
  streamParamsOptionsPanelId: string | null;
  setStreamParamsOptionsPanelId: Dispatch<SetStateAction<string | null>>;
  streamBin2dOptionsPanelId: string | null;
  setStreamBin2dOptionsPanelId: Dispatch<SetStateAction<string | null>>;

  // -----------------------------------------------------------------
  // Y-axis manual-range editor (transient draft state, plus the
  // cached auto-range computation for the currently-edited panel)
  // -----------------------------------------------------------------
  yAxisDraftMin: string | number;
  setYAxisDraftMin: Dispatch<SetStateAction<string | number>>;
  yAxisDraftMax: string | number;
  setYAxisDraftMax: Dispatch<SetStateAction<string | number>>;
  yAxisAutoRange: { min: number; max: number } | null;
  setYAxisAutoRange: Dispatch<
    SetStateAction<{ min: number; max: number } | null>
  >;

  // -----------------------------------------------------------------
  // Panel title editor (transient draft state)
  // -----------------------------------------------------------------
  editingPanelId: string | null;
  setEditingPanelId: Dispatch<SetStateAction<string | null>>;
  panelTitleDraft: string;
  setPanelTitleDraft: Dispatch<SetStateAction<string>>;
}

const PanelsContext = createContext<PanelsContextValue | null>(null);

function loadInitialPlotState() {
  try {
    const raw = localStorage.getItem("ecui.plotState");
    if (!raw) {
      return normalizePlotState(null, { defaultWindowS: DEFAULT_WINDOW_S });
    }
    return normalizePlotState(JSON.parse(raw), {
      defaultWindowS: DEFAULT_WINDOW_S,
    });
  } catch {
    return normalizePlotState(null, { defaultWindowS: DEFAULT_WINDOW_S });
  }
}

export function PanelsProvider({ children }: { children: ReactNode }) {
  // Rehydrate from localStorage exactly once at mount, same shape as
  // App.tsx's previous inline useMemo. The two refs seed from this so
  // the initial render reads consistent state.
  const initial = useMemo(() => loadInitialPlotState(), []);

  const [panels, setPanels] = useState<PlotPanelState[]>(initial.panels);
  const [activePanelId, setActivePanelId] = useState<string | null>(
    initial.activePanelId
  );

  const panelsRef = useRef<PlotPanelState[]>(initial.panels);
  const panelIdRef = useRef<number>(initial.nextPanelId);

  const [plotOptionsPanelId, setPlotOptionsPanelId] = useState<string | null>(
    null
  );
  const [expandedPlotPanelId, setExpandedPlotPanelId] = useState<string | null>(
    null
  );
  const [streamTraceOptionsPanelId, setStreamTraceOptionsPanelId] = useState<
    string | null
  >(null);
  const [streamBinStatsOptionsPanelId, setStreamBinStatsOptionsPanelId] =
    useState<string | null>(null);
  const [streamParamsOptionsPanelId, setStreamParamsOptionsPanelId] = useState<
    string | null
  >(null);
  const [streamBin2dOptionsPanelId, setStreamBin2dOptionsPanelId] = useState<
    string | null
  >(null);

  const [yAxisDraftMin, setYAxisDraftMin] = useState<string | number>("");
  const [yAxisDraftMax, setYAxisDraftMax] = useState<string | number>("");
  const [yAxisAutoRange, setYAxisAutoRange] = useState<{
    min: number;
    max: number;
  } | null>(null);

  const [editingPanelId, setEditingPanelId] = useState<string | null>(null);
  const [panelTitleDraft, setPanelTitleDraft] = useState("");

  // Keep panelsRef in sync so closures (telemetry message handlers,
  // animation-frame callbacks, etc.) always see the latest panels
  // array without subscribing to re-renders.
  useEffect(() => {
    panelsRef.current = panels;
  }, [panels]);

  // Autosave on every panels/activePanelId change. App.tsx used to
  // own this useEffect inline; centralising it here means the
  // persistence layer can be reasoned about in one place.
  useEffect(() => {
    try {
      const serialized = serializePlotState({ panels, activePanelId });
      localStorage.setItem("ecui.plotState", JSON.stringify(serialized));
    } catch {
      // ignore storage errors
    }
  }, [panels, activePanelId]);

  const value = useMemo<PanelsContextValue>(
    () => ({
      panels,
      setPanels,
      activePanelId,
      setActivePanelId,
      panelsRef,
      panelIdRef,
      plotOptionsPanelId,
      setPlotOptionsPanelId,
      expandedPlotPanelId,
      setExpandedPlotPanelId,
      streamTraceOptionsPanelId,
      setStreamTraceOptionsPanelId,
      streamBinStatsOptionsPanelId,
      setStreamBinStatsOptionsPanelId,
      streamParamsOptionsPanelId,
      setStreamParamsOptionsPanelId,
      streamBin2dOptionsPanelId,
      setStreamBin2dOptionsPanelId,
      yAxisDraftMin,
      setYAxisDraftMin,
      yAxisDraftMax,
      setYAxisDraftMax,
      yAxisAutoRange,
      setYAxisAutoRange,
      editingPanelId,
      setEditingPanelId,
      panelTitleDraft,
      setPanelTitleDraft,
    }),
    [
      panels,
      activePanelId,
      plotOptionsPanelId,
      expandedPlotPanelId,
      streamTraceOptionsPanelId,
      streamBinStatsOptionsPanelId,
      streamParamsOptionsPanelId,
      streamBin2dOptionsPanelId,
      yAxisDraftMin,
      yAxisDraftMax,
      yAxisAutoRange,
      editingPanelId,
      panelTitleDraft,
    ]
  );

  return (
    <PanelsContext.Provider value={value}>{children}</PanelsContext.Provider>
  );
}

export function usePanels(): PanelsContextValue {
  const ctx = useContext(PanelsContext);
  if (ctx === null) {
    throw new Error("usePanels must be called inside a <PanelsProvider>");
  }
  return ctx;
}
