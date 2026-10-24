import { Badge, Group, ScrollArea, Stack, Text } from "@mantine/core";
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import type { ActiveStep } from "../features/sequencer/active_step";
import {
  passesPerRun,
  planStepsByLine,
  type SequencerLoopStatus,
  type SequencerPlan,
  type SequencerPlanStep,
} from "../features/sequencer/plan";
import {
  buildTimelineSteps,
  collapseDoneBlocks,
  doneInPass,
  isDone,
  laneLeaves,
  liveLoopFor,
  LOOP_KINDS,
  overviewSegments,
  passBreakdown,
  timelineAncestorIndex,
  timelineBlocks,
  wrapTimeline,
  type OverviewSegment,
  type TimelineBand,
  type TimelineBlock,
  type TimelineStep,
} from "../features/sequencer/timeline";
import type { SequencerStepOutlineNode } from "../features/sequencer/types";
import { formatDurationCompact } from "../features/sequencer/utils";
import { formatNumber } from "../features/sequencer/value_format";

type Props = {
  outline: readonly SequencerStepOutlineNode[];
  plan: SequencerPlan | null;
  planError: string | null;
  /** The run overrides have errors: the counts use the last valid ones. */
  overridesInvalid?: boolean;
  loops: readonly SequencerLoopStatus[];
  runtimeState: string;
  /** The running step in the editor text (null: none to show). */
  activeStep: ActiveStep | null;
  /** The editor holds other text than the loaded sequence. */
  textNotLoaded: boolean;
  /** Open a step (by line) in the Steps tab. */
  onOpenStep: (line: number) => void;
};

const LIVE_STATES = new Set(["RUNNING", "PAUSED", "STOP_REQUESTED"]);
/** Narrowest a block gets before the timeline wraps to another line. */
const MIN_BLOCK_PX = 150;
const COLUMN_GAP_PX = 6;
/** Grid rows a block spans below its bands. */
const BLOCK_ROWS = 3;
/** Loops with at most this many passes get one square per pass. */
const MAX_TRACK_CELLS = 48;

const KIND_CLASS: Record<string, string> = {
  call: "call",
  sleep: "sleep",
  for: "loop",
  repeat: "loop",
  while: "loop",
  adaptive: "adaptive",
  wait_until: "wait",
  set_context: "context",
  assign: "assign",
  set: "assign",
  pause: "pause",
  finally: "finally",
  use: "use",
};

function kindClass(kind: string): string {
  return `seq-tl-k-${KIND_CLASS[kind] ?? "other"}`;
}

/** Seconds as `41 ms`, `2.5 s`, `1:05`. */
export function formatSeconds(seconds: number): string {
  if (seconds < 1) {
    return `${Math.round(seconds * 1000)} ms`;
  }
  if (seconds < 60) {
    return `${formatNumber(Number(seconds.toPrecision(3)))} s`;
  }
  return formatDurationCompact(seconds);
}

function countText(plan: SequencerPlanStep | null, live: boolean): string {
  if (!plan) {
    return "";
  }
  if (plan.disabled) {
    return "off";
  }
  if (live) {
    return `${plan.runsDone}/${plan.count}${plan.certainty === "exact" ? "" : "+"}`;
  }
  if (plan.certainty === "unknown") {
    return "×?";
  }
  return `${plan.certainty === "approximate" ? "≈" : ""}×${plan.count.toLocaleString("en-US")}`;
}

function durationText(plan: SequencerPlanStep | null): string {
  if (!plan) {
    return "";
  }
  if (plan.meanS !== null) {
    return `avg ${formatSeconds(plan.meanS)}`;
  }
  if (plan.seconds !== undefined && plan.seconds !== null) {
    return formatSeconds(plan.seconds);
  }
  if (plan.timeoutS !== undefined && plan.timeoutS !== null) {
    return `≤ ${formatSeconds(plan.timeoutS)}`;
  }
  return "";
}

function stepTitle(step: TimelineStep): string {
  const plan = step.plan;
  const lines = [step.summary];
  if (plan) {
    lines.push(
      `Runs ${plan.count.toLocaleString("en-US")} time${plan.count === 1 ? "" : "s"}` +
        (plan.certainty === "exact" ? "" : ` (${plan.certainty})`)
    );
    if (plan.runsDone > 0) {
      lines.push(`Done ${plan.runsDone.toLocaleString("en-US")}` + (plan.meanS !== null ? `, avg ${formatSeconds(plan.meanS)}` : ""));
    }
    if (plan.note) {
      lines.push(plan.note);
    }
  }
  if (step.kind === "use" && step.usePath) {
    lines.push("Click to show the steps of this sub-sequence.");
  } else if (step.line !== null) {
    lines.push(`Line ${step.line}. Click to open in Steps.`);
  }
  return lines.join("\n");
}

function loopCountText(step: TimelineStep, live: SequencerLoopStatus | null): string {
  if (live) {
    return `${live.iteration}/${live.count ?? "?"}`;
  }
  const plan = step.plan;
  if (!plan || plan.iterationsCertainty === "unknown") {
    return LOOP_KINDS.has(step.kind) ? "×?" : "";
  }
  const passes = passesPerRun(plan);
  if (passes === null) {
    return "";
  }
  const rounded = Math.round(passes);
  const exactlyInteger = Math.abs(passes - rounded) < 1e-9;
  const prefix = plan.iterationsCertainty === "approximate" || !exactlyInteger ? "≈" : "";
  return `${prefix}×${rounded.toLocaleString("en-US")}`;
}

function trackCells(step: TimelineStep, live: SequencerLoopStatus | null): ("" | "done" | "cur")[] | null {
  const total = live?.count ?? (step.plan ? passesPerRun(step.plan) : null);
  if (total === null || total < 2 || total > MAX_TRACK_CELLS || !Number.isInteger(total)) {
    return null;
  }
  return Array.from({ length: total }, (_, index) => {
    if (!live) {
      return "";
    }
    if (index < live.iteration - 1) {
      return "done";
    }
    return index === live.iteration - 1 ? "cur" : "";
  });
}

function loopValueText(loop: SequencerLoopStatus, pass: number): string | null {
  const record = loop.values?.[pass - 1];
  if (!record) {
    return null;
  }
  const parts = Object.entries(record).map(
    ([name, value]) => `${name} ${typeof value === "number" ? formatNumber(value) : String(value)}`
  );
  return parts.length ? parts.join(", ") : null;
}

export function SequencerTimelinePane({
  outline,
  plan,
  planError,
  overridesInvalid = false,
  loops,
  runtimeState,
  activeStep,
  textNotLoaded,
  onOpenStep,
}: Props) {
  const live = LIVE_STATES.has(runtimeState) && Boolean(plan?.live);
  const [expandedUses, setExpandedUses] = useState<ReadonlySet<string>>(new Set());
  const [expandedDone, setExpandedDone] = useState<ReadonlySet<string>>(new Set());
  const toggle = (set: ReadonlySet<string>, id: string): Set<string> => {
    const next = new Set(set);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    return next;
  };

  const widthRef = useRef<HTMLDivElement>(null);
  const [columns, setColumns] = useState(6);
  useEffect(() => {
    const el = widthRef.current;
    if (!el || typeof ResizeObserver === "undefined") {
      return;
    }
    const update = () => {
      const width = el.clientWidth;
      if (width > 0) {
        setColumns(Math.max(2, Math.floor((width + COLUMN_GAP_PX) / (MIN_BLOCK_PX + COLUMN_GAP_PX))));
      }
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const byLine = useMemo(() => planStepsByLine(plan), [plan]);
  const steps = useMemo(
    () => buildTimelineSteps(outline, plan, byLine, expandedUses),
    [outline, plan, byLine, expandedUses]
  );
  const allBlocks = useMemo(() => timelineBlocks(steps), [steps]);
  const blocks = useMemo(
    () => collapseDoneBlocks(allBlocks, live, expandedDone, loops),
    [allBlocks, live, expandedDone, loops]
  );
  const lines = useMemo(() => wrapTimeline(blocks, columns), [blocks, columns]);
  const activeId = live ? activeStep?.leafId ?? null : null;
  const ancestorIndex = useMemo(() => timelineAncestorIndex(steps), [steps]);
  const activeAncestors = useMemo(
    () => new Set(activeId !== null ? ancestorIndex.get(activeId) ?? [] : []),
    [activeId, ancestorIndex]
  );
  const passDone = useMemo(
    () => doneInPass(blocks, activeId, activeAncestors),
    [blocks, activeId, activeAncestors]
  );
  // From the unfolded blocks: a finished loop keeps its segment and passes.
  const segments = useMemo(
    () => overviewSegments(allBlocks, loops, live),
    [allBlocks, loops, live]
  );
  // The loop the run is in, else the one with the most work (the main scan,
  // not a short setup loop before it).
  const mainLoop = useMemo(() => {
    const loopSegments = segments.filter((s) => s.kind === "loop");
    const current = loopSegments.find((s) => s.currentPass !== null);
    const biggest = loopSegments.reduce<OverviewSegment | null>(
      (best, s) => (best === null || s.share > best.share ? s : best),
      null
    );
    return (current ?? biggest)?.loop ?? null;
  }, [segments]);
  // Only with measured step times: before a run just the sleeps are known,
  // and a pass made of them alone would be misleading.
  const measured = plan?.estimatedS !== null && plan?.estimatedS !== undefined;
  const breakdown = useMemo(
    () => (mainLoop && measured ? passBreakdown(mainLoop) : null),
    [mainLoop, measured]
  );

  if (textNotLoaded) {
    return (
      <Text size="sm" c="dimmed">
        The timeline shows the loaded sequence. Load the editor YAML (or discard
        your edits) to see it here.
      </Text>
    );
  }
  if (!outline.length) {
    return (
      <Text size="sm" c="dimmed">
        No steps to show.
      </Text>
    );
  }

  const stepState = (step: TimelineStep): string => {
    if (step.disabled) {
      return "is-off";
    }
    if (step.id === activeId) {
      return "is-running";
    }
    if (live && (passDone.has(step.id) || isDone(step))) {
      return "is-done";
    }
    return "";
  };

  const renderTile = (step: TimelineStep, style: CSSProperties, mini = false): ReactNode => {
    const plan = step.plan;
    const expandable = step.kind === "use" && step.usePath !== null;
    const open = expandable
      ? () => setExpandedUses((prev) => toggle(prev, step.id))
      : step.line !== null
        ? () => onOpenStep(step.line as number)
        : undefined;
    if (mini) {
      return (
        <button
          key={step.id}
          type="button"
          className={`seq-tl-mini ${kindClass(step.kind)} ${stepState(step)}`}
          style={style}
          title={stepTitle(step)}
          onClick={open}
        >
          <span className="seq-tl-mini-name">{step.summary}</span>
          <span className="seq-tl-mini-count">{countText(plan, live)}</span>
        </button>
      );
    }
    return (
      <button
        key={step.id}
        type="button"
        className={`seq-tl-tile ${kindClass(step.kind)} ${stepState(step)}${
          plan?.certainty === "unknown" ? " is-unknown" : ""
        }`}
        style={style}
        title={stepTitle(step)}
        onClick={open}
      >
        <span className="seq-tl-kind">
          {step.kind}
          {step.id === activeId ? <span className="seq-tl-running">● running</span> : null}
        </span>
        <span className="seq-tl-name">{step.summary}</span>
        <span className="seq-tl-foot">
          <span className="seq-tl-count">{countText(plan, live)}</span>
          <span className="seq-tl-dur">{expandable ? "show steps ▸" : durationText(plan)}</span>
        </span>
      </button>
    );
  };

  const renderBlock = (
    block: TimelineBlock,
    col: number,
    depth: number,
    width: number
  ): ReactNode => {
    if (block.type === "collapsed") {
      const rows = BLOCK_ROWS;
      return (
        <button
          key={block.id}
          type="button"
          className="seq-tl-tile seq-tl-collapsed is-done"
          style={{ gridColumn: `${col} / span 1`, gridRow: `${depth + 1} / span ${rows}` }}
          title={block.steps.map((step) => step.summary).join("\n")}
          onClick={() => setExpandedDone((prev) => toggle(prev, block.id))}
        >
          <span className="seq-tl-kind">done</span>
          <span className="seq-tl-name">✓ {block.steps.length} steps</span>
          <span className="seq-tl-foot">
            <span className="seq-tl-dur">show ▸</span>
          </span>
        </button>
      );
    }
    if (block.type === "lanes") {
      const step = block.step;
      const lanes = step.lanes ?? [];
      const rows = Math.max(BLOCK_ROWS, lanes.length + 1);
      return (
        <div
          key={step.id}
          className={`seq-tl-lanes ${stepState(step)}`}
          style={{
            gridColumn: `${col} / span ${width}`,
            gridRow: `${depth + 1} / span ${rows}`,
          }}
        >
          <button
            type="button"
            className="seq-tl-lanes-head"
            title={stepTitle(step)}
            onClick={step.line !== null ? () => onOpenStep(step.line as number) : undefined}
          >
            <span className="seq-tl-kind">{step.kind}</span>
            <span className="seq-tl-name">{step.summary}</span>
            <span className="seq-tl-count">{countText(step.plan, live)}</span>
          </button>
          {lanes.map((lane, index) => {
            const leaves = laneLeaves(lane.steps);
            return (
              <div key={`${step.id}:${index}`} className="seq-tl-lane">
                <span className="seq-tl-lane-label" title={lane.label}>
                  {lane.label}
                </span>
                {leaves.length ? (
                  leaves.map((leaf) => renderTile(leaf, {}, true))
                ) : (
                  <span className="seq-tl-lane-empty">nothing</span>
                )}
              </div>
            );
          })}
        </div>
      );
    }
    const rows = BLOCK_ROWS;
    return renderTile(block.step, {
      gridColumn: `${col} / span 1`,
      gridRow: `${depth + 1} / span ${rows}`,
    });
  };

  const renderBand = (band: TimelineBand, key: string): ReactNode => {
    const step = band.step;
    const liveLoop = live ? liveLoopFor(step, loops) : null;
    const cells = LOOP_KINDS.has(step.kind) ? trackCells(step, liveLoop) : null;
    const count = LOOP_KINDS.has(step.kind) ? loopCountText(step, liveLoop) : "";
    const isActive = activeAncestors.has(step.id);
    const classes = [
      "seq-tl-band",
      kindClass(step.kind),
      band.continuesFromPrevious ? "cont-l" : "",
      band.continuesOnNext ? "cont-r" : "",
      isActive ? "is-active" : "",
      step.disabled ? "is-off" : "",
      step.plan?.iterationsCertainty === "unknown" || step.plan?.iterationsCertainty === "approximate"
        ? "is-open"
        : "",
    ]
      .filter(Boolean)
      .join(" ");
    const title = [
      step.summary,
      step.plan?.iterationsNote ?? null,
      liveLoop ? loopValueText(liveLoop, liveLoop.iteration) : null,
      step.line !== null ? `Line ${step.line}. Click to open in Steps.` : null,
    ]
      .filter(Boolean)
      .join("\n");
    const activate = () => {
      if (step.kind === "use") {
        setExpandedUses((prev) => toggle(prev, step.id));
      } else if (step.line !== null) {
        onOpenStep(step.line);
      }
    };
    return (
      <div
        key={key}
        className={classes}
        style={{ gridColumn: `${band.col0} / ${band.col1}`, gridRow: `${band.depth + 1}` }}
        title={title}
        role="button"
        tabIndex={0}
        onClick={activate}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            activate();
          }
        }}
      >
        {band.continuesFromPrevious ? <span className="seq-tl-wrap">↳</span> : null}
        <span className="seq-tl-band-kind">{step.kind}</span>
        <span className="seq-tl-band-label">{step.summary}</span>
        {count ? <span className="seq-tl-band-count">{count}</span> : null}
        {step.kind === "use" ? <span className="seq-tl-band-count">◂ fold</span> : null}
        {cells ? (
          <span className="seq-tl-track">
            {cells.map((cell, index) => (
              <span key={index} className={`seq-tl-cell ${cell}`} />
            ))}
          </span>
        ) : null}
        {band.continuesOnNext ? <span className="seq-tl-wrap">→</span> : null}
      </div>
    );
  };

  const activeLoops = live ? loops : [];

  return (
    <ScrollArea style={{ flex: 1, minHeight: 0 }} type="auto" offsetScrollbars>
      <Stack gap="sm" ref={widthRef}>
        <PlanSummary
          plan={plan}
          planError={planError}
          overridesInvalid={overridesInvalid}
          live={live}
        />
        {activeLoops.length > 0 ? (
          <Group gap="md" wrap="wrap">
            <Text size="sm" fw={600}>
              {activeLoops
                .map((loop) => `${loop.vars[0] ?? loop.kind} ${loop.iteration} of ${loop.count ?? "?"}`)
                .join(" · ")}
            </Text>
            <Text size="xs" c="dimmed" className="mono">
              {activeLoops
                .map((loop) => loopValueText(loop, loop.iteration))
                .filter(Boolean)
                .join(" · ")}
            </Text>
          </Group>
        ) : null}
        {segments.length > 1 || segments.some((s) => s.kind === "loop") ? (
          <OverviewBar segments={segments} loops={loops} live={live} />
        ) : null}
        <div className="seq-tl-lines">
          {lines.map((line, lineIndex) => {
            const rows = Math.max(
              ...line.blocks.map((item) =>
                item.depth +
                (item.block.type === "lanes"
                  ? Math.max(BLOCK_ROWS, (item.block.step.lanes?.length ?? 0) + 1)
                  : BLOCK_ROWS)
              )
            );
            return (
              <div
                key={lineIndex}
                className="seq-tl-line"
                style={{
                  gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
                  gridTemplateRows: `repeat(${rows}, 26px)`,
                }}
              >
                {line.bands.map((band, index) => renderBand(band, `${lineIndex}:b${index}`))}
                {line.blocks.map((item) =>
                  renderBlock(item.block, item.col, item.depth, item.width)
                )}
              </div>
            );
          })}
        </div>
        {breakdown && mainLoop ? (
          <PassBar loop={mainLoop} breakdown={breakdown} />
        ) : null}
        <Legend />
      </Stack>
    </ScrollArea>
  );
}

function PlanSummary({
  plan,
  planError,
  overridesInvalid,
  live,
}: {
  plan: SequencerPlan | null;
  planError: string | null;
  overridesInvalid: boolean;
  live: boolean;
}) {
  const overridesNote = overridesInvalid ? (
    <Text size="xs" c="yellow">
      The run overrides have errors: counts use the last valid overrides.
    </Text>
  ) : null;
  if (!plan) {
    return (
      <Stack gap={4}>
        <Text size="xs" c={planError ? "red" : "dimmed"}>
          {planError ? `Counts unavailable: ${planError}` : "Working out how often each step runs…"}
        </Text>
        {overridesNote}
      </Stack>
    );
  }
  const items: { value: string; label: string }[] = [];
  items.push({
    value:
      plan.totalRuns !== null
        ? `${plan.certainty === "approximate" ? "≈ " : ""}${plan.totalRuns.toLocaleString("en-US")}`
        : "?",
    label: "step runs",
  });
  if (plan.sleepS !== null && plan.sleepS > 0) {
    items.push({ value: formatSeconds(plan.sleepS), label: "in sleeps" });
  }
  if (plan.waitTimeoutS !== null && plan.waitTimeoutS > 0) {
    items.push({ value: `≤ ${formatSeconds(plan.waitTimeoutS)}`, label: "waiting, at most" });
  }
  if (plan.estimatedS !== null) {
    items.push({
      value: `≈ ${formatSeconds(plan.estimatedS)}`,
      label: live ? "at this run's step times" : "at the last run's step times",
    });
  }
  const reason =
    plan.unknownReason ??
    plan.approximateReason ??
    (plan.certainty === "inferred"
      ? plan.steps.find((step) => step.iterationsCertainty === "inferred")?.iterationsNote ?? null
      : null);
  return (
    <Stack gap={4}>
      <Group gap="xs" wrap="wrap">
        {items.map((item) => (
          <Badge key={item.label} size="lg" variant="light" color="gray" radius="sm">
            <span style={{ fontWeight: 700 }}>{item.value}</span>{" "}
            <span style={{ fontWeight: 400, textTransform: "none" }}>{item.label}</span>
          </Badge>
        ))}
      </Group>
      {reason ? (
        <Text size="xs" c="dimmed">
          {reason}
        </Text>
      ) : null}
      {planError ? (
        <Text size="xs" c="yellow">
          Counts may be out of date: {planError}
        </Text>
      ) : null}
      {overridesNote}
    </Stack>
  );
}

function OverviewBar({
  segments,
  loops,
  live,
}: {
  segments: OverviewSegment[];
  loops: readonly SequencerLoopStatus[];
  live: boolean;
}) {
  return (
    <div className="seq-tl-overview" aria-label="Run overview">
      {segments.map((segment) => {
        const liveLoop = segment.loop && live ? liveLoopFor(segment.loop, loops) : null;
        const passes = segment.passes;
        const divided = segment.kind === "loop" && passes !== null && passes >= 2 && passes <= 200;
        return (
          <div
            key={segment.key}
            className={`seq-tl-seg seq-tl-seg-${segment.kind}`}
            style={{ flexGrow: segment.share, flexBasis: 0 }}
            title={`${segment.label}${passes !== null && segment.kind === "loop" ? ` · ${passes} passes` : ""}`}
          >
            <div className="seq-tl-seg-bar">
              {divided ? (
                Array.from({ length: passes as number }, (_, index) => {
                  const pass = index + 1;
                  const state =
                    pass <= segment.passesDone ? "done" : pass === segment.currentPass ? "cur" : "";
                  const value = liveLoop ? loopValueText(liveLoop, pass) : null;
                  return (
                    <span
                      key={index}
                      className={`seq-tl-pass ${state}`}
                      title={`Pass ${pass} of ${passes}${value ? `: ${value}` : ""}`}
                    />
                  );
                })
              ) : (
                <span
                  className="seq-tl-pass done"
                  style={{ flexGrow: 0, width: `${Math.round(segment.fractionDone * 100)}%` }}
                />
              )}
            </div>
            <span className="seq-tl-seg-label">
              {segment.kind === "loop" && passes !== null ? `${passes} × ` : ""}
              {segment.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function PassBar({
  loop,
  breakdown,
}: {
  loop: TimelineStep;
  breakdown: { parts: { id: string; label: string; kind: string; seconds: number }[]; totalS: number };
}) {
  const top = [...breakdown.parts].sort((a, b) => b.seconds - a.seconds)[0];
  return (
    <Stack gap={4}>
      <Group justify="space-between" gap="xs">
        <Text size="xs">
          <b>One pass of {loop.summary}</b> ≈ {formatSeconds(breakdown.totalS)}
        </Text>
        {top ? (
          <Text size="xs" c="dimmed">
            {Math.round((top.seconds / breakdown.totalS) * 100)}% {top.label}
          </Text>
        ) : null}
      </Group>
      <div className="seq-tl-passbar">
        {breakdown.parts.map((part) => (
          <span
            key={part.id}
            className={`seq-tl-passpart ${kindClass(part.kind)}`}
            style={{ flexGrow: part.seconds, flexBasis: 0 }}
            title={`${part.label}: ${formatSeconds(part.seconds)} per pass`}
          />
        ))}
      </div>
    </Stack>
  );
}

function Legend() {
  const items: [string, string][] = [
    ["seq-tl-k-call", "call"],
    ["seq-tl-k-wait", "wait_until"],
    ["seq-tl-k-sleep", "sleep"],
    ["seq-tl-k-loop", "loop"],
    ["seq-tl-k-finally", "finally"],
    ["seq-tl-k-use", "use"],
  ];
  return (
    <Group gap="md" wrap="wrap">
      {items.map(([cls, label]) => (
        <Group key={label} gap={4} wrap="nowrap">
          <span className={`seq-tl-swatch ${cls}`} />
          <Text size="xs" c="dimmed">
            {label}
          </Text>
        </Group>
      ))}
      <Text size="xs" c="dimmed">
        → continues on the next line · striped end: count only known while running
      </Text>
    </Group>
  );
}
