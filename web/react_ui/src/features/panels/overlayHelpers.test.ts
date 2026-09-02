import { describe, expect, it } from "vitest";

import type {
  PlotStreamBinStatsPanelState,
  PlotStreamPanelState,
} from "../stream/types";
import {
  streamBinStatsOverlaySeries,
  streamExtraChannelSeries,
  streamTraceOverlaySeries,
} from "./overlayHelpers";

const baseRawPanel: PlotStreamPanelState = {
  id: "raw-panel",
  title: "Raw",
  kind: "stream_raw",
  sourceMode: "raw",
  stream: { deviceId: "dev-a", stream: "samples" },
  overlayCount: 1,
  channelIndex: 0,
  extraChannelIndices: [],
  workspaceId: "workspace-a",
  outputId: null,
  overlayOutputIds: [],
  traceDecimator: "minmax",
  traceMaxPoints: 1200,
  traceMaxFps: 10,
  rollingWindow: 5,
  averageMode: "block",
  yScaleMode: "auto",
  yMin: null,
  yMax: null,
};

const baseBinStatsPanel: PlotStreamBinStatsPanelState = {
  id: "bin-panel",
  title: "Bins",
  kind: "stream_bin_stats",
  workspaceId: "workspace-a",
  outputId: "bins",
  overlayOutputIds: [],
  fitOverlayOutputIds: [],
  stream: { deviceId: "dev-a", stream: "samples" },
  channelIndex: 0,
  analysis: {
    traceStartIdx: 0,
    traceStopIdx: null,
    backgroundEnabled: false,
    backgroundStartIdx: 0,
    backgroundStopIdx: 0,
  },
  binStats: {
    contextField: "frequency",
    xMin: 0,
    xMax: 1,
    binCount: 10,
    autoRange: true,
  },
  uncertaintyMode: "sem",
  uncertaintyScale: 1,
  showBinMarkers: false,
  xOffset: 0,
  xScale: 1,
  yScaleMode: "auto",
  yMin: null,
  yMax: null,
};

describe("stream overlay helpers", () => {
  it("preserves configured DAG trace overlay slots before data arrives", () => {
    const panel: PlotStreamPanelState = {
      ...baseRawPanel,
      sourceMode: "dag",
      stream: null,
      outputId: "main",
      overlayOutputIds: ["reference-a", "reference-b"],
    };
    const overlays = new Map<
      string,
      Map<string, { seq: number; values: number[] }>
    >();

    expect(streamTraceOverlaySeries(panel, overlays)).toEqual([
      { label: "reference-a", values: [] },
      { label: "reference-b", values: [] },
    ]);

    overlays.set(
      panel.id,
      new Map([["reference-b", { seq: 1, values: [4, 5, 6] }]])
    );
    expect(streamTraceOverlaySeries(panel, overlays)).toEqual([
      { label: "reference-a", values: [] },
      { label: "reference-b", values: [4, 5, 6] },
    ]);
  });

  it("preserves configured raw extra-channel slots across cache availability", () => {
    const panel: PlotStreamPanelState = {
      ...baseRawPanel,
      extraChannelIndices: [1, 2],
    };
    const extras = new Map<
      string,
      Map<number, { seq: number; values: number[] }>
    >();

    expect(streamExtraChannelSeries(panel, extras)).toEqual([
      { label: "ch 1", values: [] },
      { label: "ch 2", values: [] },
    ]);

    extras.set(panel.id, new Map([[1, { seq: 3, values: [1, 2, 3] }]]));
    expect(streamExtraChannelSeries(panel, extras)).toEqual([
      { label: "ch 1", values: [1, 2, 3] },
      { label: "ch 2", values: [] },
    ]);
  });

  it("preserves configured bin-stat overlay slots across cache availability", () => {
    const panel: PlotStreamBinStatsPanelState = {
      ...baseBinStatsPanel,
      overlayOutputIds: ["reference-a", "reference-b"],
    };
    const overlays = new Map<
      string,
      Map<string, { seq: number; values: number[] }>
    >();

    expect(streamBinStatsOverlaySeries(panel, overlays)).toEqual([
      { label: "reference-a", values: [] },
      { label: "reference-b", values: [] },
    ]);

    overlays.set(
      panel.id,
      new Map([["reference-a", { seq: 4, values: [7, 8, 9] }]])
    );
    expect(streamBinStatsOverlaySeries(panel, overlays)).toEqual([
      { label: "reference-a", values: [7, 8, 9] },
      { label: "reference-b", values: [] },
    ]);
  });
});
