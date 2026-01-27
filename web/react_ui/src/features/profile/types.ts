import type { PinnedCommand } from "../../types";
import type { PlotPanelState, StreamAnalysisWorkspaceConfig } from "../stream/types";

export type PinnedCommandMap = Record<string, PinnedCommand[]>;

export type PlotState = {
  panels: PlotPanelState[];
  activePanelId: string;
  nextPanelId: number;
};

export type UiProfileState = {
  navWidth: number;
  plotState: PlotState;
  deviceOrder: string[];
  telemetryCollapsedByDevice: Record<string, boolean>;
  pinnedCommands: PinnedCommandMap;
  streamWorkspaces: Record<string, StreamAnalysisWorkspaceConfig>;
};

export type UiProfileFile = {
  kind: "experiment-control-ui-profile";
  version: 1;
  exported_at: string;
  layout: {
    nav_width: number;
    device_order: string[];
    telemetry_collapsed_by_device: Record<string, boolean>;
  };
  plots: {
    plot_state: {
      panels: PlotPanelState[];
      activePanelId: string;
    };
  };
  commands: {
    pinned_commands: PinnedCommandMap;
  };
  analysis: {
    stream_workspaces: Record<string, StreamAnalysisWorkspaceConfig>;
  };
};

export type PinnedParamDrafts = Record<string, Record<string, string>>;
