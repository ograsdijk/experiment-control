// @vitest-environment jsdom

import { act, createElement, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MantineProvider } from "@mantine/core";
import { DndContext } from "@dnd-kit/core";
import { SortableContext } from "@dnd-kit/sortable";

// The card is under test, not the plots. Every plot body owns a canvas
// that jsdom cannot render, so they are stubbed to nothing — what matters
// here is the chrome around them.
vi.mock("../../components/PlotPanel", () => ({ PlotPanel: () => null }));
vi.mock("../../components/StreamRawPanel", () => ({
  StreamRawPanel: () => null,
}));
vi.mock("../../components/StreamWaterfallPanel", () => ({
  StreamWaterfallPanel: () => null,
}));
vi.mock("../../components/StreamBin2dPanel", () => ({
  StreamBin2dPanel: () => null,
}));
vi.mock("../../components/StreamParamsPanel", () => ({
  StreamParamsPanel: () => null,
}));
vi.mock("../../components/StreamBinStatsPanel", () => ({
  StreamBinStatsPanel: () => null,
  BIN_STATS_OVERLAY_COLORS: ["#000000"],
  BIN_STATS_FIT_OVERLAY_COLORS: ["#000000"],
  binStatsMeanStroke: () => "#000000",
}));

import { PanelCard } from "./PanelCard";
import { PanelsProvider, usePanels } from "./PanelsContext";
import { TelemetryProvider } from "../telemetry/TelemetryContext";
import { StreamAnalysisProvider } from "../stream_analysis/StreamAnalysisContext";
import type { PlotPanelState } from "../stream/types";
import type { PanelsGridHandlers, PanelsGridHelpers } from "./PanelsGrid";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function telemetryPanel(
  overrides: Partial<PlotPanelState> = {}
): PlotPanelState {
  return {
    id: "panel-1",
    title: "Chamber pressure",
    kind: "telemetry",
    traces: [],
    timeWindowS: 60,
    yScaleMode: "auto",
    yMin: null,
    yMax: null,
    yDisplayMode: "absolute",
    yOffsetMode: "auto",
    yOffsetValue: null,
    smoothingMode: "none",
    smoothingWindowS: 10,
    ...overrides,
  } as PlotPanelState;
}

function binStatsPanel(): PlotPanelState {
  return {
    id: "panel-2",
    title: "Fluorescence vs scan",
    kind: "stream_bin_stats",
    workspaceId: "workspace-1",
    outputId: "fluor_integral_vs_scan",
    overlayOutputIds: [],
    fitOverlayOutputIds: [],
    stream: null,
    channelIndex: 0,
    analysis: {},
    binStats: {},
    uncertaintyMode: "std",
    uncertaintyScale: 1,
    showBinMarkers: false,
    xOffset: 0,
    xScale: 1,
    yScaleMode: "auto",
    yMin: null,
    yMax: null,
  } as unknown as PlotPanelState;
}

const noop = () => {};

function makeHelpers(): PanelsGridHelpers {
  return {
    resolveTelemetryPanelOffset: () => null,
    streamTraceOverlaySeries: () => [],
    streamExtraChannelSeries: () => [],
    streamBinStatsOverlaySeries: () => [],
    streamBinStatsFitOverlayCurves: () => [],
    isExpandablePlotPanel: () => true,
    copyTextToClipboard: async () => {},
  };
}

function makeHandlers(
  overrides: Partial<PanelsGridHandlers> = {}
): PanelsGridHandlers {
  return {
    startPanelTitleEdit: noop,
    commitPanelTitleEdit: noop,
    cancelPanelTitleEdit: noop,
    removePanel: noop,
    duplicatePanel: noop,
    setPanelLayout: noop,
    removeTraceFromPanel: noop,
    setPanelTimeWindow: noop,
    openPlotOptions: noop,
    closePlotOptions: noop,
    applyPlotOptionsAxis: noop,
    setPlotOptionsAxisMode: noop,
    setTelemetryYDisplayMode: noop,
    setTelemetryYOffsetMode: noop,
    setTelemetrySmoothingMode: noop,
    setTelemetrySmoothingWindow: noop,
    clearPanelBuffers: noop,
    clearStreamPanelFrames: noop,
    clearStreamBinStatsPanel: async () => {},
    clearStreamBin2dPanel: async () => {},
    setStreamAnalysisPanelWorkspace: noop,
    setStreamAnalysisPanelOutput: noop,
    openExpandedPlot: noop,
    openStreamTraceOptionsModal: noop,
    openStreamBin2dOptionsModal: noop,
    openStreamParamsOptionsModal: noop,
    openStreamBinStatsOptionsModal: noop,
    ...overrides,
  };
}

/**
 * Bridges panel context out to the assertions: reports the live active id
 * and hands back the editing setter so a rename session can be started
 * the way the real title editor starts one.
 */
let activeIdProbe: string | null = null;
let startEditingProbe: (panelId: string | null) => void = () => {};
function ContextProbe() {
  const { activePanelId, setEditingPanelId } = usePanels();
  activeIdProbe = activePanelId;
  startEditingProbe = setEditingPanelId;
  return null;
}

/**
 * A second panel that is active at mount.
 *
 * The provider normalises an unknown `activePanelId` to the first panel,
 * so with a single panel in state the card under test would always read
 * as active and no activation assertion could fail.
 */
function decoyPanel(): PlotPanelState {
  return telemetryPanel({ id: "panel-0", title: "Decoy" } as never);
}

const mountedRoots: Array<{ root: ReturnType<typeof createRoot>; host: HTMLElement }> =
  [];

function renderCard(
  panel: PlotPanelState,
  handlers: PanelsGridHandlers = makeHandlers(),
  activePanelId: string | null = null
): HTMLElement {
  localStorage.setItem(
    "ecui.plotState",
    JSON.stringify({
      panels: [decoyPanel(), panel],
      activePanelId: activePanelId ?? "panel-0",
    })
  );
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  mountedRoots.push({ root, host });

  const tree: ReactNode = createElement(
    MantineProvider,
    null,
    createElement(
      TelemetryProvider,
      null,
      createElement(
        StreamAnalysisProvider,
        null,
        createElement(
          PanelsProvider,
          null,
          createElement(ContextProbe),
          createElement(
            DndContext,
            null,
            createElement(SortableContext, {
              items: [`panel:${panel.id}`],
              children: createElement(PanelCard, {
                panel,
                streamWorkspaceOptions: [],
                yAxisDraftInvalid: false,
                streamWsConnected: true,
                streamAnalysisWsConnected: true,
                activeUiDrag: null,
                helpers: makeHelpers(),
                handlers,
              }),
            })
          )
        )
      )
    )
  );
  act(() => root.render(tree));
  return host;
}

function pointerClick(
  target: Element,
  from: { x: number; y: number } = { x: 10, y: 10 },
  to: { x: number; y: number } = { x: 10, y: 10 }
) {
  // jsdom has no PointerEvent constructor; React dispatches on the native
  // event *type*, so a MouseEvent named "pointerdown" reaches the same
  // handler and carries the coordinates the slop check reads.
  act(() => {
    target.dispatchEvent(
      new MouseEvent("pointerdown", {
        bubbles: true,
        clientX: from.x,
        clientY: from.y,
      })
    );
    target.dispatchEvent(
      new MouseEvent("pointerup", {
        bubbles: true,
        clientX: to.x,
        clientY: to.y,
      })
    );
  });
}

describe("PanelCard", () => {
  beforeEach(() => {
    activeIdProbe = null;
    localStorage.clear();
    // Mantine and the plot-height hook both reach for browser APIs jsdom
    // does not implement.
    if (!window.matchMedia) {
      window.matchMedia = ((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: noop,
        removeListener: noop,
        addEventListener: noop,
        removeEventListener: noop,
        dispatchEvent: () => false,
      })) as unknown as typeof window.matchMedia;
    }
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
    globalThis.IntersectionObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() {
        return [];
      }
      root = null;
      rootMargin = "";
      thresholds = [];
    } as unknown as typeof IntersectionObserver;
  });

  afterEach(() => {
    for (const { root, host } of mountedRoots.splice(0)) {
      act(() => root.unmount());
      host.remove();
    }
  });

  describe("header", () => {
    it("shows the title without the badges the old header carried", () => {
      const host = renderCard(telemetryPanel());
      expect(host.textContent).toContain("Chamber pressure");
      expect(host.textContent).not.toContain("Set active");
      expect(host.textContent).not.toContain("Active");
      expect(host.textContent).not.toContain("Telemetry");
      expect(host.textContent).not.toContain("Plot options");
    });

    it("renders settings, expand and overflow controls", () => {
      const host = renderCard(telemetryPanel());
      expect(host.querySelector('[aria-label="Panel settings"]')).not.toBeNull();
      expect(host.querySelector('[aria-label="Enlarge plot"]')).not.toBeNull();
      expect(
        host.querySelector('[aria-label="More panel actions"]')
      ).not.toBeNull();
    });

    it("omits expand for panel kinds that do not support it", () => {
      localStorage.setItem(
        "ecui.plotState",
        JSON.stringify({
          panels: [decoyPanel(), telemetryPanel()],
          activePanelId: "panel-0",
        })
      );
      const host = document.createElement("div");
      document.body.appendChild(host);
      const root = createRoot(host);
      mountedRoots.push({ root, host });
      act(() =>
        root.render(
          createElement(
            MantineProvider,
            null,
            createElement(
              TelemetryProvider,
              null,
              createElement(
                StreamAnalysisProvider,
                null,
                createElement(
                  PanelsProvider,
                  null,
                  createElement(
                    DndContext,
                    null,
                    createElement(SortableContext, {
                      items: ["panel:panel-1"],
                      children: createElement(PanelCard, {
                        panel: telemetryPanel(),
                        streamWorkspaceOptions: [],
                        yAxisDraftInvalid: false,
                        streamWsConnected: true,
                        streamAnalysisWsConnected: true,
                        activeUiDrag: null,
                        helpers: {
                          ...makeHelpers(),
                          isExpandablePlotPanel: () => false,
                        },
                        handlers: makeHandlers(),
                      }),
                    })
                  )
                )
              )
            )
          )
        )
      );
      expect(host.querySelector('[aria-label="Enlarge plot"]')).toBeNull();
    });
  });

  describe("activation", () => {
    it("activates a telemetry card when its background is clicked", () => {
      const host = renderCard(telemetryPanel(), makeHandlers(), null);
      const title = host.querySelector(".panel-card-title");
      expect(title).not.toBeNull();
      pointerClick(title!);
      expect(activeIdProbe).toBe("panel-1");
    });

    it("does not activate from a click on an interactive child", () => {
      const host = renderCard(telemetryPanel(), makeHandlers(), null);
      const settings = host.querySelector('[aria-label="Panel settings"]');
      expect(settings).not.toBeNull();
      pointerClick(settings!);
      expect(activeIdProbe).not.toBe("panel-1");
    });

    it("does not activate when the pointer travelled — that is a drag", () => {
      const host = renderCard(telemetryPanel(), makeHandlers(), null);
      const title = host.querySelector(".panel-card-title");
      pointerClick(title!, { x: 10, y: 10 }, { x: 90, y: 60 });
      expect(activeIdProbe).not.toBe("panel-1");
    });

    it("never activates a stream card — active state is telemetry-only", () => {
      const host = renderCard(binStatsPanel(), makeHandlers(), null);
      const title = host.querySelector(".panel-card-title");
      expect(title).not.toBeNull();
      pointerClick(title!);
      expect(activeIdProbe).not.toBe("panel-2");
    });
  });

  describe("rename", () => {
    it("starts editing on a double-click of the title", () => {
      const startPanelTitleEdit = vi.fn();
      const host = renderCard(
        telemetryPanel(),
        makeHandlers({ startPanelTitleEdit })
      );
      const title = host.querySelector(".panel-card-title");
      act(() => {
        title!.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
      });
      expect(startPanelTitleEdit).toHaveBeenCalledTimes(1);
    });

    it("swaps the title for an editor while a rename is in progress", () => {
      const host = renderCard(telemetryPanel());
      expect(host.querySelector('[aria-label="Panel title"]')).toBeNull();

      act(() => startEditingProbe("panel-1"));

      expect(host.querySelector('[aria-label="Panel title"]')).not.toBeNull();
      expect(host.querySelector('[aria-label="Save title"]')).not.toBeNull();
      expect(host.querySelector('[aria-label="Cancel rename"]')).not.toBeNull();
      expect(host.querySelector(".panel-card-title")).toBeNull();
    });

    it("cancels the rename on Escape, leaving the title untouched", () => {
      const cancelPanelTitleEdit = vi.fn();
      const host = renderCard(
        telemetryPanel(),
        makeHandlers({ cancelPanelTitleEdit })
      );
      act(() => startEditingProbe("panel-1"));
      const input = host.querySelector<HTMLInputElement>(
        '[aria-label="Panel title"]'
      );
      expect(input).not.toBeNull();
      act(() => {
        input!.dispatchEvent(
          new KeyboardEvent("keydown", { key: "Escape", bubbles: true })
        );
      });
      expect(cancelPanelTitleEdit).toHaveBeenCalledTimes(1);
    });

    it("commits the rename on Enter", () => {
      const commitPanelTitleEdit = vi.fn();
      const host = renderCard(
        telemetryPanel(),
        makeHandlers({ commitPanelTitleEdit })
      );
      act(() => startEditingProbe("panel-1"));
      const input = host.querySelector<HTMLInputElement>(
        '[aria-label="Panel title"]'
      );
      act(() => {
        input!.dispatchEvent(
          new KeyboardEvent("keydown", { key: "Enter", bubbles: true })
        );
      });
      expect(commitPanelTitleEdit).toHaveBeenCalledTimes(1);
    });
  });

  describe("layout", () => {
    it("spans extra grid columns when colSpan is set", () => {
      const host = renderCard(telemetryPanel({ colSpan: 2 } as never));
      const card = host.querySelector<HTMLElement>("[data-panel-card-id]");
      expect(card).not.toBeNull();
      expect(card!.style.gridColumn).toBe("span 2");
    });

    it("leaves the grid alone for a single-column panel", () => {
      const host = renderCard(telemetryPanel());
      const card = host.querySelector<HTMLElement>("[data-panel-card-id]");
      expect(card!.style.gridColumn).toBe("");
    });
  });
});
