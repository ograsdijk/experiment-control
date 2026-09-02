// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";


vi.mock("@mantine/core", async () => {
  const React = await import("react");
  return {
    Alert: ({ children }: { children?: React.ReactNode }) =>
      React.createElement("div", null, children),
  };
});

const uPlotMocks = vi.hoisted(() => ({
  construct: vi.fn(),
  setData: vi.fn(),
  destroy: vi.fn(),
  setSize: vi.fn(),
}));

vi.mock("uplot", () => {
  class MockUPlot {
    data: unknown;
    cursor = { idx: null, left: null, top: null };
    bbox = { left: 0, top: 0, width: 600, height: 320 };
    width = 600;
    height = 320;

    constructor(_opts: unknown, data: unknown, _host: HTMLElement) {
      this.data = data;
      uPlotMocks.construct();
    }

    setData(data: unknown) {
      this.data = data;
      uPlotMocks.setData();
    }

    destroy() {
      uPlotMocks.destroy();
    }

    setSize(_size: unknown) {
      uPlotMocks.setSize();
    }
  }

  return { default: MockUPlot };
});

import {
  StreamBinStatsPanel,
  type StreamBinStatsSeries,
} from "./StreamBinStatsPanel";
import { StreamRawPanel, type StreamFrame } from "./StreamRawPanel";

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

let root: Root;
let container: HTMLDivElement;

function render(element: ReturnType<typeof createElement>) {
  act(() => {
    root.render(element);
  });
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("ResizeObserver", ResizeObserverMock);
  uPlotMocks.construct.mockClear();
  uPlotMocks.setData.mockClear();
  uPlotMocks.destroy.mockClear();
  uPlotMocks.setSize.mockClear();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe("persistent stream uPlot lifecycle", () => {
  it("updates raw stream data across ticks without reconstructing uPlot", () => {
    const frames: StreamFrame[] = [
      { seq: 1, shape: [3], values: [1, 2, 3] },
    ];
    const baseProps = {
      frames,
      overlayCount: 1,
      channelIndex: 0,
      colorScheme: "light" as const,
    };

    render(createElement(StreamRawPanel, { ...baseProps, tick: 0 }));
    expect(uPlotMocks.construct).toHaveBeenCalledTimes(1);

    uPlotMocks.setData.mockClear();
    uPlotMocks.destroy.mockClear();
    for (let tick = 1; tick <= 25; tick += 1) {
      frames.push({
        seq: tick + 1,
        shape: [3],
        values: [tick, tick + 1, tick + 2],
      });
      render(createElement(StreamRawPanel, { ...baseProps, tick }));
    }

    expect(uPlotMocks.construct).toHaveBeenCalledTimes(1);
    expect(uPlotMocks.setData).toHaveBeenCalledTimes(25);
    expect(uPlotMocks.destroy).not.toHaveBeenCalled();

    // Changing the number of displayed uPlot series is a real topology change.
    render(
      createElement(StreamRawPanel, {
        ...baseProps,
        overlayCount: 2,
        tick: 26,
      })
    );
    expect(uPlotMocks.construct).toHaveBeenCalledTimes(2);
    expect(uPlotMocks.destroy).toHaveBeenCalledTimes(1);
  });

  it("keeps raw topology stable across invalid live frames", () => {
    const frames: StreamFrame[] = [
      { seq: 1, shape: [3], values: [1, 2, 3] },
      { seq: 2, shape: [3], values: [4, 5, 6] },
    ];
    const baseProps = {
      frames,
      overlayCount: 2,
      channelIndex: 0,
      colorScheme: "light" as const,
    };

    render(createElement(StreamRawPanel, { ...baseProps, tick: 0 }));
    expect(uPlotMocks.construct).toHaveBeenCalledTimes(1);

    uPlotMocks.setData.mockClear();
    frames.push({ seq: 3, shape: [3], values: [], truncated: true });
    render(createElement(StreamRawPanel, { ...baseProps, tick: 1 }));

    expect(uPlotMocks.construct).toHaveBeenCalledTimes(1);
    expect(uPlotMocks.setData).toHaveBeenCalledTimes(1);
  });

  it("updates bin statistics and fit data without reconstructing uPlot", () => {
    const stats: StreamBinStatsSeries = {
      xBins: [0, 1, 2],
      mean: [1, 2, 3],
      std: [0.2, 0.2, 0.2],
      sem: [0.1, 0.1, 0.1],
      count: [5, 5, 5],
    };
    const overlaySeries = [{ label: "reference", values: [1, 1, 1] }];
    const baseProps = {
      series: stats,
      overlaySeries,
      xLabel: "frequency",
      uncertaintyMode: "sem" as const,
      uncertaintyScale: 1,
      colorScheme: "dark" as const,
    };

    render(createElement(StreamBinStatsPanel, { ...baseProps, tick: 0 }));
    expect(uPlotMocks.construct).toHaveBeenCalledTimes(1);

    uPlotMocks.setData.mockClear();
    stats.mean[2] = 4;
    render(
      createElement(StreamBinStatsPanel, {
        ...baseProps,
        fitOverlays: [{ label: "fit", x: [0, 1, 2], y: [1, 2.5, 4] }],
        tick: 1,
      })
    );

    expect(uPlotMocks.construct).toHaveBeenCalledTimes(1);
    expect(uPlotMocks.setData).toHaveBeenCalledTimes(1);

    // Temporary loss of valid bins must not change the configured series topology.
    stats.count.fill(0);
    render(createElement(StreamBinStatsPanel, { ...baseProps, tick: 2 }));
    expect(uPlotMocks.construct).toHaveBeenCalledTimes(1);

    stats.count.fill(5);
    render(
      createElement(StreamBinStatsPanel, {
        ...baseProps,
        overlaySeries: [
          ...overlaySeries,
          { label: "second", values: [2, 2, 2] },
        ],
        tick: 3,
      })
    );
    expect(uPlotMocks.construct).toHaveBeenCalledTimes(2);
  });
});
