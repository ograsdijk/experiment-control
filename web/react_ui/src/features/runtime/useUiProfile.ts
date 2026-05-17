import { useEffect, useRef, useState, type ChangeEvent } from "react";

import { notifications } from "@mantine/notifications";

import { fetchDefaultUiProfile } from "../../api";
import { normalizeUiProfile } from "../profile/utils";
import type { UiProfileFile } from "../profile/types";
import { useCommands } from "../commands/CommandsContext";
import { useDevicesContext } from "../devices/DevicesContext";
import {
  LAYOUT_DEFAULT_NAV_WIDTH,
  LAYOUT_NAV_MAX_WIDTH,
  LAYOUT_NAV_MIN_WIDTH,
  useLayout,
} from "../layout/LayoutContext";
import { usePanels } from "../panels/PanelsContext";
import { normalizePlotState, serializePlotState } from "../profile/plot_state";
import {
  isStreamBinStatsPanel,
  isStreamScalarPanel,
  workspaceFromLegacyPanel,
} from "../stream/panel_helpers";
import type { StreamAnalysisWorkspaceConfig } from "../stream/types";
import {
  nextWorkspaceCounter,
  normalizeStreamWorkspaceRecord,
} from "../stream/workspace";
import { useStreamAnalysis } from "../stream_analysis/StreamAnalysisContext";

/**
 * UI profile import / export — the full save / restore path for the
 * user's customised layout, panels, deck, and DAQ workspaces.
 *
 * **Handlers**:
 *
 * - `exportUiProfile()` — serializes the current Layout / Devices /
 *   Commands / Panels / StreamAnalysis state into a versioned JSON
 *   profile and triggers a browser download.
 * - `applyUiProfileRaw(raw, opts)` — validates a parsed profile,
 *   writes it across all five contexts, and syncs the imported
 *   workspaces back to the stream_analysis runtime if it is up.
 *   Also migrates legacy per-panel stream-analysis settings into the
 *   workspaces map when the profile predates the workspaces split.
 * - `importUiProfile(event)` — file-input change handler; reads the
 *   selected JSON file and delegates to `applyUiProfileRaw`.
 * - `loadDefaultUiProfile()` — fetches the instance's bundled default
 *   profile (if any) and applies it.
 *
 * **State** (exposed so App can render the "load defaults" button):
 *
 * - `defaultProfileAvailable` — true once an initial probe finds a
 *   default profile on the gateway.
 * - `defaultProfileLoading` — busy flag while a default-profile load
 *   is in flight.
 *
 * **Mount-time effect**: on first mount the hook probes for a default
 * profile. If one exists *and* there's no existing local customisation
 * (per a known set of localStorage keys), it nudges the user with a
 * toast suggesting they open Settings → Load instance defaults.
 *
 * **Args** (handlers that still live in App.tsx or other hooks):
 *
 * - `syncStreamAnalysisWorkspace` / `loadStreamAnalysisWorkspaces`
 *   from `useWorkspaceListManagement` — used by `applyUiProfileRaw`
 *   to push the imported workspaces to the runtime.
 * - `setDevicePanelCollapsed` — App-local wrapper that cancels any
 *   in-flight resize-animation frame before flipping the layout
 *   collapsed flag. Passed in so the profile-apply doesn't leave a
 *   stale RAF callback running against a vanished panel width.
 */

export interface UiProfileArgs {
  syncStreamAnalysisWorkspace: (
    workspaceId: string,
    source: string
  ) => Promise<void>;
  loadStreamAnalysisWorkspaces: (
    source: string,
    options?: { notifyOnError?: boolean }
  ) => Promise<unknown>;
  setDevicePanelCollapsed: (collapsed: boolean) => void;
}

const CUSTOMIZATION_LOCALSTORAGE_KEYS = [
  "ecui.commandDeck",
  "ecui.commandDeck.collapsedByGroup",
  "ecui.plotState",
  "ecui.pinnedCommands",
  "ecui.streamWorkspaces",
  "ecui.deviceOrder",
  "ecui.telemetryCollapsedByDevice",
];

export function useUiProfile(args: UiProfileArgs) {
  const {
    syncStreamAnalysisWorkspace,
    loadStreamAnalysisWorkspaces,
    setDevicePanelCollapsed,
  } = args;
  const {
    panels,
    activePanelId,
    setPanels,
    setActivePanelId,
    panelIdRef,
  } = usePanels();
  const {
    navWidth,
    isDevicePanelCollapsed,
    devicePanelTab,
    plotWorkspaceColumns,
    setNavWidth,
    setDevicePanelTab,
    setPlotWorkspaceColumns,
  } = useLayout();
  const {
    deviceOrder,
    telemetryCollapsedByDevice,
    setDeviceOrder,
    setTelemetryCollapsedByDevice,
  } = useDevicesContext();
  const {
    pinnedCommands,
    commandDeck,
    commandDeckCollapsedByGroup,
    setPinnedCommands,
    setCommandDeck,
    setCommandDeckCollapsedByGroup,
  } = useCommands();
  const {
    streamWorkspaces,
    setStreamWorkspaces,
    streamWorkspacesRef,
    setStreamWorkspaceRevisions,
    streamWorkspaceRevisionsRef,
    streamWorkspaceIdRef,
    setDaqWorkspaceId,
    streamAnalysisReadyRef,
  } = useStreamAnalysis();

  const [defaultProfileAvailable, setDefaultProfileAvailable] = useState(false);
  const [defaultProfileLoading, setDefaultProfileLoading] = useState(false);
  const defaultProfileCheckedRef = useRef(false);

  const exportUiProfile = () => {
    try {
      const serializedPlotState = serializePlotState({ panels, activePanelId });
      const profile: UiProfileFile = {
        kind: "experiment-control-ui-profile",
        version: 1,
        exported_at: new Date().toISOString(),
        layout: {
          nav_width: navWidth,
          device_panel_collapsed: isDevicePanelCollapsed,
          device_panel_tab: devicePanelTab,
          plot_workspace_columns: plotWorkspaceColumns,
          device_order: [...deviceOrder],
          telemetry_collapsed_by_device: { ...telemetryCollapsedByDevice },
        },
        plots: {
          plot_state: serializedPlotState,
        },
        commands: {
          pinned_commands: { ...pinnedCommands },
          command_deck: [...commandDeck],
          command_deck_collapsed_by_group: { ...commandDeckCollapsedByGroup },
        },
        analysis: {
          stream_workspaces: { ...streamWorkspaces },
        },
      };
      const text = JSON.stringify(profile, null, 2);
      const now = new Date();
      const stamp = `${now.getFullYear()}_${String(
        now.getMonth() + 1
      ).padStart(2, "0")}_${String(now.getDate()).padStart(2, "0")}-${String(
        now.getHours()
      ).padStart(2, "0")}_${String(now.getMinutes()).padStart(
        2,
        "0"
      )}_${String(now.getSeconds()).padStart(2, "0")}`;
      const filename = `ec_ui_profile_${stamp}.json`;
      const blob = new Blob([text], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      notifications.show({
        color: "teal",
        title: "UI profile exported",
        message: filename,
      });
    } catch (error) {
      notifications.show({
        color: "red",
        title: "Export failed",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  };

  const applyUiProfileRaw = async (
    raw: unknown,
    opts: { sourceLabel: string; syncSource: string }
  ): Promise<void> => {
    const { sourceLabel, syncSource } = opts;
    const profile = normalizeUiProfile(raw, {
      defaultNavWidth: LAYOUT_DEFAULT_NAV_WIDTH,
      navMin: LAYOUT_NAV_MIN_WIDTH,
      navMax: LAYOUT_NAV_MAX_WIDTH,
      normalizePlotState,
      normalizeStreamWorkspaceRecord,
    });
    if (!profile) {
      throw new Error("Invalid UI profile format.");
    }
    panelIdRef.current = profile.plotState.nextPanelId;
    setNavWidth(profile.navWidth);
    setDevicePanelCollapsed(profile.devicePanelCollapsed);
    setDevicePanelTab(profile.devicePanelTab);
    setPlotWorkspaceColumns(profile.plotWorkspaceColumns);
    setPanels(profile.plotState.panels);
    setActivePanelId(profile.plotState.activePanelId);
    setDeviceOrder(profile.deviceOrder);
    setTelemetryCollapsedByDevice(profile.telemetryCollapsedByDevice);
    setPinnedCommands(profile.pinnedCommands);
    setCommandDeck(profile.commandDeck);
    setCommandDeckCollapsedByGroup(profile.commandDeckCollapsedByGroup);
    {
      const migratedFromPanels: Record<string, StreamAnalysisWorkspaceConfig> =
        {};
      for (const panel of profile.plotState.panels) {
        if (!isStreamScalarPanel(panel) && !isStreamBinStatsPanel(panel)) {
          continue;
        }
        const workspaceId = String(panel.workspaceId ?? "").trim();
        if (!workspaceId || migratedFromPanels[workspaceId]) {
          continue;
        }
        migratedFromPanels[workspaceId] = workspaceFromLegacyPanel(panel);
      }
      const importedWorkspaces =
        Object.keys(profile.streamWorkspaces).length > 0
          ? profile.streamWorkspaces
          : Object.keys(migratedFromPanels).length > 0
          ? migratedFromPanels
          : streamWorkspacesRef.current;
      setStreamWorkspaces(importedWorkspaces);
      streamWorkspacesRef.current = importedWorkspaces;
      setStreamWorkspaceRevisions({});
      streamWorkspaceRevisionsRef.current = {};
      streamWorkspaceIdRef.current = nextWorkspaceCounter(importedWorkspaces);
      const firstWorkspaceId =
        Object.keys(importedWorkspaces).sort()[0] ?? null;
      setDaqWorkspaceId(firstWorkspaceId);
      if (streamAnalysisReadyRef.current) {
        for (const workspaceId of Object.keys(importedWorkspaces)) {
          await syncStreamAnalysisWorkspace(workspaceId, syncSource);
        }
        await loadStreamAnalysisWorkspaces(syncSource, {
          notifyOnError: false,
        });
      }
    }
    notifications.show({
      color: "teal",
      title: "UI profile loaded",
      message: sourceLabel,
    });
  };

  const importUiProfile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) {
      return;
    }
    try {
      const rawText = await file.text();
      const raw = JSON.parse(rawText);
      await applyUiProfileRaw(raw, {
        sourceLabel: file.name,
        syncSource: "ui-profile-import",
      });
    } catch (error) {
      notifications.show({
        color: "red",
        title: "Import failed",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  };

  const loadDefaultUiProfile = async (): Promise<boolean> => {
    setDefaultProfileLoading(true);
    try {
      const result = await fetchDefaultUiProfile();
      if (!result.ok) {
        if (result.status === 404) {
          setDefaultProfileAvailable(false);
          notifications.show({
            color: "yellow",
            title: "No default profile",
            message: "This instance does not provide a default UI profile.",
          });
          return false;
        }
        notifications.show({
          color: "red",
          title: "Default profile load failed",
          message: result.error,
        });
        return false;
      }
      await applyUiProfileRaw(result.raw, {
        sourceLabel: "instance default",
        syncSource: "ui-profile-default",
      });
      return true;
    } catch (error) {
      notifications.show({
        color: "red",
        title: "Default profile load failed",
        message: error instanceof Error ? error.message : String(error),
      });
      return false;
    } finally {
      setDefaultProfileLoading(false);
    }
  };

  // Probe for the instance default profile exactly once at mount.
  // If found and the user has no existing customisation, surface a
  // toast suggesting they load defaults.
  useEffect(() => {
    if (defaultProfileCheckedRef.current) {
      return;
    }
    defaultProfileCheckedRef.current = true;
    let alive = true;
    void (async () => {
      const result = await fetchDefaultUiProfile();
      if (!alive) {
        return;
      }
      if (!result.ok) {
        setDefaultProfileAvailable(false);
        return;
      }
      setDefaultProfileAvailable(true);
      const hasCustomization = CUSTOMIZATION_LOCALSTORAGE_KEYS.some((key) => {
        const raw = localStorage.getItem(key);
        if (raw === null) {
          return false;
        }
        const trimmed = raw.trim();
        if (!trimmed) {
          return false;
        }
        if (trimmed === "{}" || trimmed === "[]") {
          return false;
        }
        return true;
      });
      if (hasCustomization) {
        return;
      }
      notifications.show({
        color: "blue",
        title: "Instance default UI profile available",
        message:
          "Open Settings → Load instance defaults to populate the command deck and plot workspaces.",
        autoClose: 8000,
      });
    })();
    return () => {
      alive = false;
    };
  }, []);

  return {
    exportUiProfile,
    applyUiProfileRaw,
    importUiProfile,
    loadDefaultUiProfile,
    defaultProfileAvailable,
    defaultProfileLoading,
  };
}
