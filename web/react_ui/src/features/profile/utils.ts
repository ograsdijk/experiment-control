import type { CommandDeckEntry, PinnedCommand } from "../../types";
import { normalizeBooleanMap, normalizeStringList } from "../common/normalize";
import type {
  PinnedCommandMap,
  PlotState,
  PlotWorkspaceColumnsSetting,
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

export function normalizeCommandDeck(raw: unknown): CommandDeckEntry[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const out: CommandDeckEntry[] = [];
  const seenIds = new Set<string>();
  for (let index = 0; index < raw.length; index += 1) {
    const item = raw[index];
    if (!item || typeof item !== "object") {
      continue;
    }
    const obj = item as Record<string, unknown>;
    const kindRaw = String(obj.kind ?? "").trim().toLowerCase();
    const isTelemetry = kindRaw === "telemetry";
    const targetKindRaw = String(obj.targetKind ?? obj.target_kind ?? "device").trim();
    const targetKind =
      targetKindRaw === "device" || targetKindRaw === "process"
        ? targetKindRaw
        : null;
    let id = String(obj.id ?? "").trim();
    const labelRaw = obj.label;
    const label =
      typeof labelRaw === "string" && labelRaw.trim().length > 0
        ? labelRaw.trim()
        : undefined;
    const groupRaw = obj.group;
    const group =
      typeof groupRaw === "string" && groupRaw.trim().length > 0
        ? groupRaw.trim()
        : undefined;
    const createdAtRaw = obj.createdAt ?? obj.created_at;
    const createdAt =
      typeof createdAtRaw === "number" && Number.isFinite(createdAtRaw)
        ? createdAtRaw
        : undefined;
    if (isTelemetry) {
      const deviceId = String(obj.deviceId ?? obj.device_id ?? "").trim();
      const signal = String(obj.signal ?? "").trim();
      if (!deviceId || !signal) {
        continue;
      }
      const formatRaw = String(obj.format ?? obj.notation ?? "auto")
        .trim()
        .toLowerCase();
      const format =
        formatRaw === "fixed" || formatRaw === "scientific" ? formatRaw : "auto";
      const decimalsRaw = obj.decimals;
      const decimals =
        typeof decimalsRaw === "number" && Number.isFinite(decimalsRaw)
          ? Math.max(0, Math.min(12, Math.trunc(decimalsRaw)))
          : undefined;
      if (!id) {
        id = `deck-${index}-${deviceId}-${signal}`;
      }
      if (seenIds.has(id)) {
        continue;
      }
      seenIds.add(id);
      out.push({
        id,
        kind: "telemetry",
        deviceId,
        signal,
        format,
        decimals,
        label,
        group,
        createdAt,
      });
      continue;
    }
    if (!targetKind) {
      continue;
    }
    const targetId = String(obj.targetId ?? obj.target_id ?? "").trim();
    const action = String(obj.action ?? "").trim();
    if (!targetId || !action) {
      continue;
    }
    if (!id) {
      id = `deck-${index}-${targetId}-${action}`;
    }
    if (seenIds.has(id)) {
      continue;
    }
    seenIds.add(id);
    const paramsDraftRaw = obj.paramsDraft ?? obj.params_draft;
    const paramsDraft: Record<string, string> = {};
    if (paramsDraftRaw && typeof paramsDraftRaw === "object") {
      for (const [key, value] of Object.entries(
        paramsDraftRaw as Record<string, unknown>
      )) {
        if (typeof key !== "string" || !key.trim()) {
          continue;
        }
        if (typeof value === "string") {
          paramsDraft[key] = value;
        } else if (value != null) {
          paramsDraft[key] = String(value);
        } else {
          paramsDraft[key] = "";
        }
      }
    }
    out.push({
      id,
      kind: "command",
      targetKind,
      targetId,
      action,
      label,
      group,
      paramsDraft,
      createdAt,
    });
  }
  return out;
}

export function normalizePlotWorkspaceColumnsSetting(
  raw: unknown
): PlotWorkspaceColumnsSetting {
  const normalized = String(raw ?? "").trim().toLowerCase();
  if (normalized === "1" || normalized === "2" || normalized === "3" || normalized === "4") {
    return normalized;
  }
  return "auto";
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
  const devicePanelTabRaw =
    layout.device_panel_tab ?? layout.devicePanelTab ?? obj.devicePanelTab;
  const plotWorkspaceColumnsRaw =
    layout.plot_workspace_columns ??
    layout.plotWorkspaceColumns ??
    obj.plotWorkspaceColumns;
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
  const commandDeckRaw =
    commands.command_deck ?? commands.commandDeck ?? obj.commandDeck;
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
    devicePanelTabRaw !== undefined ||
    plotWorkspaceColumnsRaw !== undefined ||
    deviceOrderRaw !== undefined ||
    collapsedRaw !== undefined ||
    pinnedCommandsRaw !== undefined ||
    commandDeckRaw !== undefined ||
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
    devicePanelTab: devicePanelTabRaw === "deck" ? "deck" : "devices",
    plotWorkspaceColumns: normalizePlotWorkspaceColumnsSetting(
      plotWorkspaceColumnsRaw
    ),
    plotState: opts.normalizePlotState(plotStateRaw),
    deviceOrder: normalizeStringList(deviceOrderRaw),
    telemetryCollapsedByDevice: normalizeBooleanMap(collapsedRaw),
    pinnedCommands: normalizePinnedCommands(pinnedCommandsRaw),
    commandDeck: normalizeCommandDeck(commandDeckRaw),
    streamWorkspaces: opts.normalizeStreamWorkspaceRecord(streamWorkspacesRaw),
  };
}
