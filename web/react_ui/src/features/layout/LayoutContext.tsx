import {
  createContext,
  useContext,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type Dispatch,
  type MutableRefObject,
  type ReactNode,
  type SetStateAction,
} from "react";

import type { PlotWorkspaceColumnsSetting } from "../profile/types";
import {
  clampNavWidth,
  normalizePlotWorkspaceColumnsSetting,
} from "../profile/utils";

/**
 * Shared layout / viewport state container.
 *
 * App.tsx historically held the nav sidebar width (with resize drag
 * machinery), the device-panel collapsed flag and tab selector, the
 * plot-workspace columns setting, the viewport-width tracker the
 * mobile-breakpoint check depends on, the plot-grid DOM ref, and the
 * shared drag-column-count cache used by both the device-grid and
 * plot-grid drag/drop. All of those moved here so the eventual
 * layout-shell extraction (header / sidebar / grids) can subscribe
 * directly without prop-drilling.
 *
 * **Scope choices** (mirrors the previous Context extractions):
 *
 * - The Provider owns the **state container only**. Resize event
 *   handlers and the window-resize listener stay in App.tsx — they
 *   bridge to DOM events that are easier to manage where the rest of
 *   the App lives until the layout shell extracts.
 * - localStorage rehydration for the 4 persisted layout settings
 *   (`navWidth`, `devicePanelCollapsed`, `devicePanelTab`,
 *   `plotWorkspaceColumns`) moves out of App.tsx's inline state
 *   initializers into the Provider.
 * - `plotGridStyle` (the previously-inline `useMemo` that builds the
 *   `gridTemplateColumns` CSS from `plotWorkspaceColumns` +
 *   viewport width) lives in the Provider so consumers don't
 *   recompute the same CSS independently.
 *
 * **Downstream-compatibility**: no centrex instance UI references any
 * of this layout state — each instance has its own root layout. The
 * Provider is upstream-only and downstream-safe.
 */

const DEFAULT_NAV_WIDTH = 360;
const NAV_MIN_WIDTH = 260;
const NAV_MAX_WIDTH = 900;
const PLOT_GRID_MOBILE_BREAKPOINT = 900;

export const LAYOUT_NAV_MIN_WIDTH = NAV_MIN_WIDTH;
export const LAYOUT_NAV_MAX_WIDTH = NAV_MAX_WIDTH;
export const LAYOUT_DEFAULT_NAV_WIDTH = DEFAULT_NAV_WIDTH;
export const LAYOUT_PLOT_GRID_MOBILE_BREAKPOINT = PLOT_GRID_MOBILE_BREAKPOINT;

type DevicePanelTab = "devices" | "deck";

export interface LayoutContextValue {
  // -----------------------------------------------------------------
  // Sidebar width + resize drag (localStorage-persisted)
  // -----------------------------------------------------------------
  navWidth: number;
  setNavWidth: Dispatch<SetStateAction<number>>;
  isResizing: boolean;
  setIsResizing: Dispatch<SetStateAction<boolean>>;
  resizeRef: MutableRefObject<{ startX: number; startWidth: number } | null>;
  resizePendingWidthRef: MutableRefObject<number | null>;
  resizeRafRef: MutableRefObject<number | null>;

  // -----------------------------------------------------------------
  // Device panel state (localStorage-persisted)
  // -----------------------------------------------------------------
  isDevicePanelCollapsed: boolean;
  setIsDevicePanelCollapsed: Dispatch<SetStateAction<boolean>>;
  devicePanelTab: DevicePanelTab;
  setDevicePanelTab: Dispatch<SetStateAction<DevicePanelTab>>;

  // -----------------------------------------------------------------
  // Plot workspace columns (localStorage-persisted) + transient
  // options popover open/close state
  // -----------------------------------------------------------------
  plotWorkspaceColumns: PlotWorkspaceColumnsSetting;
  setPlotWorkspaceColumns: Dispatch<SetStateAction<PlotWorkspaceColumnsSetting>>;
  plotWorkspaceOptionsOpen: boolean;
  setPlotWorkspaceOptionsOpen: Dispatch<SetStateAction<boolean>>;

  // -----------------------------------------------------------------
  // Viewport width (updated by the App.tsx window-resize listener)
  // and the derived isNarrow / plot-grid CSS
  // -----------------------------------------------------------------
  viewportWidth: number;
  setViewportWidth: Dispatch<SetStateAction<number>>;
  isNarrowPlotViewport: boolean;
  plotGridStyle: CSSProperties | undefined;

  // -----------------------------------------------------------------
  // Plot-grid DOM ref + shared drag column-count cache
  // -----------------------------------------------------------------
  plotGridRef: MutableRefObject<HTMLDivElement | null>;
  dragColumnsRef: MutableRefObject<{ device: number; panel: number }>;
}

const LayoutContext = createContext<LayoutContextValue | null>(null);

function loadNavWidth(): number {
  try {
    const raw = localStorage.getItem("ecui.navWidth");
    const parsed = raw ? Number(raw) : NaN;
    if (Number.isFinite(parsed)) {
      return clampNavWidth(parsed, { min: NAV_MIN_WIDTH, max: NAV_MAX_WIDTH });
    }
  } catch {
    // ignore storage errors
  }
  return clampNavWidth(DEFAULT_NAV_WIDTH, {
    min: NAV_MIN_WIDTH,
    max: NAV_MAX_WIDTH,
  });
}

function loadDevicePanelCollapsed(): boolean {
  try {
    const raw = localStorage.getItem("ecui.devicePanelCollapsed");
    return raw === "1" || raw === "true";
  } catch {
    return false;
  }
}

function loadDevicePanelTab(): DevicePanelTab {
  try {
    const raw = String(localStorage.getItem("ecui.devicePanelTab") ?? "").trim();
    return raw === "deck" ? "deck" : "devices";
  } catch {
    return "devices";
  }
}

function loadPlotWorkspaceColumns(): PlotWorkspaceColumnsSetting {
  try {
    return normalizePlotWorkspaceColumnsSetting(
      localStorage.getItem("ecui.plotWorkspaceColumns")
    );
  } catch {
    return "auto";
  }
}

function loadViewportWidth(): number {
  if (typeof window === "undefined") {
    return 1200;
  }
  return window.innerWidth;
}

export function LayoutProvider({ children }: { children: ReactNode }) {
  const [navWidth, setNavWidth] = useState<number>(loadNavWidth);
  const [isResizing, setIsResizing] = useState(false);
  const resizeRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const resizePendingWidthRef = useRef<number | null>(null);
  const resizeRafRef = useRef<number | null>(null);

  const [isDevicePanelCollapsed, setIsDevicePanelCollapsed] = useState<boolean>(
    loadDevicePanelCollapsed
  );
  const [devicePanelTab, setDevicePanelTab] =
    useState<DevicePanelTab>(loadDevicePanelTab);

  const [plotWorkspaceColumns, setPlotWorkspaceColumns] =
    useState<PlotWorkspaceColumnsSetting>(loadPlotWorkspaceColumns);
  const [plotWorkspaceOptionsOpen, setPlotWorkspaceOptionsOpen] =
    useState(false);

  const [viewportWidth, setViewportWidth] = useState<number>(loadViewportWidth);

  const plotGridRef = useRef<HTMLDivElement | null>(null);
  const dragColumnsRef = useRef<{ device: number; panel: number }>({
    device: 1,
    panel: 1,
  });

  const isNarrowPlotViewport = viewportWidth <= PLOT_GRID_MOBILE_BREAKPOINT;

  const plotGridStyle = useMemo<CSSProperties | undefined>(() => {
    if (isNarrowPlotViewport) {
      return { gridTemplateColumns: "1fr" };
    }
    if (plotWorkspaceColumns === "auto") {
      return undefined;
    }
    return {
      gridTemplateColumns: `repeat(${plotWorkspaceColumns}, minmax(0, 1fr))`,
    };
  }, [isNarrowPlotViewport, plotWorkspaceColumns]);

  const value = useMemo<LayoutContextValue>(
    () => ({
      navWidth,
      setNavWidth,
      isResizing,
      setIsResizing,
      resizeRef,
      resizePendingWidthRef,
      resizeRafRef,
      isDevicePanelCollapsed,
      setIsDevicePanelCollapsed,
      devicePanelTab,
      setDevicePanelTab,
      plotWorkspaceColumns,
      setPlotWorkspaceColumns,
      plotWorkspaceOptionsOpen,
      setPlotWorkspaceOptionsOpen,
      viewportWidth,
      setViewportWidth,
      isNarrowPlotViewport,
      plotGridStyle,
      plotGridRef,
      dragColumnsRef,
    }),
    [
      navWidth,
      isResizing,
      isDevicePanelCollapsed,
      devicePanelTab,
      plotWorkspaceColumns,
      plotWorkspaceOptionsOpen,
      viewportWidth,
      isNarrowPlotViewport,
      plotGridStyle,
    ]
  );

  return (
    <LayoutContext.Provider value={value}>{children}</LayoutContext.Provider>
  );
}

export function useLayout(): LayoutContextValue {
  const ctx = useContext(LayoutContext);
  if (ctx === null) {
    throw new Error("useLayout must be called inside a <LayoutProvider>");
  }
  return ctx;
}
