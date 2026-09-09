import type { StreamAnalysisWorkspaceConfig } from "./types";

/**
 * Human-facing names for DAG outputs.
 *
 * `output_id` is the stable programmatic identity — it is what panels
 * persist, what the analysis process keys on, and what appears in the
 * DAG editor. It is also developer-oriented
 * (`fluor_integral_div_abs_integral_vs_scan`), which makes a poor plot
 * title.
 *
 * Two layers fix that without touching the identity: an optional `label`
 * on the output, edited in the DAG modal and stored with the workspace,
 * and this derivation for every output that has none. The derived form
 * cannot invent "Normalized fluorescence" — only a person can — but it
 * turns an id into something readable for free.
 */

/**
 * Word-level expansions. Deliberately small and lab-specific: this is a
 * readability aid, not a dictionary. An unknown token passes through
 * unchanged, which is always a safe outcome.
 */
const TOKEN_EXPANSIONS: Record<string, string> = {
  abs: "absorption",
  fluor: "fluorescence",
  div: "/",
  avg: "average",
  std: "std",
  sem: "sem",
  idx: "index",
  ts: "timestamp",
};

/** Kept lowercase mid-sentence so the result reads as a phrase. */
const MINOR_WORDS = new Set(["vs", "of", "per", "and", "to", "over"]);

export function prettifyOutputId(outputId: string | null | undefined): string {
  const id = String(outputId ?? "").trim();
  if (!id) {
    return "";
  }
  const parts = id.split(/[_\s]+/).filter((part) => part.length > 0);
  if (parts.length === 0) {
    return id;
  }
  const words = parts.map((part) => {
    const lower = part.toLowerCase();
    if (TOKEN_EXPANSIONS[lower]) {
      return TOKEN_EXPANSIONS[lower];
    }
    if (MINOR_WORDS.has(lower)) {
      return lower;
    }
    // Digits and mixed case (2d, PMT1) are left as the author wrote them.
    return part;
  });
  const text = words.join(" ").replace(/\s+\/\s+/g, " / ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * What a person should read for this output: the explicit label, else a
 * name derived from the id, else the id itself.
 */
export function outputDisplayName(
  workspace: StreamAnalysisWorkspaceConfig | null,
  outputId: string | null | undefined
): string {
  const id = String(outputId ?? "").trim();
  if (!id) {
    return "";
  }
  const output = workspace?.publishOutputs.find(
    (entry) => entry.outputId === id
  );
  const label = typeof output?.label === "string" ? output.label.trim() : "";
  return label || prettifyOutputId(id) || id;
}

/**
 * Title for a panel whose title is still auto-generated: the bound
 * output's display name, or the generic fallback when nothing is bound.
 *
 * Only panels carrying `titleAuto` are retitled through this. A title
 * loaded from a profile has no such flag, so it is the user's and is
 * never rewritten.
 */
export function autoPanelTitle(
  workspace: StreamAnalysisWorkspaceConfig | null,
  outputId: string | null | undefined,
  fallback: string
): string {
  return outputDisplayName(workspace, outputId) || fallback;
}
