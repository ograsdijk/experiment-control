import { describe, expect, it } from "vitest";

import { RingBuffer } from "../../utils/ringBuffer";
import type { NormalizedStreamAnalysisOutput } from "../stream/messages";
import type { PlotPanelState, RawStreamSubscription } from "../stream/types";
import {
  applyRawStreamFrameToPanels,
  applyStreamAnalysisOutputToPanels,
  buildPanelsByWorkspaceOutput,
  buildRawPanelsBySubscription,
  pushStreamScalarSample,
  type ApplyHelpersDeps,
} from "./applyToPanels";
import { rawStreamSubscriptionKey } from "../telemetry/rawStreamHydration";

const rawPanel = (
  id: string,
  overrides: Partial<Extract<PlotPanelState, { kind: "stream_raw" }>> = {}
): Extract<PlotPanelState, { kind: "stream_raw" }> => ({
  id,
  title: id,
  kind: "stream_raw",
  sourceMode: "raw",
  stream: { deviceId: "dev", stream: "trace" },
  overlayCount: 1,
  channelIndex: 0,
  extraChannelIndices: [2],
  workspaceId: "workspace",
  outputId: null,
  overlayOutputIds: [],
  traceDecimator: "minmax",
  traceMaxPoints: 2048,
  traceMaxFps: 20,
  rollingWindow: 1,
  averageMode: "rolling",
  yScaleMode: "auto",
  yMin: null,
  yMax: null,
  ...overrides,
});

function deps(panels: PlotPanelState[]): ApplyHelpersDeps {
  return {
    panelsRef: { current: panels },
    panelsByWorkspaceOutputRef: { current: buildPanelsByWorkspaceOutput(panels) },
    rawPanelsBySubscriptionRef: { current: buildRawPanelsBySubscription(panels) },
    buffersRef: new Map(),
    streamFramesRef: new Map(),
    streamTraceOverlayRef: new Map(),
    streamExtraChannelRef: new Map(),
    streamBinStatsOverlayRef: new Map(),
    streamBinStatsFitOverlayRef: new Map(),
    streamParamsLatestRef: new Map(),
    streamBinStatsRef: new Map(),
    streamBin2dRef: new Map(),
  };
}

const analysis = {
  traceStartIdx: 0,
  traceStopIdx: null,
  backgroundEnabled: false,
  backgroundStartIdx: 0,
  backgroundStopIdx: 1,
};

function output(
  outputId: string,
  kind: string,
  value: unknown
): NormalizedStreamAnalysisOutput {
  return {
    workspaceId: "workspace",
    outputId,
    kind,
    value,
    seq: 1,
    tWallS: 10,
    contextFields: null,
    encoding: "json",
    dtype: null,
    byteLength: null,
    truncated: false,
    originalShape: [],
    originalPointCount: null,
    maxPayloadPoints: null,
  };
}

describe("pushStreamScalarSample", () => {
  it("does not append the same timestamp from overlapping snapshots", () => {
    const buffer = new RingBuffer(20);

    expect(pushStreamScalarSample(buffer, 10, 2.5)).toBe(true);
    expect(pushStreamScalarSample(buffer, 10, 2.5)).toBe(false);
    expect(buffer.toArrays()).toEqual([[10], [2.5]]);
  });

  it("continues appending newer samples", () => {
    const buffer = new RingBuffer(20);

    pushStreamScalarSample(buffer, 10, 2.5);
    expect(pushStreamScalarSample(buffer, 11, 3.5)).toBe(true);
    expect(buffer.toArrays()).toEqual([
      [10, 11],
      [2.5, 3.5],
    ]);
  });
});

describe("raw stream reverse index", () => {
  it("keys primary and extra channels by every subscription setting", () => {
    const panel = rawPanel("A");
    const index = buildRawPanelsBySubscription([
      panel,
      rawPanel("B", { traceMaxPoints: 1024 }),
    ]);
    const subscription: RawStreamSubscription = {
      deviceId: "dev",
      stream: "trace",
      channelIndex: 2,
      traceDecimator: "minmax",
      traceMaxPoints: 2048,
      traceMaxFps: 20,
      rollingWindow: 1,
      averageMode: "rolling",
    };
    expect(index.get(rawStreamSubscriptionKey(subscription))?.map((p) => p.id)).toEqual(["A"]);
  });

  it("updates and dirties only exact matching panels", () => {
    const panels = [rawPanel("A"), rawPanel("B", { traceMaxFps: 10 })];
    const state = deps(panels);
    const subscription: RawStreamSubscription = {
      deviceId: "dev",
      stream: "trace",
      channelIndex: 0,
      traceDecimator: "minmax",
      traceMaxPoints: 2048,
      traceMaxFps: 20,
      rollingWindow: 1,
      averageMode: "rolling",
    };
    const dirty = applyRawStreamFrameToPanels(state, subscription, {
      seq: 1,
      shape: [3],
      values: [1, 2, 3],
    });
    expect([...dirty]).toEqual(["A"]);
    expect(state.streamFramesRef.has("A")).toBe(true);
    expect(state.streamFramesRef.has("B")).toBe(false);
  });
});

describe("stream-analysis reverse routing", () => {
  const panels: PlotPanelState[] = [
    {
      id: "scalar",
      title: "scalar",
      kind: "stream_scalar",
      workspaceId: "workspace",
      outputId: "scalar_out",
      stream: null,
      channelIndex: 0,
      analysis,
      timeWindowS: 60,
      yScaleMode: "auto",
      yMin: null,
      yMax: null,
    },
    {
      id: "params",
      title: "params",
      kind: "stream_params",
      workspaceId: "workspace",
      outputIds: ["params_out"],
    },
    {
      ...rawPanel("trace", {
        sourceMode: "dag",
        stream: null,
        outputId: "trace_out",
        overlayOutputIds: [],
        extraChannelIndices: [],
      }),
    },
    {
      id: "bin",
      title: "bin",
      kind: "stream_bin_stats",
      workspaceId: "workspace",
      outputId: "hist_out",
      overlayOutputIds: ["overlay_out"],
      fitOverlayOutputIds: ["fit_out"],
      stream: null,
      channelIndex: 0,
      analysis,
      binStats: {
        contextField: "x",
        xMin: 0,
        xMax: 1,
        binCount: 2,
        autoRange: false,
      },
      uncertaintyMode: "sem",
      uncertaintyScale: 1,
      showBinMarkers: false,
      xOffset: 0,
      xScale: 1,
      yScaleMode: "auto",
      yMin: null,
      yMax: null,
    },
    {
      id: "bin2d",
      title: "bin2d",
      kind: "stream_bin2d",
      workspaceId: "workspace",
      outputId: "hist2d_out",
      reducer: "mean",
      yScaleMode: "auto",
      yMin: null,
      yMax: null,
    },
  ];

  it.each([
    ["scalar", output("scalar_out", "scalar", 2)],
    ["params", output("params_out", "params_map", { center: { value: 1 } })],
    ["trace", output("trace_out", "trace", [1, 2])],
    [
      "bin",
      output("hist_out", "hist_agg", {
        x_bins: [0], mean: [1], std: [0], sem: [0], count: [1],
      }),
    ],
    ["bin", output("overlay_out", "trace", [1, 2])],
    ["bin", output("fit_out", "fit_1d", { x: [0], yhat: [1] })],
    [
      "bin2d",
      output("hist2d_out", "hist2d", {
        x_bins: [0], y_bins: [0], count: [[1]], sum: [[1]], mean: [[1]],
        std: [[0]], sem: [[0]], min: [[1]], max: [[1]],
      }),
    ],
  ])("dirties only %s for its matching output", (panelId, message) => {
    expect([...applyStreamAnalysisOutputToPanels(deps(panels), message)]).toEqual([
      panelId,
    ]);
  });
});
