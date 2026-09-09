import { describe, expect, it } from "vitest";
import { normalizePlotState, serializePlotState } from "./plot_state";

describe("plot_state normalizePlotState telemetry smoothing", () => {
  it("applies smoothing defaults for fallback telemetry panel", () => {
    const state = normalizePlotState(null, { defaultWindowS: 60 });
    expect(state.panels).toHaveLength(1);
    const panel = state.panels[0];
    expect(panel.kind).toBe("telemetry");
    if (panel.kind === "telemetry") {
      expect(panel.smoothingMode).toBe("none");
      expect(panel.smoothingWindowS).toBe(5);
    }
  });

  it("normalizes smoothing mode and window for telemetry panels", () => {
    const state = normalizePlotState(
      {
        panels: [
          {
            id: "panel-1",
            title: "Panel",
            kind: "telemetry",
            traces: [],
            timeWindowS: 30,
            smoothingMode: "unknown",
            smoothingWindowS: -12,
          },
        ],
        activePanelId: "panel-1",
      },
      { defaultWindowS: 60 }
    );
    const panel = state.panels[0];
    expect(panel.kind).toBe("telemetry");
    if (panel.kind === "telemetry") {
      expect(panel.smoothingMode).toBe("none");
      expect(panel.smoothingWindowS).toBe(1);
    }
  });

  it("preserves DAG trace output selections", () => {
    const state = normalizePlotState({
      panels: [
        {
          id: "panel-1",
          title: "Trace",
          kind: "stream_raw",
          sourceMode: "dag",
          workspaceId: "detection_fluorescence",
          outputId: "fluorescence_trace",
          overlayOutputIds: ["absorption_trace"],
          stream: { deviceId: "pxie5171", stream: "waveforms", shape: [5, 4096] },
          overlayCount: 4,
          channelIndex: 1,
          traceDecimator: "lttb",
          traceMaxPoints: 4096,
          traceMaxFps: 20,
          rollingWindow: 1,
          averageMode: "latest",
        },
      ],
      activePanelId: "panel-1",
    });

    const panel = state.panels[0];
    expect(panel.kind).toBe("stream_raw");
    if (panel.kind === "stream_raw") {
      expect(panel.sourceMode).toBe("dag");
      expect(panel.workspaceId).toBe("detection_fluorescence");
      expect(panel.outputId).toBe("fluorescence_trace");
      expect(panel.overlayOutputIds).toEqual(["absorption_trace"]);
    }

    const serialized = serializePlotState(state);
    const roundTrip = normalizePlotState(serialized);
    const roundTripPanel = roundTrip.panels[0];
    expect(roundTripPanel.kind).toBe("stream_raw");
    if (roundTripPanel.kind === "stream_raw") {
      expect(roundTripPanel.sourceMode).toBe("dag");
      expect(roundTripPanel.outputId).toBe("fluorescence_trace");
      expect(roundTripPanel.overlayOutputIds).toEqual(["absorption_trace"]);
    }
  });

  it("preserves multi-channel raw stream selections", () => {
    const state = normalizePlotState({
      panels: [
        {
          id: "panel-1",
          title: "Trace",
          kind: "stream_raw",
          sourceMode: "raw",
          workspaceId: "detection_fluorescence",
          outputId: null,
          overlayOutputIds: [],
          extraChannelIndices: [1, 3, "2"],
          stream: { deviceId: "pxie5171", stream: "waveforms", shape: [5, 4096] },
          overlayCount: 4,
          channelIndex: 0,
          traceDecimator: "lttb",
          traceMaxPoints: 4096,
          traceMaxFps: 20,
          rollingWindow: 1,
          averageMode: "latest",
        },
      ],
      activePanelId: "panel-1",
    });

    const panel = state.panels[0];
    expect(panel.kind).toBe("stream_raw");
    if (panel.kind === "stream_raw") {
      expect(panel.channelIndex).toBe(0);
      expect(panel.extraChannelIndices).toEqual([1, 3, 2]);
    }

    const serialized = serializePlotState(state);
    const roundTrip = normalizePlotState(serialized);
    const roundTripPanel = roundTrip.panels[0];
    expect(roundTripPanel.kind).toBe("stream_raw");
    if (roundTripPanel.kind === "stream_raw") {
      expect(roundTripPanel.extraChannelIndices).toEqual([1, 3, 2]);
    }
  });

  it("defaults extraChannelIndices to empty for legacy raw panels", () => {
    const state = normalizePlotState({
      panels: [
        {
          id: "panel-1",
          title: "Trace",
          kind: "stream_raw",
          sourceMode: "raw",
          workspaceId: "detection_fluorescence",
          outputId: null,
          overlayOutputIds: [],
          stream: { deviceId: "pxie5171", stream: "waveforms", shape: [5, 4096] },
          overlayCount: 4,
          channelIndex: 2,
          traceDecimator: "lttb",
          traceMaxPoints: 4096,
          traceMaxFps: 20,
          rollingWindow: 1,
          averageMode: "latest",
        },
      ],
      activePanelId: "panel-1",
    });

    const panel = state.panels[0];
    expect(panel.kind).toBe("stream_raw");
    if (panel.kind === "stream_raw") {
      expect(panel.extraChannelIndices).toEqual([]);
    }
  });

  it("preserves DAG scalar output selection", () => {
    const state = normalizePlotState({
      panels: [
        {
          id: "panel-1",
          title: "Scalar",
          kind: "stream_scalar",
          workspaceId: "detection_fluorescence",
          outputId: "absorption_signal",
          stream: { deviceId: "pxie5171", stream: "waveforms", shape: [5, 4096] },
          channelIndex: 3,
          timeWindowS: 120,
        },
      ],
      activePanelId: "panel-1",
    });

    const panel = state.panels[0];
    expect(panel.kind).toBe("stream_scalar");
    if (panel.kind === "stream_scalar") {
      expect(panel.workspaceId).toBe("detection_fluorescence");
      expect(panel.outputId).toBe("absorption_signal");
    }

    const serialized = serializePlotState(state);
    const roundTrip = normalizePlotState(serialized);
    const roundTripPanel = roundTrip.panels[0];
    expect(roundTripPanel.kind).toBe("stream_scalar");
    if (roundTripPanel.kind === "stream_scalar") {
      expect(roundTripPanel.outputId).toBe("absorption_signal");
    }
  });

  it("normalizes and preserves stream bin stats x-axis transforms", () => {
    const state = normalizePlotState({
      panels: [{
        id: "panel-1", title: "Bin stats", kind: "stream_bin_stats",
        workspaceId: "detection_fluorescence", outputId: "bin_stats",
        overlayOutputIds: [], fitOverlayOutputIds: [],
        stream: { deviceId: "pxie5171", stream: "waveforms", shape: [5, 4096] },
        binStats: {}, uncertaintyMode: "sem", uncertaintyScale: 1,
        showBinMarkers: false, xOffset: -2.5, xScale: 1000,
      }],
      activePanelId: "panel-1",
    });

    const panel = state.panels[0];
    expect(panel.kind).toBe("stream_bin_stats");
    if (panel.kind === "stream_bin_stats") {
      expect([panel.xOffset, panel.xScale]).toEqual([-2.5, 1000]);
    }

    const roundTripPanel = normalizePlotState(serializePlotState(state)).panels[0];
    expect(roundTripPanel.kind).toBe("stream_bin_stats");
    if (roundTripPanel.kind === "stream_bin_stats") {
      expect([roundTripPanel.xOffset, roundTripPanel.xScale]).toEqual([-2.5, 1000]);
    }
  });

  it("uses identity x-axis transforms for invalid bin stats values", () => {
    const state = normalizePlotState({
      panels: [{
        id: "panel-1", title: "Bin stats", kind: "stream_bin_stats",
        workspaceId: null, outputId: null, overlayOutputIds: [],
        fitOverlayOutputIds: [], stream: null, binStats: {},
        uncertaintyMode: "sem", uncertaintyScale: 1, showBinMarkers: false,
        xOffset: "invalid", xScale: 0,
      }],
      activePanelId: "panel-1",
    });

    const panel = state.panels[0];
    expect(panel.kind).toBe("stream_bin_stats");
    if (panel.kind === "stream_bin_stats") {
      expect([panel.xOffset, panel.xScale]).toEqual([0, 1]);
    }
  });
});

describe("plot_state panel card layout", () => {
  const panelWith = (extra: Record<string, unknown>) =>
    normalizePlotState(
      {
        panels: [
          {
            id: "panel-1",
            title: "Panel",
            kind: "telemetry",
            traces: [],
            timeWindowS: 30,
            ...extra,
          },
        ],
        activePanelId: "panel-1",
      },
      { defaultWindowS: 60 }
    ).panels[0];

  it("leaves layout absent for panels saved before card sizing existed", () => {
    const panel = panelWith({});
    expect(panel.heightPx).toBeUndefined();
    expect(panel.colSpan).toBeUndefined();
  });

  it("clamps a pinned plot height into a usable range", () => {
    expect(panelWith({ heightPx: 5 }).heightPx).toBe(160);
    expect(panelWith({ heightPx: 99999 }).heightPx).toBe(1200);
    expect(panelWith({ heightPx: 380.4 }).heightPx).toBe(380);
  });

  it("drops a nonsense height rather than pinning one", () => {
    expect(panelWith({ heightPx: "tall" }).heightPx).toBeUndefined();
    expect(panelWith({ heightPx: Number.NaN }).heightPx).toBeUndefined();
  });

  it("keeps colSpan only when it actually spans", () => {
    expect(panelWith({ colSpan: 1 }).colSpan).toBeUndefined();
    expect(panelWith({ colSpan: 0 }).colSpan).toBeUndefined();
    expect(panelWith({ colSpan: 2 }).colSpan).toBe(2);
    expect(panelWith({ colSpan: 40 }).colSpan).toBe(4);
  });

  it("round-trips layout through serialize", () => {
    const state = normalizePlotState(
      {
        panels: [
          {
            id: "panel-1",
            title: "Panel",
            kind: "telemetry",
            traces: [],
            timeWindowS: 30,
            heightPx: 420,
            colSpan: 2,
          },
        ],
        activePanelId: "panel-1",
      },
      { defaultWindowS: 60 }
    );
    const round = normalizePlotState(serializePlotState(state), {
      defaultWindowS: 60,
    });
    expect(round.panels[0].heightPx).toBe(420);
    expect(round.panels[0].colSpan).toBe(2);
  });
});

describe("panel naming", () => {
  const panelWith = (extra: Record<string, unknown>) =>
    normalizePlotState(
      {
        panels: [
          {
            id: "panel-1",
            title: "Panel",
            kind: "telemetry",
            traces: [],
            timeWindowS: 30,
            ...extra,
          },
        ],
        activePanelId: "panel-1",
      },
      { defaultWindowS: 60 }
    ).panels[0];

  it("leaves naming absent for panels that carry none", () => {
    const panel = panelWith({});
    expect(panel.seriesLabels).toBeUndefined();
    expect(panel.titleAuto).toBeUndefined();
  });

  it("keeps only non-empty string renames", () => {
    const panel = panelWith({
      seriesLabels: {
        "dev:signal": " PMT 1 ",
        blank: "   ",
        numeric: 4,
        "": "orphan",
      },
    });
    expect(panel.seriesLabels).toEqual({ "dev:signal": "PMT 1" });
  });

  it("drops a seriesLabels value that is not an object", () => {
    expect(panelWith({ seriesLabels: "nope" }).seriesLabels).toBeUndefined();
    expect(panelWith({ seriesLabels: ["a"] }).seriesLabels).toBeUndefined();
  });

  it("treats anything but an explicit true as a user-set title", () => {
    expect(panelWith({ titleAuto: true }).titleAuto).toBe(true);
    expect(panelWith({ titleAuto: "yes" }).titleAuto).toBeUndefined();
    expect(panelWith({ titleAuto: false }).titleAuto).toBeUndefined();
  });

  it("round-trips naming through serialize", () => {
    const state = normalizePlotState(
      {
        panels: [
          {
            id: "panel-1",
            title: "Panel",
            kind: "telemetry",
            traces: [],
            timeWindowS: 30,
            seriesLabels: { "dev:signal": "PMT 1" },
            titleAuto: true,
          },
        ],
        activePanelId: "panel-1",
      },
      { defaultWindowS: 60 }
    );
    const round = normalizePlotState(serializePlotState(state), {
      defaultWindowS: 60,
    });
    expect(round.panels[0].seriesLabels).toEqual({ "dev:signal": "PMT 1" });
    expect(round.panels[0].titleAuto).toBe(true);
  });
});
