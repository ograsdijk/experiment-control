import type { PinnedCommand } from "../../types";
import { normalizeBooleanMap, normalizeStringList } from "../common/normalize";
import type {
  PinnedCommandMap,
  PlotState,
  UiProfileState,
} from "./types";

export function clampNavWidth(
  value: number,
  opts: { min: number; max: number }
): number {
  if (typeof window === "undefined") {
    return Math.max(opts.min, Math.min(opts.max, value));
  }
  const max = Math.min(opts.max, Math.floor(window.innerWidth * 0.6));
  const safeMax = Math.max(opts.min, max);
  return Math.max(opts.min, Math.min(safeMax, value));
}

export function normalizePinnedCommands(raw: unknown): PinnedCommandMap {
  if (!raw || typeof raw !== "object") {
    return {};
  }
  const result: PinnedCommandMap = {};
  for (const [deviceId, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!Array.isArray(value)) {
      continue;
    }
    const entries: PinnedCommand[] = [];
    for (const item of value) {
      if (typeof item === "string") {
        entries.push({ action: item });
        continue;
      }
      if (item && typeof item === "object") {
        const action =
          typeof (item as { action?: unknown }).action === "string"
            ? String((item as { action?: unknown }).action)
            : "";
        if (!action) {
          continue;
        }
        const labelRaw = (item as { label?: unknown }).label;
        const label =
          typeof labelRaw === "string" && labelRaw.trim().length > 0
            ? labelRaw.trim()
            : undefined;
        entries.push({ action, label });
      }
    }
    if (entries.length > 0) {
      result[deviceId] = entries;
    }
  }
  return result;
}

export function normalizeUiProfile(
  raw: unknown,
  opts: {
    defaultNavWidth: number;
    navMin: number;
    navMax: number;
    normalizePlotState: (rawPlot: unknown) => PlotState;
    normalizeStreamWorkspaceRecord: (
      rawWorkspaces: unknown
    ) => UiProfileState["streamWorkspaces"];
  }
): UiProfileState | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  const layout =
    obj.layout && typeof obj.layout === "object"
      ? (obj.layout as Record<string, unknown>)
      : obj;
  const plots =
    obj.plots && typeof obj.plots === "object"
      ? (obj.plots as Record<string, unknown>)
      : obj;
  const commands =
    obj.commands && typeof obj.commands === "object"
      ? (obj.commands as Record<string, unknown>)
      : obj;
  const analysis =
    obj.analysis && typeof obj.analysis === "object"
      ? (obj.analysis as Record<string, unknown>)
      : obj;

  const navWidthRaw = layout.nav_width ?? layout.navWidth ?? obj.navWidth;
  const deviceOrderRaw =
    layout.device_order ?? layout.deviceOrder ?? obj.deviceOrder;
  const devicePanelCollapsedRaw =
    layout.device_panel_collapsed ??
    layout.devicePanelCollapsed ??
    obj.devicePanelCollapsed;
  const collapsedRaw =
    layout.telemetry_collapsed_by_device ??
    layout.telemetryCollapsedByDevice ??
    obj.telemetryCollapsedByDevice;
  const pinnedCommandsRaw =
    commands.pinned_commands ?? commands.pinnedCommands ?? obj.pinnedCommands;
  const streamWorkspacesRaw =
    analysis.stream_workspaces ??
    analysis.streamWorkspaces ??
    obj.stream_workspaces ??
    obj.streamWorkspaces;

  const plotStateRaw =
    (plots.plot_state as unknown) ??
    (plots.plotState as unknown) ??
    (obj.plotState as unknown) ??
    ({
      panels: plots.panels ?? obj.panels,
      activePanelId:
        plots.active_panel_id ?? plots.activePanelId ?? obj.activePanelId,
    } as unknown);

  const hasKnownData =
    navWidthRaw !== undefined ||
    devicePanelCollapsedRaw !== undefined ||
    deviceOrderRaw !== undefined ||
    collapsedRaw !== undefined ||
    pinnedCommandsRaw !== undefined ||
    streamWorkspacesRaw !== undefined ||
    (plotStateRaw &&
      typeof plotStateRaw === "object" &&
      ("panels" in (plotStateRaw as Record<string, unknown>) ||
        "activePanelId" in (plotStateRaw as Record<string, unknown>)));
  if (!hasKnownData) {
    return null;
  }

  const navWidth =
    typeof navWidthRaw === "number" && Number.isFinite(navWidthRaw)
      ? clampNavWidth(navWidthRaw, { min: opts.navMin, max: opts.navMax })
      : clampNavWidth(opts.defaultNavWidth, { min: opts.navMin, max: opts.navMax });

  return {
    navWidth,
    devicePanelCollapsed: Boolean(devicePanelCollapsedRaw),
    plotState: opts.normalizePlotState(plotStateRaw),
    deviceOrder: normalizeStringList(deviceOrderRaw),
    telemetryCollapsedByDevice: normalizeBooleanMap(collapsedRaw),
    pinnedCommands: normalizePinnedCommands(pinnedCommandsRaw),
    streamWorkspaces: opts.normalizeStreamWorkspaceRecord(streamWorkspacesRaw),
  };
}
