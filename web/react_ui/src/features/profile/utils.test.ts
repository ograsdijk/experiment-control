import { describe, expect, it } from "vitest";
import { normalizeUiProfile } from "./utils";
import type { PlotState } from "./types";
import type { CommandDeckCommandEntry } from "../../types";

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

  it("reads layout.plot_workspace_columns", () => {
    const profile = normalizeUiProfile(
      {
        layout: {
          nav_width: 380,
          plot_workspace_columns: "3",
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
    expect(profile?.plotWorkspaceColumns).toBe("3");
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
    expect((profile?.commandDeck[0] as CommandDeckCommandEntry).targetId).toBe("laser");
    expect((profile?.commandDeck[0] as CommandDeckCommandEntry).action).toBe("set_frequency_hz");
  });

  it("accepts process command deck entries", () => {
    const profile = normalizeUiProfile(
      {
        layout: { nav_width: 380 },
        commands: {
          command_deck: [
            {
              id: "deck-proc-1",
              target_kind: "process",
              target_id: "bias_sequence",
              action: "turn_on",
              label: "Bias ON",
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
    expect((profile?.commandDeck[0] as CommandDeckCommandEntry).targetKind).toBe("process");
    expect((profile?.commandDeck[0] as CommandDeckCommandEntry).targetId).toBe("bias_sequence");
    expect((profile?.commandDeck[0] as CommandDeckCommandEntry).action).toBe("turn_on");
  });

  it("accepts telemetry deck entries", () => {
    const profile = normalizeUiProfile(
      {
        layout: { nav_width: 380 },
        commands: {
          command_deck: [
            {
              id: "deck-telem-1",
              kind: "telemetry",
              device_id: "laser",
              signal: "frequency_hz",
              format: "scientific",
              decimals: 4,
              label: "Readback",
              group: "Scan",
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
    const entry = profile?.commandDeck[0];
    expect(entry?.kind).toBe("telemetry");
    if (entry && entry.kind === "telemetry") {
      expect(entry.deviceId).toBe("laser");
      expect(entry.signal).toBe("frequency_hz");
      expect(entry.format).toBe("scientific");
      expect(entry.decimals).toBe(4);
    }
  });
});
