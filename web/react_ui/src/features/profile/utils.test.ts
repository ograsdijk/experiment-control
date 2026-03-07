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

  it("defaults devicePanelTab to devices", () => {
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
    expect(profile?.devicePanelTab).toBe("devices");
  });

  it("reads command deck entries from commands.command_deck", () => {
    const profile = normalizeUiProfile(
      {
        layout: { nav_width: 380 },
        commands: {
          command_deck: [
            {
              id: "deck-1",
              target_kind: "device",
              target_id: "laser",
              action: "set_frequency_hz",
              group: "Scan",
              params_draft: { hz: "1.23" },
            },
          ],
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
    expect(profile?.commandDeck).toHaveLength(1);
    expect(profile?.commandDeck[0].targetId).toBe("laser");
    expect(profile?.commandDeck[0].action).toBe("set_frequency_hz");
  });
});
