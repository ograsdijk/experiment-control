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
