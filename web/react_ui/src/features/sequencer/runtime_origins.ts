/**
 * Which step sets each name that only exists while running (loop variables,
 * assign, save_as, ...), so a value chip can say where it comes from and a
 * name no step sets can be flagged as a likely typo.
 */

import { parse } from "yaml";
import { forSourceText } from "./outline";
import { asRecord, has, own } from "./records";
import type { SequencerStepOutlineNode } from "./types";

export type RuntimeOrigin = {
  /** Short chip label: "loop", "index", "assign", "save_as", "extract", ... */
  label: string;
  /** Tooltip line, e.g. "loop variable of for at L15". */
  description: string;
  line: number;
  /** What it runs over, with `${...}` still in it (resolved for display). */
  over: string | null;
};

/** Runtime names by name; a `metric_*` key matches adaptive `<metric>_<stat>`. */
export type RuntimeOrigins = Record<string, RuntimeOrigin>;

const NAME_RE = /^[A-Za-z_]\w*(?:_\*)?$/;

/** The names a call step sets: `save_as`, `extract` (into `value` without one) and `assign`. */
function callTargets(snippet: string): Array<{ name: string; label: string }> {
  let parsed: unknown;
  try {
    parsed = parse(snippet);
  } catch {
    return [];
  }
  const step = asRecord(Array.isArray(parsed) ? parsed[0] : parsed);
  if (!step) {
    return [];
  }
  const out: Array<{ name: string; label: string }> = [];
  const saveAs = typeof step.save_as === "string" ? step.save_as : null;
  if (step.extract != null) {
    out.push({ name: saveAs ?? "value", label: "extract" });
  } else if (saveAs) {
    out.push({ name: saveAs, label: "save_as" });
  }
  for (const name of Object.keys(asRecord(step.assign) ?? {})) {
    out.push({ name, label: "assign" });
  }
  return out;
}

/** Names set while running, from the steps that set them (first one wins). */
export function buildRuntimeOrigins(outline: ReadonlyArray<SequencerStepOutlineNode>): RuntimeOrigins {
  const out: RuntimeOrigins = {};
  const add = (name: string | null | undefined, origin: RuntimeOrigin) => {
    const key = name?.trim();
    if (key && NAME_RE.test(key) && !has(out, key)) {
      out[key] = origin;
    }
  };
  const visit = (node: SequencerStepOutlineNode) => {
    const at = `${node.kind} at L${node.line}`;
    const origin = (label: string, description: string, over: string | null = null) => ({
      label,
      description,
      line: node.line,
      over,
    });
    for (const entry of node.forDetail?.bind ?? []) {
      add(
        entry.value,
        entry.name === "index"
          ? origin("index", `loop index of ${at}`)
          : origin("loop", `loop variable of ${at}`, forSourceText(node.forDetail!))
      );
    }
    for (const entry of node.assignDetail?.entries ?? []) {
      add(entry.name, origin("assign", `set by ${at}`));
    }
    if (node.adaptiveDetail) {
      for (const group of node.adaptiveDetail.space) {
        add(group.name, origin("adaptive", `parameter of ${at}, chosen per trial`));
      }
      add("score", origin("adaptive", `trial score of ${at}`));
      for (const metric of node.adaptiveDetail.metrics) {
        add(`${metric.name}_*`, origin("adaptive", `metric statistic of ${at}`));
      }
    }
    if (node.kind === "wait_until") {
      add("sample", origin("sample", `latest sample of ${at}`));
      add("samples", origin("sample", `samples of ${at}`));
    }
    if (node.kind === "call") {
      for (const target of callTargets(node.snippet)) {
        add(target.name, origin(target.label, `set from the response of ${at}`));
      }
    }
    node.children.forEach(visit);
  };
  outline.forEach(visit);
  return out;
}

/** The step that sets a runtime name, if any. */
export function findOrigin(name: string, origins: RuntimeOrigins | undefined): RuntimeOrigin | undefined {
  if (!origins) {
    return undefined;
  }
  const exact = own(origins, name);
  if (exact) {
    return exact;
  }
  const prefix = Object.keys(origins).find(
    (key) => key.endsWith("_*") && name.startsWith(key.slice(0, -1))
  );
  return prefix ? origins[prefix] : undefined;
}
