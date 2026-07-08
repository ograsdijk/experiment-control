import { describe, expect, it } from "vitest";

import type {
  PlotPanelState,
  PlotStreamPanelState,
  RawStreamSubscription,
} from "../stream/types";
import {
  prepareRawStreamHydration,
  rawStreamSubscriptionKey,
  rawStreamSubscriptionKeysForPanel,
} from "./rawStreamHydration";

const baseRawPanel: PlotStreamPanelState = {
  id: "panel-a",
  title: "Stream",
  kind: "stream_raw",
  sourceMode: "raw",
  stream: { deviceId: "dev-a", stream: "samples" },
  overlayCount: 4,
  channelIndex: 0,
  extraChannelIndices: [],
  workspaceId: "",
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

function subscription(
  overrides: Partial<RawStreamSubscription> = {}
): RawStreamSubscription {
  return {
    deviceId: "dev-a",
    stream: "samples",
    channelIndex: 0,
    traceDecimator: "minmax",
    traceMaxPoints: 1200,
    traceMaxFps: 10,
    rollingWindow: 5,
    averageMode: "block",
    ...overrides,
  };
}

describe("rawStreamSubscriptionKeysForPanel", () => {
  it("builds one normalized key for a single-channel raw panel", () => {
    expect(rawStreamSubscriptionKeysForPanel(baseRawPanel)).toEqual([
      "dev-a|samples|0|minmax|1200|10.000|5|block",
    ]);
  });

  it("includes deduplicated extra channels for multi-channel raw panels", () => {
    expect(
      rawStreamSubscriptionKeysForPanel({
        ...baseRawPanel,
        channelIndex: 2,
        extraChannelIndices: [1, 2, 1.9],
      })
    ).toEqual([
      "dev-a|samples|2|minmax|1200|10.000|5|block",
      "dev-a|samples|1|minmax|1200|10.000|5|block",
    ]);
  });

  it("returns no keys for non-raw or unbound panels", () => {
    const telemetryPanel: PlotPanelState = {
      id: "telemetry",
      title: "Telemetry",
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
      smoothingWindowS: 5,
    };
    expect(rawStreamSubscriptionKeysForPanel(telemetryPanel)).toEqual([]);
    expect(
      rawStreamSubscriptionKeysForPanel({ ...baseRawPanel, stream: null })
    ).toEqual([]);
  });
});

describe("prepareRawStreamHydration", () => {
  it("only makes invalidated active keys pending", () => {
    const first = subscription({ channelIndex: 0 });
    const second = subscription({ channelIndex: 1 });
    const firstKey = rawStreamSubscriptionKey(first);
    const secondKey = rawStreamSubscriptionKey(second);

    const result = prepareRawStreamHydration(
      [first, second],
      new Set([firstKey, secondKey]),
      new Set([firstKey])
    );

    expect(result.nextHydratedKeys).toEqual(new Set([secondKey]));
    expect(result.pending).toEqual([first]);
  });

  it("prunes inactive hydrated keys without rehydrating them", () => {
    const active = subscription({ channelIndex: 0 });
    const inactive = subscription({ channelIndex: 2 });
    const activeKey = rawStreamSubscriptionKey(active);
    const inactiveKey = rawStreamSubscriptionKey(inactive);

    const result = prepareRawStreamHydration(
      [active],
      new Set([activeKey, inactiveKey]),
      new Set([inactiveKey])
    );

    expect(result.nextHydratedKeys).toEqual(new Set([activeKey]));
    expect(result.pending).toEqual([]);
  });
});
