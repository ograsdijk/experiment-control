/**
 * Layout of the Timeline tab: the step outline as a flame graph (an icicle:
 * loops and other containers as bands above the steps they hold) that wraps
 * onto more lines instead of running off one, plus the run overview bar.
 *
 * Blocks all have the same width: most step times are unknown before a run,
 * and measured times only go into labels and the per-pass bar. A block is a
 * step, or one box holding the branches of an `if` / `parallel` side by side.
 * Everything here is pure; counts come from `sequencer.plan`, matched to the
 * outline by line (paths differ when the parser wraps the steps for
 * `requires:`), and live loop positions from `sequencer.status.loops`.
 */

import { passesPerRun, type SequencerLoopStatus, type SequencerPlan, type SequencerPlanStep } from "./plan";
import { forSourceText } from "./outline";
import type { SequencerOutlineMetadataEntry, SequencerStepOutlineNode } from "./types";

export const LOOP_KINDS: ReadonlySet<string> = new Set(["for", "repeat", "while", "adaptive"]);
const CONTAINER_KINDS: ReadonlySet<string> = new Set([
  "for",
  "repeat",
  "while",
  "adaptive",
  "atomic",
  "try",
  "finally",
  "use",
]);
const LANE_KINDS: ReadonlySet<string> = new Set(["if", "parallel"]);
/** Widest an if/parallel box gets, in blocks. */
const MAX_LANE_WIDTH = 3;

export type TimelineStep = {
  /** Outline id, or `plan:<path>` for steps of an expanded `use:`. */
  id: string;
  kind: string;
  line: number | null;
  /** Short label: what the step does (`fs740.flush`, `freq_hz, freq_step_index`). */
  summary: string;
  /** Second line: its main argument or source, if any. */
  detail: string | null;
  disabled: boolean;
  plan: SequencerPlanStep | null;
  /** Body of a container (for `try`, its steps then a `finally` block). */
  children: TimelineStep[];
  /** Branches of an `if` (then/else) or `parallel` (one per branch). */
  lanes: { label: string; steps: TimelineStep[] }[] | null;
  /** A `use:` step's plan path, when the plan lists its sub-sequence. */
  usePath: string | null;
};

/** `${name}` -> `name`; other text unchanged. */
export function unwrapTemplate(value: string): string {
  const trimmed = value.trim();
  const match = trimmed.match(/^["']?\$\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}["']?$/);
  if (match) {
    return match[1];
  }
  // YAML quotes are not part of the value (`"ch${n}_locked"`).
  const quoted = trimmed.match(/^(["'])(.*)\1$/);
  return quoted ? quoted[2] : trimmed;
}

function entryText(entries: readonly SequencerOutlineMetadataEntry[], max = 2): string | null {
  const shown = entries
    .filter((entry) => entry.value !== null && entry.value !== "" && !entry.value.includes("\n"))
    .slice(0, max)
    .map((entry) => `${entry.name} = ${unwrapTemplate(entry.value ?? "")}`);
  return shown.length ? shown.join(", ") : null;
}

function entryEnding(entries: readonly SequencerOutlineMetadataEntry[], suffixes: string[]): string | null {
  for (const suffix of suffixes) {
    const found = entries.find((entry) => entry.name === suffix || entry.name.endsWith(`.${suffix}`));
    if (found?.value) {
      return unwrapTemplate(found.value);
    }
  }
  return null;
}

/** What a block shows: a short name and an optional second line. */
export function stepLabels(node: SequencerStepOutlineNode): { name: string; detail: string | null } {
  const fallback = { name: node.summary ?? node.kind, detail: null };
  switch (node.kind) {
    case "call": {
      const detail = node.callDetail;
      const target = detail?.device || detail?.process;
      if (!detail || !target || !detail.action) {
        return fallback;
      }
      return { name: `${target}.${detail.action}`, detail: entryText(detail.params) };
    }
    case "sleep": {
      const duration = node.sleepDetail?.duration;
      return duration ? { name: unwrapTemplate(duration), detail: null } : fallback;
    }
    case "wait_until": {
      const detail = node.waitUntilDetail;
      const source = detail ? entryEnding(detail.sample, ["signal", "action"]) : null;
      const every = detail?.everyS ? `every ${unwrapTemplate(detail.everyS)}` : null;
      return source ? { name: source, detail: every } : { name: "wait", detail: every };
    }
    case "assign": {
      const vars = node.assignDetail?.variables ?? [];
      if (!vars.length) {
        return fallback;
      }
      return {
        name: vars.map((entry) => entry.name).join(", "),
        detail: vars[0].value ? `= ${unwrapTemplate(vars[0].value)}` : null,
      };
    }
    case "set": {
      const detail = node.setDetail;
      if (!detail?.name) {
        return fallback;
      }
      return {
        name: detail.device ? `${detail.device}.${detail.name}` : detail.name,
        detail: detail.value ? `= ${unwrapTemplate(detail.value)}` : null,
      };
    }
    case "set_context": {
      const detail = node.setContextDetail;
      if (!detail) {
        return fallback;
      }
      const streams = detail.streams.map((item) => `${item.device ?? "?"}/${item.stream ?? "?"}`);
      return {
        name: streams.length ? streams.join(", ") : "context",
        detail: detail.fields.length ? detail.fields.map((entry) => entry.name).join(", ") : null,
      };
    }
    case "for": {
      const detail = node.forDetail;
      if (!detail) {
        return fallback;
      }
      const vars = detail.bind.map((entry) => entry.value ?? entry.name);
      const source = forSourceText(detail);
      return {
        name: vars.length ? vars.join(", ") : "items",
        detail: source ? unwrapTemplate(source.replace(/^values\(inline: (.*)\)$/, "$1")) : null,
      };
    }
    case "repeat":
      return node.repeatDetail?.times
        ? { name: unwrapTemplate(node.repeatDetail.times), detail: null }
        : fallback;
    case "atomic":
      return { name: node.atomicDetail?.name ?? "atomic", detail: null };
    case "pause":
      return { name: node.pauseDetail?.reason ?? "pause", detail: null };
    case "try":
      return { name: "", detail: null };
    case "use":
      return { name: (node.summary ?? "").replace(/^use\s+/, "") || "use", detail: "sub-sequence" };
    case "adaptive":
      return { name: node.adaptiveDetail?.id ?? "adaptive", detail: node.adaptiveDetail?.controllerKind ?? null };
    default:
      return fallback;
  }
}

function pickPlan(
  byLine: Map<number, SequencerPlanStep[]>,
  node: SequencerStepOutlineNode
): SequencerPlanStep | null {
  const entries = byLine.get(node.line);
  if (!entries?.length) {
    return null;
  }
  return entries.find((entry) => entry.kind === node.kind) ?? null;
}

function laneLabel(node: SequencerStepOutlineNode): string {
  return node.summary ?? node.kind;
}

/** The plan path of a step's parent (`a.for.do[2]` -> `a`), for use sub-steps. */
export function parentPlanPath(path: string): string | null {
  const withoutIndex = path.replace(/\[\d+\]$/, "");
  if (withoutIndex === path) {
    return null;
  }
  if (withoutIndex.endsWith(".use.steps")) {
    return withoutIndex.slice(0, -".use.steps".length);
  }
  const match = withoutIndex.match(/^(.*)\.[a-z_]+\.(do|then|else|finally)$/);
  return match ? match[1] : null;
}

function branchOf(path: string): string | null {
  const match = path.replace(/\[\d+\]$/, "").match(/\.([a-z_]+)\.(do|then|else|finally)$/);
  return match ? match[2] : null;
}

/** Steps of an expanded `use:` step, rebuilt from the plan's paths. */
function useSubSteps(plan: SequencerPlan, usePath: string): TimelineStep[] {
  const byParent = new Map<string, SequencerPlanStep[]>();
  for (const entry of plan.steps) {
    if (!entry.path.startsWith(`${usePath}.use.steps[`)) {
      continue;
    }
    const parent = parentPlanPath(entry.path);
    if (parent === null) {
      continue;
    }
    const list = byParent.get(parent) ?? [];
    list.push(entry);
    byParent.set(parent, list);
  }
  const build = (entry: SequencerPlanStep): TimelineStep => {
    const kids = byParent.get(entry.path) ?? [];
    const step: TimelineStep = {
      id: `plan:${entry.path}`,
      kind: entry.kind,
      line: null,
      summary: (entry.summary ?? entry.kind).replace(new RegExp(`^${entry.kind}\\s+`), ""),
      detail: null,
      disabled: entry.disabled,
      plan: entry,
      children: [],
      lanes: null,
      usePath: null,
    };
    if (entry.kind === "if") {
      step.lanes = (["then", "else"] as const).map((branch) => ({
        label: branch,
        steps: kids.filter((kid) => branchOf(kid.path) === branch).map(build),
      }));
    } else if (entry.kind === "parallel") {
      step.lanes = kids.map((kid) => ({ label: kid.summary ?? kid.kind, steps: [build(kid)] }));
    } else if (entry.kind === "try") {
      const body = kids.filter((kid) => branchOf(kid.path) === "do").map(build);
      const fin = kids.filter((kid) => branchOf(kid.path) === "finally").map(build);
      step.children = fin.length ? [...body, finallyBlock(step.id, fin)] : body;
    } else {
      step.children = kids.map(build);
    }
    return step;
  };
  return (byParent.get(usePath) ?? []).map(build);
}

function finallyBlock(parentId: string, steps: TimelineStep[]): TimelineStep {
  return {
    id: `${parentId}#finally`,
    kind: "finally",
    line: steps[0]?.line ?? null,
    summary: "runs on finish, stop or error",
    detail: null,
    disabled: false,
    plan: null,
    children: steps,
    lanes: null,
    usePath: null,
  };
}

/** The outline as timeline steps, with plan counts where the plan has them. */
export function buildTimelineSteps(
  outline: readonly SequencerStepOutlineNode[],
  plan: SequencerPlan | null,
  byLine: Map<number, SequencerPlanStep[]>,
  expandedUses: ReadonlySet<string>
): TimelineStep[] {
  const build = (node: SequencerStepOutlineNode): TimelineStep => {
    const entry = pickPlan(byLine, node);
    const labels = stepLabels(node);
    const step: TimelineStep = {
      id: node.id,
      kind: node.kind,
      line: node.line,
      summary: labels.name,
      detail: labels.detail,
      disabled: node.disabled,
      plan: entry,
      children: [],
      lanes: null,
      usePath: node.kind === "use" && entry ? entry.path : null,
    };
    if (node.kind === "if") {
      step.lanes = (["then", "else"] as const).map((branch) => ({
        label: branch,
        steps: node.children.filter((child) => child.branchLabel === branch).map(build),
      }));
    } else if (node.kind === "parallel") {
      step.lanes = node.children.map((child) => ({ label: laneLabel(child), steps: [build(child)] }));
    } else if (node.kind === "try") {
      const body = node.children.filter((child) => child.branchLabel !== "finally").map(build);
      const fin = node.children.filter((child) => child.branchLabel === "finally").map(build);
      step.children = fin.length ? [...body, finallyBlock(step.id, fin)] : body;
    } else if (node.kind === "use") {
      if (plan && step.usePath && expandedUses.has(step.id)) {
        step.children = useSubSteps(plan, step.usePath);
      }
    } else {
      step.children = node.children.map(build);
    }
    return step;
  };
  return outline.map(build);
}

// ---------------------------------------------------------------------------
// Blocks and wrapping

export type TimelineBlock =
  | { type: "step"; step: TimelineStep; ancestors: TimelineStep[]; width: 1 }
  | { type: "lanes"; step: TimelineStep; ancestors: TimelineStep[]; width: number }
  | {
      type: "collapsed";
      id: string;
      steps: TimelineStep[];
      ancestors: TimelineStep[];
      width: 1;
    };

function isContainer(step: TimelineStep): boolean {
  return CONTAINER_KINDS.has(step.kind) && step.children.length > 0;
}

/** Steps inside an if/parallel branch, flattened for its small tiles. */
export function laneLeaves(steps: readonly TimelineStep[]): TimelineStep[] {
  const out: TimelineStep[] = [];
  for (const step of steps) {
    if (step.lanes) {
      out.push(step);
    } else if (isContainer(step)) {
      out.push(...laneLeaves(step.children));
    } else {
      out.push(step);
    }
  }
  return out;
}

export function timelineBlocks(steps: readonly TimelineStep[]): TimelineBlock[] {
  const out: TimelineBlock[] = [];
  const walk = (items: readonly TimelineStep[], ancestors: TimelineStep[]) => {
    for (const step of items) {
      if (step.lanes && LANE_KINDS.has(step.kind)) {
        const widest = Math.max(1, ...step.lanes.map((lane) => laneLeaves(lane.steps).length));
        out.push({ type: "lanes", step, ancestors, width: Math.min(MAX_LANE_WIDTH, widest) });
      } else if (isContainer(step)) {
        walk(step.children, [...ancestors, step]);
      } else {
        out.push({ type: "step", step, ancestors, width: 1 });
      }
    }
  };
  walk(steps, []);
  return out;
}

export function isDone(step: TimelineStep): boolean {
  const plan = step.plan;
  return Boolean(plan && plan.count > 0 && plan.runsDone >= plan.count);
}

function sameChain(a: readonly TimelineStep[], b: readonly TimelineStep[]): boolean {
  return a.length === b.length && a.every((step, index) => step === b[index]);
}

/** Steps in a row that must all be done before they fold into one block. */
export const COLLAPSE_MIN_STEPS = 3;

/**
 * Fold runs of finished steps into one block (the setup before the main
 * loop, say), unless the operator opened that block again. Only while a run
 * is going (`live`), when finished setup is dead weight. A step inside a loop
 * folds once the whole loop is finished (it ran all its runs and the run is
 * no longer inside it), and then folds together with its neighbours outside
 * the loop.
 */
export function collapseDoneBlocks(
  blocks: readonly TimelineBlock[],
  live: boolean,
  expanded: ReadonlySet<string>,
  loops: readonly SequencerLoopStatus[] = []
): TimelineBlock[] {
  if (!live) {
    return [...blocks];
  }
  const finishedLoop = (step: TimelineStep): boolean =>
    LOOP_KINDS.has(step.kind) && isDone(step) && liveLoopFor(step, loops) === null;
  // The ancestors a folded block keeps: finished loops are dropped with it.
  const chain = (block: TimelineBlock): TimelineStep[] =>
    block.ancestors.filter((ancestor) => !finishedLoop(ancestor));
  const out: TimelineBlock[] = [];
  let run: { block: Extract<TimelineBlock, { type: "step" }>; chain: TimelineStep[] }[] = [];
  const flush = () => {
    const id = run.length ? `done:${run[0].block.step.id}` : "";
    if (run.length >= COLLAPSE_MIN_STEPS && !expanded.has(id)) {
      out.push({
        type: "collapsed",
        id,
        steps: run.map((item) => item.block.step),
        ancestors: run[0].chain,
        width: 1,
      });
    } else {
      out.push(...run.map((item) => item.block));
    }
    run = [];
  };
  for (const block of blocks) {
    const kept = chain(block);
    const foldable =
      block.type === "step" &&
      isDone(block.step) &&
      !kept.some((ancestor) => LOOP_KINDS.has(ancestor.kind));
    if (foldable && (run.length === 0 || sameChain(run[0].chain, kept))) {
      run.push({ block, chain: kept });
      continue;
    }
    flush();
    if (foldable) {
      run.push({ block, chain: kept });
    } else {
      out.push(block);
    }
  }
  flush();
  return out;
}

export type PlacedBlock = { block: TimelineBlock; index: number; col: number; depth: number };

export type TimelineBand = {
  step: TimelineStep;
  depth: number;
  /** Grid columns, 1-based, end exclusive. */
  col0: number;
  col1: number;
  /** The container started on an earlier line / goes on to a later one. */
  continuesFromPrevious: boolean;
  continuesOnNext: boolean;
};

export type TimelineLine = {
  blocks: PlacedBlock[];
  bands: TimelineBand[];
  /** Rows of bands above the blocks (the deepest nesting on this line). */
  bandRows: number;
};

/**
 * Wrap blocks onto lines of `columns` blocks and lay the container bands
 * over them. A band split by a line break is drawn on each line with an open
 * edge where it continues.
 */
export function wrapTimeline(blocks: readonly TimelineBlock[], columns: number): TimelineLine[] {
  const cols = Math.max(1, Math.floor(columns));
  const first = new Map<TimelineStep, number>();
  const last = new Map<TimelineStep, number>();
  blocks.forEach((block, index) => {
    for (const ancestor of block.ancestors) {
      if (!first.has(ancestor)) {
        first.set(ancestor, index);
      }
      last.set(ancestor, index);
    }
  });
  const lines: TimelineLine[] = [];
  let current: PlacedBlock[] = [];
  let col = 1;
  blocks.forEach((block, index) => {
    const width = Math.min(block.width, cols);
    if (col > 1 && col + width - 1 > cols) {
      lines.push(finishLine(current, first, last));
      current = [];
      col = 1;
    }
    current.push({ block, index, col, depth: block.ancestors.length });
    col += width;
  });
  if (current.length) {
    lines.push(finishLine(current, first, last));
  }
  return lines;
}

function finishLine(
  placed: PlacedBlock[],
  first: Map<TimelineStep, number>,
  last: Map<TimelineStep, number>
): TimelineLine {
  const bands: TimelineBand[] = [];
  const bandRows = Math.max(0, ...placed.map((item) => item.depth));
  for (let depth = 0; depth < bandRows; depth += 1) {
    let open: { step: TimelineStep; start: PlacedBlock; end: PlacedBlock } | null = null;
    const close = () => {
      if (!open) {
        return;
      }
      bands.push({
        step: open.step,
        depth,
        col0: open.start.col,
        col1: open.end.col + Math.max(1, open.end.block.width),
        continuesFromPrevious: (first.get(open.step) ?? 0) < open.start.index,
        continuesOnNext: (last.get(open.step) ?? 0) > open.end.index,
      });
      open = null;
    };
    for (const item of placed) {
      const step = item.block.ancestors[depth];
      if (!step) {
        close();
        continue;
      }
      if (open && open.step === step) {
        open.end = item;
      } else {
        close();
        open = { step, start: item, end: item };
      }
    }
    close();
  }
  return { blocks: placed, bands, bandRows };
}

// ---------------------------------------------------------------------------
// Live state

/** The live loop entry for a timeline step, by line (or plan path in a use). */
export function liveLoopFor(
  step: TimelineStep,
  loops: readonly SequencerLoopStatus[]
): SequencerLoopStatus | null {
  return (
    loops.find((loop) =>
      step.line !== null && loop.line !== null
        ? loop.line === step.line && loop.kind === step.kind
        : step.plan !== null && loop.path === step.plan.path
    ) ?? null
  );
}

/**
 * Blocks already done in the pass the run is in: before the running block
 * and inside a loop the running step is also inside (or in no loop).
 */
export function doneInPass(
  blocks: readonly TimelineBlock[],
  activeId: string | null
): Set<string> {
  const out = new Set<string>();
  const activeIndex = blocks.findIndex(
    (block) => block.type !== "collapsed" && block.step.id === activeId
  );
  if (activeIndex < 0) {
    return out;
  }
  const activeAncestors = new Set(blocks[activeIndex].ancestors);
  for (let index = 0; index < activeIndex; index += 1) {
    const block = blocks[index];
    if (block.type === "collapsed") {
      continue;
    }
    const loops = block.ancestors.filter((ancestor) => LOOP_KINDS.has(ancestor.kind));
    const innermost = loops[loops.length - 1];
    if (!innermost || activeAncestors.has(innermost)) {
      out.add(block.step.id);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Overview bar

export type OverviewSegment = {
  key: string;
  kind: "steps" | "loop" | "finally";
  label: string;
  /** The loop, for a loop segment. */
  loop: TimelineStep | null;
  /** Share of the bar, 0..1. */
  share: number;
  /** Passes of a loop segment (null when unknown). */
  passes: number | null;
  /** Live: passes done / pass in progress (1-based) of a loop; fraction done otherwise. */
  passesDone: number;
  currentPass: number | null;
  fractionDone: number;
};

/** Smallest share a segment gets, so short setup stays visible. */
const MIN_SEGMENT_SHARE = 0.05;

function leafRuns(step: TimelineStep): { count: number; done: number } {
  if (step.lanes) {
    return step.lanes.reduce(
      (sum, lane) => {
        for (const leaf of laneLeaves(lane.steps)) {
          const r = leafRuns(leaf);
          sum.count += r.count;
          sum.done += r.done;
        }
        return sum;
      },
      { count: 0, done: 0 }
    );
  }
  if (step.children.length) {
    return step.children.reduce(
      (sum, child) => {
        const r = leafRuns(child);
        return { count: sum.count + r.count, done: sum.done + r.done };
      },
      { count: 0, done: 0 }
    );
  }
  const count = step.disabled ? 0 : step.plan?.count ?? 1;
  return { count, done: Math.min(count, step.plan?.runsDone ?? 0) };
}

/**
 * The bar above the timeline: runs of steps outside any loop make one
 * segment each, every outermost loop one segment divided into its passes,
 * and a `finally` block its own segment. Loops inside loops are not divided
 * (the headline names them). Shares follow step runs, with a floor.
 */
export function overviewSegments(
  blocks: readonly TimelineBlock[],
  loops: readonly SequencerLoopStatus[],
  live: boolean
): OverviewSegment[] {
  type Draft = {
    key: string;
    group: string;
    kind: OverviewSegment["kind"];
    loop: TimelineStep | null;
    steps: TimelineStep[];
  };
  const drafts: Draft[] = [];
  for (const block of blocks) {
    const loop = block.ancestors.find((ancestor) => LOOP_KINDS.has(ancestor.kind)) ?? null;
    const inFinally = block.ancestors.some((ancestor) => ancestor.kind === "finally");
    const kind: Draft["kind"] = loop ? "loop" : inFinally ? "finally" : "steps";
    const key = loop ? `loop:${loop.id}` : kind;
    const steps = block.type === "collapsed" ? block.steps : [block.step];
    const lastDraft = drafts[drafts.length - 1];
    if (lastDraft && lastDraft.group === key) {
      lastDraft.steps.push(...steps);
    } else {
      drafts.push({ key: `${key}:${drafts.length}`, group: key, kind, loop, steps: [...steps] });
    }
  }
  const weights = drafts.map((draft) => {
    const runs = draft.loop
      ? leafRuns(draft.loop)
      : draft.steps.reduce(
          (sum, step) => {
            const r = leafRuns(step);
            return { count: sum.count + r.count, done: sum.done + r.done };
          },
          { count: 0, done: 0 }
        );
    return runs;
  });
  const total = weights.reduce((sum, w) => sum + Math.max(1, w.count), 0);
  const raw = weights.map((w) => Math.max(MIN_SEGMENT_SHARE, Math.max(1, w.count) / total));
  const rawTotal = raw.reduce((a, b) => a + b, 0);
  return drafts.map((draft, index) => {
    const runs = weights[index];
    const loopLive = draft.loop ? liveLoopFor(draft.loop, loops) : null;
    const perRun = draft.loop ? passesPerRun(draft.loop.plan) : null;
    const passes = loopLive?.count ?? (perRun !== null ? Math.round(perRun) : null);
    const currentPass = live && loopLive ? Math.max(1, loopLive.iteration) : null;
    const passesDone =
      currentPass !== null
        ? currentPass - 1
        : passes !== null && runs.count > 0 && runs.done >= runs.count
          ? passes
          : 0;
    return {
      key: draft.key,
      kind: draft.kind,
      label:
        draft.kind === "loop"
          ? draft.loop?.summary ?? "loop"
          : draft.kind === "finally"
            ? "finally"
            : draft.steps.length === 1
              ? draft.steps[0].summary
              : `${draft.steps.length} steps`,
      loop: draft.loop,
      share: raw[index] / rawTotal,
      passes,
      passesDone,
      currentPass,
      fractionDone: runs.count > 0 ? runs.done / runs.count : 0,
    };
  });
}

// ---------------------------------------------------------------------------
// Time of one pass

export type PassPart = { id: string; label: string; kind: string; seconds: number };

function stepSeconds(step: TimelineStep): number | null {
  const plan = step.plan;
  if (!plan) {
    return null;
  }
  if (plan.meanS !== null) {
    return plan.meanS * plan.count;
  }
  if (plan.seconds !== undefined && plan.seconds !== null) {
    return plan.seconds * plan.count;
  }
  return null;
}

function subtreeSeconds(step: TimelineStep): { seconds: number; known: boolean } {
  const children = step.lanes ? step.lanes.flatMap((lane) => lane.steps) : step.children;
  if (!children.length || step.kind === "use") {
    const own = stepSeconds(step);
    return { seconds: own ?? 0, known: own !== null };
  }
  let seconds = 0;
  let known = false;
  for (const child of children) {
    const r = subtreeSeconds(child);
    seconds += r.seconds;
    known = known || r.known;
  }
  return { seconds, known };
}

/**
 * One pass of `loop` split into its direct children by time: measured mean
 * times where the run has them, else known sleep lengths. Null when nothing
 * in the body has a time.
 */
export function passBreakdown(loop: TimelineStep): { parts: PassPart[]; totalS: number } | null {
  const passes = loop.plan?.iterations;
  if (!passes || passes <= 0) {
    return null;
  }
  let anyKnown = false;
  const parts = loop.children.map((child) => {
    const r = subtreeSeconds(child);
    anyKnown = anyKnown || r.known;
    return { id: child.id, label: child.summary, kind: child.kind, seconds: r.seconds / passes };
  });
  if (!anyKnown) {
    return null;
  }
  const totalS = parts.reduce((sum, part) => sum + part.seconds, 0);
  return totalS > 0 ? { parts, totalS } : null;
}
