import { describe, expect, it } from "vitest";
import { normalizePlotState } from "./plot_state";

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
});
