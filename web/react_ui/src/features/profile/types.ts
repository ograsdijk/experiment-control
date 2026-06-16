import type { CommandDeckEntry, PinnedCommand } from "../../types";
import type { PlotPanelState, StreamAnalysisWorkspaceConfig } from "../stream/types";

export type PinnedCommandMap = Record<string, PinnedCommand[]>;
export type PlotWorkspaceColumnsSetting = "auto" | "1" | "2" | "3" | "4";

export type PlotState = {
  panels: PlotPanelState[];
  activePanelId: string | null;
  nextPanelId: number;
};

export type UiProfileState = {
  navWidth: number;
  devicePanelCollapsed: boolean;
  devicePanelTab: "devices" | "deck";
  plotWorkspaceColumns: PlotWorkspaceColumnsSetting;
  plotState: PlotState;
  deviceOrder: string[];
  telemetryCollapsedByDevice: Record<string, boolean>;
  pinnedCommands: PinnedCommandMap;
  commandDeck: CommandDeckEntry[];
  commandDeckCollapsedByGroup: Record<string, boolean>;
  streamWorkspaces: Record<string, StreamAnalysisWorkspaceConfig>;
};

export type UiProfileFile = {
  kind: "experiment-control-ui-profile";
  version: 1;
  exported_at: string;
  layout: {
    nav_width: number;
    device_panel_collapsed?: boolean;
    device_panel_tab?: "devices" | "deck";
    plot_workspace_columns?: PlotWorkspaceColumnsSetting;
    device_order: string[];
    telemetry_collapsed_by_device: Record<string, boolean>;
  };
  plots: {
    plot_state: {
      panels: PlotPanelState[];
      activePanelId: string | null;
    };
  };
  commands: {
    pinned_commands: PinnedCommandMap;
    command_deck?: CommandDeckEntry[];
    command_deck_collapsed_by_group?: Record<string, boolean>;
  };
  analysis: {
    stream_workspaces: Record<string, StreamAnalysisWorkspaceConfig>;
  };
};

export type PinnedParamDrafts = Record<string, Record<string, string>>;
