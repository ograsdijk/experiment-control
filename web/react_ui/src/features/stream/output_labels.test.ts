import { describe, expect, it } from "vitest";
import {
  autoPanelTitle,
  outputDisplayName,
  prettifyOutputId,
} from "./output_labels";
import type { StreamAnalysisWorkspaceConfig } from "./types";

function workspace(
  outputs: Array<{ outputId: string; label?: string }>
): StreamAnalysisWorkspaceConfig {
  return {
    publishOutputs: outputs.map((entry) => ({
      outputId: entry.outputId,
      nodeId: "node",
      ...(entry.label ? { label: entry.label } : {}),
    })),
  } as unknown as StreamAnalysisWorkspaceConfig;
}

describe("prettifyOutputId", () => {
  it("expands lab shorthand and sentence-cases the result", () => {
    expect(prettifyOutputId("fluor_integral_vs_scan")).toBe(
      "Fluorescence integral vs scan"
    );
    expect(prettifyOutputId("abs_integral")).toBe("Absorption integral");
  });

  it("renders div as a slash without doubling the spaces", () => {
    expect(prettifyOutputId("fluor_integral_div_abs_integral")).toBe(
      "Fluorescence integral / absorption integral"
    );
  });

  it("passes unknown tokens through untouched", () => {
    expect(prettifyOutputId("PMT1_raw")).toBe("PMT1 raw");
  });

  it("returns an empty string for nothing", () => {
    expect(prettifyOutputId("")).toBe("");
    expect(prettifyOutputId(null)).toBe("");
    expect(prettifyOutputId(undefined)).toBe("");
  });
});

describe("outputDisplayName", () => {
  it("prefers an explicit label", () => {
    const ws = workspace([
      { outputId: "fluor_integral_vs_scan", label: "Normalized fluorescence" },
    ]);
    expect(outputDisplayName(ws, "fluor_integral_vs_scan")).toBe(
      "Normalized fluorescence"
    );
  });

  it("falls back to the derived name, then to the id", () => {
    const ws = workspace([{ outputId: "fluor_integral_vs_scan" }]);
    expect(outputDisplayName(ws, "fluor_integral_vs_scan")).toBe(
      "Fluorescence integral vs scan"
    );
    expect(outputDisplayName(null, "scan_x")).toBe("Scan x");
  });

  it("ignores a blank label", () => {
    const ws = workspace([{ outputId: "scan_x", label: "   " }]);
    expect(outputDisplayName(ws, "scan_x")).toBe("Scan x");
  });
});

describe("autoPanelTitle", () => {
  it("names the bound output", () => {
    const ws = workspace([{ outputId: "abs_integral", label: "Absorption" }]);
    expect(autoPanelTitle(ws, "abs_integral", "Scalar 3")).toBe("Absorption");
  });

  it("keeps the generic fallback when nothing is bound", () => {
    expect(autoPanelTitle(null, null, "Scalar 3")).toBe("Scalar 3");
  });
});
