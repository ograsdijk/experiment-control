import {
  createContext,
  useContext,
  useCallback,
  useEffect,
  useMemo,
  useRef,
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
  requestPlotTick: () => void;
}

const PlotTickContext = createContext<PlotTickContextValue | null>(null);

export function PlotTickProvider({ children }: { children: ReactNode }) {
  const [plotTick, rawSetPlotTick] = useState(0);
  const frameRef = useRef<number | null>(null);
  const pendingRef = useRef<SetStateAction<number> | null>(null);
  const flushPlotTick = useCallback(() => {
    frameRef.current = null;
    const pending = pendingRef.current;
    pendingRef.current = null;
    if (pending !== null) {
      rawSetPlotTick(pending);
    }
  }, []);
  const setPlotTick = useCallback<Dispatch<SetStateAction<number>>>((action) => {
    const pending = pendingRef.current;
    if (typeof pending === "function" && typeof action === "function") {
      pendingRef.current = (value: number) => action(pending(value));
    } else {
      pendingRef.current = action;
    }
    if (frameRef.current === null) {
      frameRef.current = window.requestAnimationFrame(flushPlotTick);
    }
  }, [flushPlotTick]);
  const requestPlotTick = useCallback(() => {
    setPlotTick((tick) => tick + 1);
  }, [setPlotTick]);
  useEffect(() => {
    return () => {
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
      }
    };
  }, []);
  const value = useMemo<PlotTickContextValue>(
    () => ({ plotTick, setPlotTick, requestPlotTick }),
    [plotTick, setPlotTick, requestPlotTick]
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
