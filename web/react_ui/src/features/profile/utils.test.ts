import { describe, expect, it } from "vitest";
import { normalizeUiProfile } from "./utils";
import type { PlotState } from "./types";

const normalizePlotState = (_raw: unknown): PlotState => ({
  panels: [],
  activePanelId: "",
  nextPanelId: 1,
});

describe("profile normalizeUiProfile", () => {
  it("defaults devicePanelCollapsed to false when absent", () => {
    const profile = normalizeUiProfile(
      {
        layout: { nav_width: 380 },
      },
      {
        defaultNavWidth: 360,
        navMin: 260,
        navMax: 900,
        normalizePlotState,
        normalizeStreamWorkspaceRecord: () => ({}),
      }
    );
    expect(profile).not.toBeNull();
    expect(profile?.devicePanelCollapsed).toBe(false);
  });

  it("reads snake_case device_panel_collapsed", () => {
    const profile = normalizeUiProfile(
      {
        layout: {
          nav_width: 380,
          device_panel_collapsed: true,
        },
      },
      {
        defaultNavWidth: 360,
        navMin: 260,
        navMax: 900,
        normalizePlotState,
        normalizeStreamWorkspaceRecord: () => ({}),
      }
    );
    expect(profile).not.toBeNull();
    expect(profile?.devicePanelCollapsed).toBe(true);
  });

  it("reads camelCase devicePanelCollapsed", () => {
    const profile = normalizeUiProfile(
      {
        layout: {
          nav_width: 380,
          devicePanelCollapsed: true,
        },
      },
      {
        defaultNavWidth: 360,
        navMin: 260,
        navMax: 900,
        normalizePlotState,
        normalizeStreamWorkspaceRecord: () => ({}),
      }
    );
    expect(profile).not.toBeNull();
    expect(profile?.devicePanelCollapsed).toBe(true);
  });
});
