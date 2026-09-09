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

// Mantine's Select and MultiSelect each stand up a floating-ui combobox
// that costs seconds per render under jsdom — a bin-stats settings body
// holds four of them. These assertions are about which controls a panel
// kind gets, not about how a combobox positions itself, so the two are
// stubbed down to their label.
vi.mock("@mantine/core", async () => {
  const actual = await vi.importActual<typeof import("@mantine/core")>(
    "@mantine/core"
  );
  const Stub = ({
    label,
    placeholder,
    onDropdownOpen,
    onDropdownClose,
  }: {
    label?: string;
    placeholder?: string;
    onDropdownOpen?: () => void;
    onDropdownClose?: () => void;
  }) =>
    createElement(
      "div",
      {
        "data-combobox": label ?? placeholder ?? "",
        "data-wired": onDropdownOpen && onDropdownClose ? "yes" : "no",
      },
      label ?? placeholder ?? ""
    );
  return { ...actual, Select: Stub, MultiSelect: Stub };
});

import { PanelCard } from "./PanelCard";
import {
  EMPTY_PANEL_SETTINGS_OPTIONS,
  PanelSettingsBody,
} from "./PanelSettings";
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

function rawTracePanel(): PlotPanelState {
  return {
    id: "panel-3",
    title: "Absorption trace",
    kind: "stream_raw",
    sourceMode: "dag",
    stream: null,
    overlayCount: 1,
    channelIndex: 0,
    extraChannelIndices: [],
    workspaceId: "workspace-1",
    outputId: "abs_trace",
    overlayOutputIds: [],
    traceDecimator: "minmax",
    traceMaxPoints: 2000,
    traceMaxFps: 10,
    rollingWindow: 1,
    averageMode: "block",
    yScaleMode: "auto",
    yMin: null,
    yMax: null,
  } as unknown as PlotPanelState;
}

function bin2dPanel(): PlotPanelState {
  return {
    id: "panel-4",
    title: "Scan map",
    kind: "stream_bin2d",
    workspaceId: "workspace-1",
    outputId: "scan_map",
    reducer: "mean",
    yScaleMode: "auto",
    yMin: null,
    yMax: null,
  } as unknown as PlotPanelState;
}

function paramsPanel(): PlotPanelState {
  return {
    id: "panel-5",
    title: "Fit parameters",
    kind: "stream_params",
    workspaceId: "workspace-1",
    outputIds: [],
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
    setPanelSeriesLabel: noop,
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
    setStreamTracePanelSourceMode: noop,
    setStreamTracePanelWorkspace: noop,
    setStreamTracePanelOutput: noop,
    setStreamTracePanelOverlayOutputs: noop,
    setStreamPanelTargetFromKey: noop,
    setStreamPanelChannelIndex: noop,
    setStreamPanelChannels: noop,
    setStreamPanelOverlayCount: noop,
    setStreamPanelRollingWindow: noop,
    setStreamPanelAverageMode: noop,
    setStreamPanelTraceDecimator: noop,
    setStreamPanelTraceMaxPoints: noop,
    setStreamPanelTraceMaxFps: noop,
    setStreamParamsPanelOutputs: noop,
    setStreamBinStatsOverlayOutputs: noop,
    setStreamBinStatsFitOverlayOutputs: noop,
    setStreamBinStatsUncertainty: noop,
    setStreamBinStatsShowBinMarkers: noop,
    setStreamBinStatsXAxisTransform: noop,
    setStreamBin2dReducer: noop,
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

/**
 * Set an input's value the way a user would: React installs its own
 * value setter on the element, so assigning `.value` directly leaves the
 * component's state behind. Going through the prototype setter and then
 * dispatching `input` is what reaches React's onChange.
 */
function setNativeInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value"
  )?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function renderCard(
  panel: PlotPanelState,
  handlers: PanelsGridHandlers = makeHandlers(),
  activePanelId: string | null = null,
  helpers: PanelsGridHelpers = makeHelpers(),
  cardProps: { streamWsConnected?: boolean; streamAnalysisWsConnected?: boolean } = {}
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
                streamTargetOptions: [],
                yAxisDraftInvalid: false,
                streamWsConnected: true,
                streamAnalysisWsConnected: true,
                ...cardProps,
                activeUiDrag: null,
                helpers,
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
                        streamTargetOptions: [],
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

  describe("series names", () => {
    // One overlay is enough to make the legend render: it only appears
    // once a card actually draws more than one curve.
    const overlayHelpers = () => ({
      ...makeHelpers(),
      streamBinStatsOverlaySeries: () => [
        { label: "abs_integral", values: [1, 2] },
      ],
    });

    function renderBinStats(
      panel: PlotPanelState,
      handlers: PanelsGridHandlers = makeHandlers()
    ) {
      return renderCard(panel, handlers, null, overlayHelpers());
    }

    it("derives a readable name from the output id", () => {
      const host = renderBinStats(binStatsPanel());
      const labels = Array.from(
        host.querySelectorAll(".plot-legend-label")
      ).map((node) => node.textContent);
      expect(labels).toContain("Fluorescence integral vs scan");
      expect(labels).toContain("Absorption integral");
    });

    it("prefers the panel's own rename", () => {
      const panel = {
        ...binStatsPanel(),
        seriesLabels: { abs_integral: "Reference PD" },
      } as PlotPanelState;
      const host = renderBinStats(panel);
      const labels = Array.from(
        host.querySelectorAll(".plot-legend-label")
      ).map((node) => node.textContent);
      expect(labels).toContain("Reference PD");
      expect(labels).not.toContain("Absorption integral");
    });

    it("commits a rename typed into the legend", () => {
      const setPanelSeriesLabel = vi.fn();
      const host = renderBinStats(
        binStatsPanel(),
        makeHandlers({ setPanelSeriesLabel })
      );
      const entry = Array.from(
        host.querySelectorAll<HTMLElement>("button.plot-legend-item")
      ).find(
        (node) => node.textContent === "Absorption integral"
      );
      expect(entry).toBeDefined();
      act(() => {
        entry!.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
      });
      const input = host.querySelector<HTMLInputElement>(".plot-legend-input");
      expect(input).not.toBeNull();
      act(() => {
        setNativeInputValue(input!, "Reference PD");
        input!.dispatchEvent(
          new KeyboardEvent("keydown", { key: "Enter", bubbles: true })
        );
      });
      expect(setPanelSeriesLabel).toHaveBeenCalledWith(
        "panel-2",
        "abs_integral",
        "Reference PD"
      );
    });

    it("discards the draft on Escape", () => {
      const setPanelSeriesLabel = vi.fn();
      const host = renderBinStats(
        binStatsPanel(),
        makeHandlers({ setPanelSeriesLabel })
      );
      const entry = Array.from(
        host.querySelectorAll<HTMLElement>("button.plot-legend-item")
      ).find((node) => node.textContent === "Absorption integral");
      act(() => {
        entry!.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
      });
      const input = host.querySelector<HTMLInputElement>(".plot-legend-input");
      act(() => {
        setNativeInputValue(input!, "Reference PD");
        input!.dispatchEvent(
          new KeyboardEvent("keydown", { key: "Escape", bubbles: true })
        );
      });
      expect(setPanelSeriesLabel).not.toHaveBeenCalled();
      expect(host.querySelector(".plot-legend-input")).toBeNull();
    });
  });

  describe("link indicator", () => {
    it("puts the dot in the header and leaves no status row when healthy", () => {
      const host = renderCard(binStatsPanel());
      const header = host.querySelector(".panel-card-header");
      expect(header!.querySelector(".panel-card-header-dot")).not.toBeNull();
      expect(host.querySelector(".panel-card-status")).toBeNull();
    });

    it("says so in words when the link is down", () => {
      const host = renderCard(
        binStatsPanel(),
        makeHandlers(),
        null,
        makeHelpers(),
        { streamAnalysisWsConnected: false }
      );
      const dot = host.querySelector<HTMLElement>(".panel-card-header-dot");
      expect(dot!.getAttribute("aria-label")).toBe("analysis link disconnected");
      const status = host.querySelector(".panel-card-status");
      expect(status).not.toBeNull();
      expect(status!.textContent).toContain("analysis link down");
    });

    it("shows no dot for a telemetry panel, which has no link of its own", () => {
      const host = renderCard(telemetryPanel());
      expect(host.querySelector(".panel-card-header-dot")).toBeNull();
    });
  });
  describe("settings", () => {
    /**
     * The settings body, not the popover around it.
     *
     * Mantine's Popover positions itself through floating-ui, which in
     * jsdom costs tens of seconds per open for nothing this suite is
     * asking about. What matters here is which sections a panel kind
     * gets, and that is the body's job.
     */
    function renderSettings(panel: PlotPanelState): HTMLElement {
      const host = document.createElement("div");
      document.body.appendChild(host);
      const root = createRoot(host);
      mountedRoots.push({ root, host });
      act(() =>
        root.render(
          createElement(
            MantineProvider,
            null,
            createElement(PanelSettingsBody, {
              panel,
              opened: true,
              streamWorkspaceOptions: [],
              streamTargetOptions: [],
              options: EMPTY_PANEL_SETTINGS_OPTIONS,
              yAxisDraftMin: "",
              yAxisDraftMax: "",
              onYAxisDraftMinChange: noop,
              onYAxisDraftMaxChange: noop,
              yAxisAutoRange: null,
              yAxisDraftInvalid: false,
              statusRows: [["analysis link", "connected"]],
              telemetryNumericTraceCount: 0,
              telemetryOffset: null,
              telemetryOffsetLabel: "n/a",
              telemetryOffsetFullLabel: null,
              handlers: makeHandlers(),
            })
          )
        )
      );
      return host;
    }

    it("offers exactly one way in — no advanced-options link", () => {
      const card = renderCard(binStatsPanel());
      expect(
        card.querySelectorAll('[aria-label="Panel settings"]')
      ).toHaveLength(1);
      expect(card.textContent).not.toContain("advanced options");
      expect(renderSettings(binStatsPanel()).textContent).not.toContain(
        "advanced options"
      );
    });

    it("holds everything the bin stats modal used to hold", () => {
      const text = renderSettings(binStatsPanel()).textContent ?? "";
      for (const label of [
        "Source",
        "Workspace",
        "Output",
        "Overlay traces",
        "Overlay fits",
        "Display",
        "Uncertainty",
        "Show sampled bins",
        "x offset",
        "x scale",
        "Card",
        "Status",
      ]) {
        expect(text).toContain(label);
      }
    });

    it("files the bin stats x calibration under Advanced", () => {
      const host = renderSettings(binStatsPanel());
      const collapsed = collapsedSection(host);
      expect(collapsed.textContent).toContain("x offset");
      expect(collapsed.textContent).toContain("x scale");
      // Everyday controls stay out in the open.
      expect(collapsed.textContent).not.toContain("Uncertainty");
    });

    /** Mantine keeps a collapsed section mounted at zero height. */
    function collapsedSection(host: HTMLElement): HTMLElement {
      const zero = Array.from(
        host.querySelectorAll<HTMLElement>("div")
      ).filter((node) => node.style.height === "0px");
      expect(zero).toHaveLength(1);
      return zero[0];
    }

    it("keeps only the set-once trace knobs under Advanced", () => {
      const host = renderSettings(rawTracePanel());
      const collapsed = collapsedSection(host);
      for (const label of ["Decimator", "Max points", "Max Hz"]) {
        expect(collapsed.textContent).toContain(label);
      }

      const toggle = host.querySelector<HTMLElement>("[aria-expanded]");
      expect(toggle!.getAttribute("aria-expanded")).toBe("false");
      act(() => {
        toggle!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      expect(toggle!.getAttribute("aria-expanded")).toBe("true");
    });

    it("leaves overlay depth and averaging out in the open", () => {
      const host = renderSettings(rawTracePanel());
      const collapsed = collapsedSection(host);
      // These are reached for while watching a trace, so they must not be
      // behind the Advanced toggle.
      for (const label of ["Overlay N", "Average N", "Rolling"]) {
        expect(host.textContent).toContain(label);
        expect(collapsed.textContent).not.toContain(label);
      }
    });

    it("labels the overlay depth as rows on a waterfall", () => {
      const panel = {
        ...(rawTracePanel() as object),
        id: "panel-6",
        kind: "stream_waterfall",
      } as PlotPanelState;
      expect(renderSettings(panel).textContent).toContain("Rows");
    });

    it("gives a raw-source trace panel its stream picker, not a workspace", () => {
      const panel = {
        ...(rawTracePanel() as object),
        sourceMode: "raw",
      } as PlotPanelState;
      const text = renderSettings(panel).textContent ?? "";
      expect(text).toContain("Stream");
      expect(text).not.toContain("Workspace");
    });

    it("gives the 2D bins panel its reducer", () => {
      const text = renderSettings(bin2dPanel()).textContent ?? "";
      expect(text).toContain("Reducer");
      // Z, not Y: the manual range on a heatmap is the colour scale.
      expect(text).toContain("Z axis");
    });

    it("lets every combobox report its popup state", () => {
      // Mantine's Popover dismisses on any mousedown outside its own two
      // nodes, and a combobox popup is portalled to the body. Without
      // these callbacks reaching the shell, picking an overlay output
      // closes the settings surface and a multi-select can never take a
      // second value.
      for (const panel of [
        binStatsPanel(),
        rawTracePanel(),
        bin2dPanel(),
        paramsPanel(),
      ]) {
        const host = renderSettings(panel);
        const comboboxes = Array.from(
          host.querySelectorAll<HTMLElement>("[data-combobox]")
        );
        expect(comboboxes.length).toBeGreaterThan(0);
        const unwired = comboboxes
          .filter((node) => node.getAttribute("data-wired") !== "yes")
          .map((node) => node.getAttribute("data-combobox"));
        expect(unwired).toEqual([]);
      }
    });

    it("gives the params panel its outputs and no axis controls", () => {
      const text = renderSettings(paramsPanel()).textContent ?? "";
      expect(text).toContain("Outputs");
      expect(text).not.toContain("Y axis");
      expect(text).toContain("Card");
    });
  });
});
