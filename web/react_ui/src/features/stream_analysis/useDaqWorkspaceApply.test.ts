import { describe, expect, it } from "vitest";

import {
  staleCacheOutputIds,
  workspaceOutputBindings,
  type PanelCacheSlot,
} from "./useDaqWorkspaceApply";
import {
  defaultStreamAnalysisSettings,
  defaultStreamBinStatsSettings,
} from "../stream/workspace";
import type {
  StreamAnalysisWorkspaceConfig,
  StreamDagNodeConfig,
  StreamDagOutputConfig,
} from "../stream/types";

/**
 * Applying a DAG workspace used to blank every plot bound to it, whatever
 * the edit was. These cover the decision that replaced that: a panel drops
 * its cache only when a node behind an output it reads actually lost its
 * accumulator, or when the binding itself moved.
 */

function workspace(
  nodes: StreamDagNodeConfig[],
  outputs: StreamDagOutputConfig[]
): StreamAnalysisWorkspaceConfig {
  return {
    workspaceId: "ws",
    name: "ws",
    stream: { deviceId: "trace1", stream: "trace" },
    channelIndex: 0,
    analysis: defaultStreamAnalysisSettings(),
    binStats: defaultStreamBinStatsSettings(),
    graphNodes: nodes,
    publishOutputs: outputs,
    enabled: true,
  };
}

const NODES: StreamDagNodeConfig[] = [
  {
    nodeId: "src",
    op: "source.stream",
    params: { device_id: "trace1", stream: "trace" },
    inputs: {},
  },
  {
    nodeId: "ctx_x",
    op: "source.context_field",
    params: { field: "freq_hz" },
    inputs: {},
  },
  {
    nodeId: "integral",
    op: "trace.integrate",
    params: {},
    inputs: { trace: "src" },
  },
  {
    nodeId: "bin",
    op: "aggregate.bin_stats",
    params: { bin_count: 30 },
    inputs: { x: "ctx_x", y: "integral" },
  },
  {
    nodeId: "fit",
    op: "fit.from_hist_agg",
    params: { model: "gaussian", every_n: 1 },
    inputs: { hist: "bin" },
  },
];

const OUTPUTS: StreamDagOutputConfig[] = [
  { outputId: "bin_stats", nodeId: "bin" },
  { outputId: "fit_1d", nodeId: "fit" },
];

const BASE = workspace(NODES, OUTPUTS);

/** The histogram store of a bin-stats card, fed by its primary output. */
const HISTOGRAM: PanelCacheSlot = {
  slot: "binStats",
  outputIds: ["bin_stats"],
};

/** The fit-overlay store of the same card, fed by a separate output. */
const FIT_OVERLAY: PanelCacheSlot = {
  slot: "binStatsFitOverlay",
  outputIds: ["fit_1d"],
};

function stale(
  next: StreamAnalysisWorkspaceConfig,
  stateResetNodeIds: string[] | null,
  slot: PanelCacheSlot = HISTOGRAM
): string[] {
  return staleCacheOutputIds(
    slot,
    stateResetNodeIds,
    workspaceOutputBindings(BASE),
    workspaceOutputBindings(next)
  );
}

function isStale(
  next: StreamAnalysisWorkspaceConfig,
  stateResetNodeIds: string[] | null,
  slot: PanelCacheSlot = HISTOGRAM
): boolean {
  return stale(next, stateResetNodeIds, slot).length > 0;
}

describe("workspaceOutputBindings", () => {
  it("resolves each output to its node and kind", () => {
    const bindings = workspaceOutputBindings(BASE);
    expect(bindings.get("bin_stats")).toEqual({
      nodeId: "bin",
      kind: "hist_agg",
    });
    expect(bindings.get("fit_1d")).toEqual({ nodeId: "fit", kind: "fit_1d" });
  });

  it("skips an output whose node is missing from the graph", () => {
    const bindings = workspaceOutputBindings(
      workspace(NODES, [...OUTPUTS, { outputId: "orphan", nodeId: "gone" }])
    );
    expect(bindings.has("orphan")).toBe(false);
  });
});

describe("staleCacheOutputIds", () => {
  it("keeps the histogram when only the fit node was reset", () => {
    // Changing every_n on the fit resets the fit and nothing upstream.
    // The card reads both outputs, but into separate stores: the fit
    // overlay goes, the histogram stays.
    expect(isStale(BASE, ["fit"], HISTOGRAM)).toBe(false);
    expect(stale(BASE, ["fit"], FIT_OVERLAY)).toEqual(["fit_1d"]);
  });

  it("drops only the dead entries of a multi-output overlay store", () => {
    const overlays: PanelCacheSlot = {
      slot: "binStatsOverlay",
      outputIds: ["bin_stats", "fit_1d"],
    };
    expect(stale(BASE, ["fit"], overlays)).toEqual(["fit_1d"]);
  });

  it("keeps the cache when nothing the panel reads was reset", () => {
    // Bumping the fit's every_n resets the fit and nothing upstream: the
    // histogram the panel is showing is still the same accumulation.
    expect(isStale(BASE, [])).toBe(false);
  });

  it("keeps the cache for a publish-only edit", () => {
    // Attaching a label rewires nothing, so the runtime resets nothing.
    const labelled = workspace(NODES, [
      { outputId: "bin_stats", nodeId: "bin", label: "Resonance vs frequency" },
      { outputId: "fit_1d", nodeId: "fit" },
    ]);
    expect(isStale(labelled, [])).toBe(false);
  });

  it("clears when a node the panel reads was reset", () => {
    expect(isStale(BASE, ["bin", "fit"])).toBe(true);
  });

  it("ignores a reset of a node the panel does not read", () => {
    const withBranch = workspace(
      [
        ...NODES,
        {
          nodeId: "peak",
          op: "trace.integrate",
          params: {},
          inputs: { trace: "src" },
        },
      ],
      [...OUTPUTS, { outputId: "peak", nodeId: "peak" }]
    );
    expect(isStale(withBranch, ["peak"])).toBe(false);
  });

  it("clears when an output it reads is repointed at another node", () => {
    const repointed = workspace(
      [
        ...NODES,
        {
          nodeId: "bin2",
          op: "aggregate.bin_stats",
          params: { bin_count: 30 },
          inputs: { x: "ctx_x", y: "integral" },
        },
      ],
      [
        { outputId: "bin_stats", nodeId: "bin2" },
        { outputId: "fit_1d", nodeId: "fit" },
      ]
    );
    // The runtime reset nothing the panel reads by name -- the staleness is
    // in the binding, not the accumulator.
    expect(isStale(repointed, [])).toBe(true);
  });

  it("clears when an output it reads changed kind", () => {
    const rekinded = workspace(
      [
        ...NODES,
        {
          nodeId: "yhat",
          op: "fit.yhat",
          params: {},
          inputs: { fit: "fit" },
        },
      ],
      [
        { outputId: "bin_stats", nodeId: "bin" },
        { outputId: "fit_1d", nodeId: "yhat" },
      ]
    );
    expect(isStale(rekinded, [], FIT_OVERLAY)).toBe(true);
    // The histogram is bound to a different output and is unaffected.
    expect(isStale(rekinded, [], HISTOGRAM)).toBe(false);
  });

  it("clears when an output it reads disappeared", () => {
    const dropped = workspace(NODES, [{ outputId: "bin_stats", nodeId: "bin" }]);
    expect(isStale(dropped, [], FIT_OVERLAY)).toBe(true);
  });

  it("clears everything when the sync could not answer", () => {
    // null is the conservative fallback: runtime not ready, put failed, or
    // a revision conflict sent us down the reload path.
    expect(isStale(BASE, null)).toBe(true);
  });

  it("always clears the primary trace frame list", () => {
    // Trace frames accumulate by append, so a frame arriving between the
    // commit and this decision would interleave with pre-apply frames.
    expect(
      isStale(BASE, [], {
        slot: "traceFrames",
        outputIds: ["bin_stats"],
        alwaysReset: true,
      })
    ).toBe(true);
  });

  it("keeps a store that reads no outputs at all", () => {
    expect(isStale(BASE, [], { slot: "params", outputIds: [] })).toBe(false);
  });
});
