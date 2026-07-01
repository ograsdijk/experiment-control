import { useMemo } from "react";

import { useStreamAnalysis } from "../stream_analysis/StreamAnalysisContext";
import type {
  PlotStreamPanelState,
  PlotStreamBin2dPanelState,
  PlotStreamBinStatsPanelState,
  PlotStreamParamsPanelState,
  RawStreamSubscription,
  StreamAnalysisWorkspaceSubscription,
  StreamAnalysisWorkspaceConfig,
  StreamTraceAverageMode,
  StreamTraceDecimator,
} from "../stream/types";
import {
  DEFAULT_TRACE_AVERAGE_MODE,
  DEFAULT_TRACE_DECIMATOR,
  DEFAULT_TRACE_MAX_FPS,
  DEFAULT_TRACE_MAX_POINTS,
  normalizeTraceAverageMode,
  normalizeTraceDecimator,
  normalizeTraceMaxFps,
  normalizeTraceMaxPoints,
  normalizeTraceRollingWindow,
} from "../stream/utils";
import {
  isStreamBin2dPanel,
  isStreamBinStatsPanel,
  isStreamParamsPanel,
  isStreamScalarPanel,
  isStreamTracePanel,
} from "../stream/panel_helpers";
import {
  workspaceBin2dAxisLabel,
  workspaceOutputKind,
  workspaceOutputOptionsByKind,
  workspaceXAxisLabel,
} from "../stream/workspace";
import { usePanels } from "./PanelsContext";

/**
 * Pure read-side derivations of panel + workspace state.
 *
 * App.tsx historically held a "memo tree" of ~18 `useMemo` entries
 * that resolved which panel had each modal open, the workspace +
 * output options for those modals, and the X/Y axis labels. Plus the
 * two subscription derivations (`activeRawStreamSubscriptions` and
 * `activeStreamAnalysisWorkspaceSubscriptions`) that walk `panels`
 * to build WS subscription lists.
 *
 * All of those are pure derivations of `panels` + `streamWorkspaces`
 * + the modal-panel-id state already owned by `PanelsContext` /
 * `StreamAnalysisContext`. This hook collects them into one place so
 * the next panel extraction (handlers) and the eventual render-loop
 * extraction don't have to thread the dependencies separately.
 *
 * **Scope choices** (round 13 of S1):
 *
 * - Read-side only. Mutation handlers, `applyRawStreamFrameToPanels`,
 *   and `applyStreamAnalysisOutputToPanels` stay in App.tsx — those
 *   are the next round's target.
 * - The hook reads from `usePanels()` and `useStreamAnalysis()`
 *   internally; no props needed. Callers just destructure the
 *   returned object.
 * - Each output keeps the exact name App.tsx used inline so the
 *   ~30 call sites in App.tsx don't need touch-ups.
 */

// The return type is intentionally inferred from the object literal at
// the bottom of usePanelDerivations() — gives consumers exact field
// types without restating ~20 entries by hand.

export function usePanelDerivations() {
  const {
    panels,
    expandedPlotPanelId,
    streamTraceOptionsPanelId,
    streamBinStatsOptionsPanelId,
    streamParamsOptionsPanelId,
    streamBin2dOptionsPanelId,
  } = usePanels();
  const { streamWorkspaces } = useStreamAnalysis();

  // ---- expanded panel ----------------------------------------------
  const expandedPlotPanel = useMemo(
    () => panels.find((panel) => panel.id === expandedPlotPanelId) ?? null,
    [expandedPlotPanelId, panels]
  );

  // ---- stream trace options ---------------------------------------
  // No explicit return type — TS infers `PlotStreamPanelState |
  // PlotStreamWaterfallPanelState | null` from the `isStreamTracePanel`
  // guard, same as App.tsx had inline.
  const streamTraceOptionsPanel = useMemo(() => {
    const panel = panels.find((entry) => entry.id === streamTraceOptionsPanelId) ?? null;
    if (!panel || !isStreamTracePanel(panel)) {
      return null;
    }
    return panel;
  }, [panels, streamTraceOptionsPanelId]);

  const streamTraceOptionsWorkspace =
    useMemo<StreamAnalysisWorkspaceConfig | null>(() => {
      if (!streamTraceOptionsPanel || streamTraceOptionsPanel.sourceMode !== "dag") {
        return null;
      }
      return streamWorkspaces[streamTraceOptionsPanel.workspaceId] ?? null;
    }, [streamTraceOptionsPanel, streamWorkspaces]);

  const streamTraceOptionsTraceOutputOptions = useMemo(() => {
    return workspaceOutputOptionsByKind(streamTraceOptionsWorkspace, "trace");
  }, [streamTraceOptionsWorkspace]);

  const streamTraceOptionsOverlayOutputOptions = useMemo(() => {
    const selectedPrimary = String(streamTraceOptionsPanel?.outputId ?? "").trim();
    return streamTraceOptionsTraceOutputOptions.filter(
      (option) => option.value !== selectedPrimary
    );
  }, [streamTraceOptionsTraceOutputOptions, streamTraceOptionsPanel?.outputId]);

  // ---- stream bin stats options -----------------------------------
  const streamBinStatsOptionsPanel =
    useMemo<PlotStreamBinStatsPanelState | null>(() => {
      const panel = panels.find((entry) => entry.id === streamBinStatsOptionsPanelId) ?? null;
      if (!panel || !isStreamBinStatsPanel(panel)) {
        return null;
      }
      return panel;
    }, [panels, streamBinStatsOptionsPanelId]);

  const streamBinStatsOptionsWorkspace =
    useMemo<StreamAnalysisWorkspaceConfig | null>(() => {
      if (!streamBinStatsOptionsPanel) {
        return null;
      }
      return streamWorkspaces[streamBinStatsOptionsPanel.workspaceId] ?? null;
    }, [streamBinStatsOptionsPanel, streamWorkspaces]);

  const streamBinStatsOptionsOutputOptions = useMemo(() => {
    return workspaceOutputOptionsByKind(streamBinStatsOptionsWorkspace, "hist_agg");
  }, [streamBinStatsOptionsWorkspace]);

  const streamBinStatsOptionsTraceOverlayOptions = useMemo(() => {
    return workspaceOutputOptionsByKind(streamBinStatsOptionsWorkspace, "trace");
  }, [streamBinStatsOptionsWorkspace]);

  const streamBinStatsOptionsFitOverlayOptions = useMemo(() => {
    return workspaceOutputOptionsByKind(streamBinStatsOptionsWorkspace, "fit_1d");
  }, [streamBinStatsOptionsWorkspace]);

  const streamBinStatsOptionsXLabel = useMemo(() => {
    return workspaceXAxisLabel(
      streamBinStatsOptionsWorkspace,
      streamBinStatsOptionsPanel?.outputId ?? null
    );
  }, [streamBinStatsOptionsWorkspace, streamBinStatsOptionsPanel?.outputId]);

  // ---- stream params options --------------------------------------
  const streamParamsOptionsPanel =
    useMemo<PlotStreamParamsPanelState | null>(() => {
      const panel = panels.find((entry) => entry.id === streamParamsOptionsPanelId) ?? null;
      if (!panel || !isStreamParamsPanel(panel)) {
        return null;
      }
      return panel;
    }, [panels, streamParamsOptionsPanelId]);

  const streamParamsOptionsWorkspace =
    useMemo<StreamAnalysisWorkspaceConfig | null>(() => {
      if (!streamParamsOptionsPanel) {
        return null;
      }
      return streamWorkspaces[streamParamsOptionsPanel.workspaceId] ?? null;
    }, [streamParamsOptionsPanel, streamWorkspaces]);

  const streamParamsOutputOptions = useMemo(() => {
    const scalar = workspaceOutputOptionsByKind(streamParamsOptionsWorkspace, "scalar").map(
      (item) => ({
        value: item.value,
        label: `[scalar] ${item.label}`,
      })
    );
    const paramsMap = workspaceOutputOptionsByKind(
      streamParamsOptionsWorkspace,
      "params_map"
    ).map((item) => ({
      value: item.value,
      label: `[fit params] ${item.label}`,
    }));
    return [...scalar, ...paramsMap];
  }, [streamParamsOptionsWorkspace]);

  // ---- stream 2D bins options -------------------------------------
  const streamBin2dOptionsPanel =
    useMemo<PlotStreamBin2dPanelState | null>(() => {
      const panel = panels.find((entry) => entry.id === streamBin2dOptionsPanelId) ?? null;
      if (!panel || !isStreamBin2dPanel(panel)) {
        return null;
      }
      return panel;
    }, [panels, streamBin2dOptionsPanelId]);

  const streamBin2dOptionsWorkspace =
    useMemo<StreamAnalysisWorkspaceConfig | null>(() => {
      if (!streamBin2dOptionsPanel) {
        return null;
      }
      return streamWorkspaces[streamBin2dOptionsPanel.workspaceId] ?? null;
    }, [streamBin2dOptionsPanel, streamWorkspaces]);

  const streamBin2dOptionsOutputOptions = useMemo(() => {
    return workspaceOutputOptionsByKind(streamBin2dOptionsWorkspace, "hist2d");
  }, [streamBin2dOptionsWorkspace]);

  const streamBin2dOptionsXLabel = useMemo(() => {
    return workspaceBin2dAxisLabel(
      streamBin2dOptionsWorkspace,
      streamBin2dOptionsPanel?.outputId ?? null,
      "x"
    );
  }, [streamBin2dOptionsWorkspace, streamBin2dOptionsPanel?.outputId]);

  const streamBin2dOptionsYLabel = useMemo(() => {
    return workspaceBin2dAxisLabel(
      streamBin2dOptionsWorkspace,
      streamBin2dOptionsPanel?.outputId ?? null,
      "y"
    );
  }, [streamBin2dOptionsWorkspace, streamBin2dOptionsPanel?.outputId]);

  // ---- subscription derivations -----------------------------------
  //
  // `activeRawStreamSubscriptions` and
  // `activeStreamAnalysisWorkspaceSubscriptions` are the two long-form
  // panel walks that feed the raw-stream and DAQ WS subscription
  // effects. They're pure functions of `panels` (+ `streamWorkspaces`
  // for the analysis side), so they belong here next to the other
  // panel-derived memos.

  const activeRawStreamSubscriptions = useMemo<RawStreamSubscription[]>(() => {
    const out = new Map<string, RawStreamSubscription>();
    for (const panel of panels) {
      if (!isStreamTracePanel(panel) || panel.sourceMode !== "raw" || panel.stream === null) {
        continue;
      }
      const traceDecimator = normalizeTraceDecimator(panel.traceDecimator);
      const traceMaxPoints = normalizeTraceMaxPoints(panel.traceMaxPoints);
      const traceMaxFps = normalizeTraceMaxFps(panel.traceMaxFps);
      const rollingWindow = normalizeTraceRollingWindow(panel.rollingWindow);
      const averageMode = normalizeTraceAverageMode(panel.averageMode);
      // Multi-channel raw panels subscribe to one channel each. The
      // primary `channelIndex` plus any `extraChannelIndices` form the
      // effective set; distinct channels → distinct subscriptions (the
      // sub key includes the channel), which the WS manager turns into
      // one socket per channel.
      const extraChannels =
        panel.kind === "stream_raw" ? panel.extraChannelIndices ?? [] : [];
      const channels = [
        ...new Set(
          [panel.channelIndex, ...extraChannels].map((value) =>
            Math.max(0, Math.trunc(value))
          )
        ),
      ];
      for (const channelIndex of channels) {
        const key = [
          panel.stream.deviceId,
          panel.stream.stream,
          String(channelIndex),
          traceDecimator,
          String(traceMaxPoints),
          traceMaxFps.toFixed(3),
          String(rollingWindow),
          averageMode,
        ].join("|");
        out.set(key, {
          deviceId: panel.stream.deviceId,
          stream: panel.stream.stream,
          channelIndex,
          traceDecimator,
          traceMaxPoints,
          traceMaxFps,
          rollingWindow,
          averageMode,
        });
      }
    }
    return [...out.values()].sort((a, b) => {
      if (a.deviceId !== b.deviceId) {
        return a.deviceId.localeCompare(b.deviceId);
      }
      if (a.stream !== b.stream) {
        return a.stream.localeCompare(b.stream);
      }
      if (a.channelIndex !== b.channelIndex) {
        return a.channelIndex - b.channelIndex;
      }
      if (a.traceDecimator !== b.traceDecimator) {
        return a.traceDecimator.localeCompare(b.traceDecimator);
      }
      if (a.traceMaxPoints !== b.traceMaxPoints) {
        return a.traceMaxPoints - b.traceMaxPoints;
      }
      if (a.traceMaxFps !== b.traceMaxFps) {
        return a.traceMaxFps - b.traceMaxFps;
      }
      if (a.rollingWindow !== b.rollingWindow) {
        return a.rollingWindow - b.rollingWindow;
      }
      return a.averageMode.localeCompare(b.averageMode);
    });
  }, [panels]);

  const activeStreamAnalysisWorkspaceSubscriptions = useMemo<
    StreamAnalysisWorkspaceSubscription[]
  >(() => {
    const outputKindsByWorkspace = new Map<
      string,
      Set<"scalar" | "hist_agg" | "hist2d" | "params_map" | "fit_1d">
    >();
    const traceConfigsByWorkspace = new Map<
      string,
      Map<
        string,
        {
          traceDecimator: StreamTraceDecimator;
          traceMaxPoints: number;
          traceMaxFps: number;
          traceRollingWindow: number;
          traceAverageMode: StreamTraceAverageMode;
        }
      >
    >();
    for (const panel of panels) {
      // PlotTelemetryPanelState is the only variant without
      // `workspaceId`; use a runtime guard so TS narrows the union.
      const workspaceId = (
        "workspaceId" in panel ? String(panel.workspaceId ?? "").trim() : ""
      );
      if (!workspaceId) {
        continue;
      }
      if (isStreamScalarPanel(panel)) {
        const kinds = outputKindsByWorkspace.get(workspaceId) ?? new Set();
        kinds.add("scalar");
        outputKindsByWorkspace.set(workspaceId, kinds);
        continue;
      }
      if (isStreamParamsPanel(panel)) {
        const kinds = outputKindsByWorkspace.get(workspaceId) ?? new Set();
        const workspace = streamWorkspaces[workspaceId] ?? null;
        for (const outputId of panel.outputIds ?? []) {
          const kind = workspaceOutputKind(workspace, outputId);
          if (kind === "scalar" || kind === "params_map") {
            kinds.add(kind);
          }
        }
        outputKindsByWorkspace.set(workspaceId, kinds);
        continue;
      }
      if (isStreamBinStatsPanel(panel)) {
        const kinds = outputKindsByWorkspace.get(workspaceId) ?? new Set();
        kinds.add("hist_agg");
        if ((panel.fitOverlayOutputIds ?? []).length > 0) {
          kinds.add("fit_1d");
        }
        outputKindsByWorkspace.set(workspaceId, kinds);
        if ((panel.overlayOutputIds ?? []).length > 0) {
          const configs = traceConfigsByWorkspace.get(workspaceId) ?? new Map();
          const traceDecimator = DEFAULT_TRACE_DECIMATOR;
          const traceMaxPoints = DEFAULT_TRACE_MAX_POINTS;
          const traceMaxFps = DEFAULT_TRACE_MAX_FPS;
          const traceRollingWindow = 1;
          const traceAverageMode = DEFAULT_TRACE_AVERAGE_MODE;
          const key = `${traceDecimator}|${traceMaxPoints}|${traceMaxFps.toFixed(3)}|${traceRollingWindow}|${traceAverageMode}`;
          configs.set(key, {
            traceDecimator,
            traceMaxPoints,
            traceMaxFps,
            traceRollingWindow,
            traceAverageMode,
          });
          traceConfigsByWorkspace.set(workspaceId, configs);
        }
        continue;
      }
      if (isStreamBin2dPanel(panel)) {
        const kinds = outputKindsByWorkspace.get(workspaceId) ?? new Set();
        kinds.add("hist2d");
        outputKindsByWorkspace.set(workspaceId, kinds);
        continue;
      }
      if (isStreamTracePanel(panel) && panel.sourceMode === "dag") {
        const configs = traceConfigsByWorkspace.get(workspaceId) ?? new Map();
        const traceDecimator = normalizeTraceDecimator(panel.traceDecimator);
        const traceMaxPoints = normalizeTraceMaxPoints(panel.traceMaxPoints);
        const traceMaxFps = normalizeTraceMaxFps(panel.traceMaxFps);
        const traceRollingWindow = normalizeTraceRollingWindow(panel.rollingWindow);
        const traceAverageMode = normalizeTraceAverageMode(panel.averageMode);
        const key = `${traceDecimator}|${traceMaxPoints}|${traceMaxFps.toFixed(3)}|${traceRollingWindow}|${traceAverageMode}`;
        configs.set(key, {
          traceDecimator,
          traceMaxPoints,
          traceMaxFps,
          traceRollingWindow,
          traceAverageMode,
        });
        traceConfigsByWorkspace.set(workspaceId, configs);
      }
    }
    const workspaceIds = new Set<string>([
      ...outputKindsByWorkspace.keys(),
      ...traceConfigsByWorkspace.keys(),
    ]);
    const out: StreamAnalysisWorkspaceSubscription[] = [];
    for (const workspaceId of [...workspaceIds].sort()) {
      const outputKinds = outputKindsByWorkspace.get(workspaceId);
      if (outputKinds && outputKinds.size > 0) {
        const kinds = [...outputKinds].sort() as Array<
          "scalar" | "hist_agg" | "hist2d" | "params_map" | "fit_1d"
        >;
        out.push({
          workspaceId,
          kinds,
        });
      }
      const traceConfigs = traceConfigsByWorkspace.get(workspaceId);
      if (traceConfigs && traceConfigs.size > 0) {
        const sortedConfigs = [...traceConfigs.values()].sort((a, b) => {
          if (a.traceDecimator !== b.traceDecimator) {
            return a.traceDecimator.localeCompare(b.traceDecimator);
          }
          if (a.traceMaxPoints !== b.traceMaxPoints) {
            return a.traceMaxPoints - b.traceMaxPoints;
          }
          if (a.traceMaxFps !== b.traceMaxFps) {
            return a.traceMaxFps - b.traceMaxFps;
          }
          if (a.traceRollingWindow !== b.traceRollingWindow) {
            return a.traceRollingWindow - b.traceRollingWindow;
          }
          return a.traceAverageMode.localeCompare(b.traceAverageMode);
        });
        for (const cfg of sortedConfigs) {
          out.push({
            workspaceId,
            kinds: ["trace"],
            traceDecimator: cfg.traceDecimator,
            traceMaxPoints: cfg.traceMaxPoints,
            traceMaxFps: cfg.traceMaxFps,
            traceRollingWindow: cfg.traceRollingWindow,
            traceAverageMode: cfg.traceAverageMode,
          });
        }
      }
    }
    return out;
  }, [panels, streamWorkspaces]);

  return {
    expandedPlotPanel,
    streamTraceOptionsPanel,
    streamTraceOptionsWorkspace,
    streamTraceOptionsTraceOutputOptions,
    streamTraceOptionsOverlayOutputOptions,
    streamBinStatsOptionsPanel,
    streamBinStatsOptionsWorkspace,
    streamBinStatsOptionsOutputOptions,
    streamBinStatsOptionsTraceOverlayOptions,
    streamBinStatsOptionsFitOverlayOptions,
    streamBinStatsOptionsXLabel,
    streamParamsOptionsPanel,
    streamParamsOptionsWorkspace,
    streamParamsOutputOptions,
    streamBin2dOptionsPanel,
    streamBin2dOptionsWorkspace,
    streamBin2dOptionsOutputOptions,
    streamBin2dOptionsXLabel,
    streamBin2dOptionsYLabel,
    activeRawStreamSubscriptions,
    activeStreamAnalysisWorkspaceSubscriptions,
  };
}

