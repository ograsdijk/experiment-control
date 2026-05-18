import {
  createContext,
  useContext,
  useMemo,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";

/**
 * Narrow context for the plot re-render pulse.
 *
 * `plotTick` is bumped after a batch of telemetry / stream / DAG
 * samples is appended to panel buffers, so plot panels can read
 * fresh data from `bufferRef`s on a render. It fires at the
 * incoming-data rate (50-100 Hz for telemetry, similar for raw
 * streams), so anything subscribing to the same context as
 * `plotTick` re-renders at that rate.
 *
 * Pulling it out of `PanelsContext` means:
 *
 * - `usePanels()` consumers (App.tsx's destructure, PanelsGrid, the
 *   modal components) only re-render on actual panel/modal state
 *   changes — not on every WS sample.
 * - Plot-rendering components opt in via `usePlotTick()` and accept
 *   the high-frequency re-renders explicitly.
 *
 * Mutators across the codebase (~10 sites in panel/stream handler
 * hooks, plus useTelemetryPipeline, plus useRaw/StreamAnalysis
 * subscription hooks) get `setPlotTick` from this context too.
 */

interface PlotTickContextValue {
  plotTick: number;
  setPlotTick: Dispatch<SetStateAction<number>>;
}

const PlotTickContext = createContext<PlotTickContextValue | null>(null);

export function PlotTickProvider({ children }: { children: ReactNode }) {
  const [plotTick, setPlotTick] = useState(0);
  const value = useMemo<PlotTickContextValue>(
    () => ({ plotTick, setPlotTick }),
    [plotTick]
  );
  return (
    <PlotTickContext.Provider value={value}>
      {children}
    </PlotTickContext.Provider>
  );
}

export function usePlotTick(): PlotTickContextValue {
  const ctx = useContext(PlotTickContext);
  if (ctx === null) {
    throw new Error("usePlotTick must be called inside a <PlotTickProvider>");
  }
  return ctx;
}
