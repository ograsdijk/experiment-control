import {
  Badge,
  Button,
  Card,
  Group,
  Modal,
  ScrollArea,
  Select,
  Stack,
  Text,
} from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { IconRefresh } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type {
  StateMachineProcessRow,
  StateMachinesSummary,
} from "../features/state_machines/useStateMachinesController";
import { processStateColor } from "../features/runtime/helpers";
import { coerceParamValue, ParamInput } from "./ParamInput";

type Props = {
  opened: boolean;
  onClose: () => void;
  summary: StateMachinesSummary;
  rows: StateMachineProcessRow[];
  selectedProcessId: string | null;
  onSelectProcess: (processId: string) => void;
  onRefresh: () => Promise<unknown> | void;
  onRefreshProcess: (processId: string) => Promise<unknown> | void;
  onRefreshGraph: (processId: string) => Promise<unknown> | void;
  onExecuteAction: (
    processId: string,
    action: string,
    params: Record<string, unknown>
  ) => Promise<unknown> | void;
  colorScheme: "light" | "dark";
};

type GraphNode = {
  id: string;
  x: number;
  y: number;
  layer: number;
};

type GraphEdge = {
  id: string;
  from: string;
  to: string;
  note: string | null;
  actions: string[];
  fromOffsetY: number;
  toOffsetY: number;
  isBackEdge: boolean;
  backRank: number;
};

const NODE_WIDTH = 176;
const NODE_HEIGHT = 44;
const LAYER_GAP = 320;
const ROW_GAP = 92;
const PADDING_X = 56;
const PADDING_TOP = 92;
const PADDING_BOTTOM = 92;
const GRAPH_CANVAS_HEIGHT = 560;
const GRAPH_ZOOM_MIN = 0.5;
const GRAPH_ZOOM_MAX = 2.5;
const GRAPH_ZOOM_STEP = 0.1;

function clampGraphZoom(value: number): number {
  if (!Number.isFinite(value)) {
    return 1;
  }
  return Math.max(GRAPH_ZOOM_MIN, Math.min(GRAPH_ZOOM_MAX, value));
}

function formatDurationSeconds(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "n/a";
  }
  return `${value.toFixed(2)} s`;
}

function stateMachineStateColor(state: string | null | undefined): string {
  const normalized = String(state ?? "").trim().toUpperCase();
  if (normalized === "ERROR" || normalized === "FAULT") {
    return "red";
  }
  if (normalized.includes("OPERATING") || normalized.endsWith("_ON")) {
    return "teal";
  }
  if (normalized.includes("PREPARED")) {
    return "blue";
  }
  if (normalized === "SAFE" || normalized === "IDLE" || normalized === "STOPPED") {
    return "gray";
  }
  return "grape";
}

function shortActionName(action: string): string {
  const value = String(action ?? "").trim();
  if (!value) {
    return value;
  }
  const lastDot = value.lastIndexOf(".");
  return lastDot >= 0 ? value.slice(lastDot + 1) : value;
}

function edgeLabelShort(actions: string[], note: string | null): string {
  if (actions.length <= 0) {
    return String(note ?? "").trim();
  }
  const deduped = [...new Set(actions.map((entry) => shortActionName(entry)).filter(Boolean))];
  if (deduped.length <= 0) {
    return "";
  }
  if (deduped.length === 1) {
    return deduped[0];
  }
  if (deduped.length === 2) {
    return `${deduped[0]}, ${deduped[1]}`;
  }
  return `${deduped[0]}, ${deduped[1]} +${deduped.length - 2}`;
}

function summarizeActionsByTransition(row: StateMachineProcessRow): Map<string, string[]> {
  const map = new Map<string, string[]>();
  const graph = row.graph;
  if (!graph) {
    return map;
  }
  for (const action of graph.actions) {
    const actionName = String(action.name ?? "").trim();
    if (!actionName) {
      continue;
    }
    for (const transition of action.transitions ?? []) {
      const from = String(transition.from_state ?? "").trim();
      const to = String(transition.to_state ?? "").trim();
      if (!from || !to) {
        continue;
      }
      const key = `${from} -> ${to}`;
      const items = map.get(key) ?? [];
      if (!items.includes(actionName)) {
        items.push(actionName);
      }
      map.set(key, items);
    }
  }
  return map;
}

function buildGraphLayout(row: StateMachineProcessRow): {
  nodes: GraphNode[];
  edges: GraphEdge[];
  width: number;
  height: number;
} {
  const graph = row.graph;
  if (!graph) {
    return { nodes: [], edges: [], width: 920, height: 240 };
  }

  const stateSet = new Set<string>();
  for (const state of graph.states) {
    const value = String(state ?? "").trim();
    if (value) {
      stateSet.add(value);
    }
  }
  const transitions: GraphEdge[] = [];
  const actionsByTransition = summarizeActionsByTransition(row);
  for (const [idx, transition] of graph.transitions.entries()) {
    const from = String(transition.from_state ?? "").trim();
    const to = String(transition.to_state ?? "").trim();
    if (!from || !to) {
      continue;
    }
    stateSet.add(from);
    stateSet.add(to);
    transitions.push({
      id: `edge:${idx}:${from}:${to}`,
      from,
      to,
      note: String(transition.note ?? "").trim() || null,
      actions: actionsByTransition.get(`${from} -> ${to}`) ?? [],
      fromOffsetY: 0,
      toOffsetY: 0,
      isBackEdge: false,
      backRank: 0,
    });
  }

  const states = [...stateSet];
  if (states.length === 0) {
    return { nodes: [], edges: transitions, width: 920, height: 240 };
  }

  const indegree = new Map<string, number>(states.map((state) => [state, 0]));
  const adjacency = new Map<string, string[]>();
  for (const edge of transitions) {
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1);
    const out = adjacency.get(edge.from) ?? [];
    out.push(edge.to);
    adjacency.set(edge.from, out);
  }
  for (const out of adjacency.values()) {
    out.sort((a, b) => a.localeCompare(b));
  }

  const roots = states
    .filter((state) => (indegree.get(state) ?? 0) === 0)
    .sort((a, b) => a.localeCompare(b));
  const preferredStart = String(row.graph?.initial_state ?? "").trim();
  const hasPreferredStart = preferredStart.length > 0 && stateSet.has(preferredStart);
  const startStates = hasPreferredStart
    ? [preferredStart]
    : roots.length > 0
      ? roots
      : [states[0]];

  const layer = new Map<string, number>();
  const queue: Array<{ state: string; depth: number }> = startStates.map((state) => ({
    state,
    depth: 0,
  }));
  while (queue.length > 0) {
    const next = queue.shift();
    if (!next) {
      break;
    }
    const existing = layer.get(next.state);
    if (existing != null && existing <= next.depth) {
      continue;
    }
    layer.set(next.state, next.depth);
    for (const target of adjacency.get(next.state) ?? []) {
      queue.push({ state: target, depth: next.depth + 1 });
    }
  }

  let maxLayer = 0;
  for (const value of layer.values()) {
    if (value > maxLayer) {
      maxLayer = value;
    }
  }
  for (const state of states.sort((a, b) => a.localeCompare(b))) {
    if (layer.has(state)) {
      continue;
    }
    maxLayer += 1;
    layer.set(state, maxLayer);
  }

  const byLayer = new Map<number, string[]>();
  for (const state of states) {
    const depth = layer.get(state) ?? 0;
    const list = byLayer.get(depth) ?? [];
    list.push(state);
    byLayer.set(depth, list);
  }
  for (const list of byLayer.values()) {
    list.sort((a, b) => a.localeCompare(b));
  }

  const predecessors = new Map<string, string[]>();
  const successors = new Map<string, string[]>();
  for (const state of states) {
    predecessors.set(state, []);
    successors.set(state, []);
  }
  for (const edge of transitions) {
    predecessors.set(edge.to, [...(predecessors.get(edge.to) ?? []), edge.from]);
    successors.set(edge.from, [...(successors.get(edge.from) ?? []), edge.to]);
  }

  const layerPositions = () => {
    const map = new Map<string, number>();
    for (const [depth, entries] of byLayer.entries()) {
      entries.forEach((state, idx) => map.set(state, idx));
    }
    return map;
  };

  const sortLayerByBarycenter = (depth: number, direction: "forward" | "backward") => {
    const entries = [...(byLayer.get(depth) ?? [])];
    if (entries.length <= 1) {
      return;
    }
    const positions = layerPositions();
    const scored = entries.map((state, originalIdx) => {
      const related =
        direction === "forward"
          ? predecessors.get(state) ?? []
          : successors.get(state) ?? [];
      const neighborPositions = related
        .filter((other) => {
          const otherLayer = layer.get(other) ?? 0;
          return direction === "forward" ? otherLayer < depth : otherLayer > depth;
        })
        .map((other) => positions.get(other))
        .filter((value): value is number => typeof value === "number");
      const barycenter =
        neighborPositions.length > 0
          ? neighborPositions.reduce((sum, value) => sum + value, 0) /
            neighborPositions.length
          : null;
      return { state, originalIdx, barycenter };
    });
    scored.sort((a, b) => {
      if (a.barycenter == null && b.barycenter == null) {
        return a.originalIdx - b.originalIdx;
      }
      if (a.barycenter == null) {
        return 1;
      }
      if (b.barycenter == null) {
        return -1;
      }
      if (a.barycenter !== b.barycenter) {
        return a.barycenter - b.barycenter;
      }
      return a.originalIdx - b.originalIdx;
    });
    byLayer.set(
      depth,
      scored.map((item) => item.state)
    );
  };

  for (let pass = 0; pass < 8; pass += 1) {
    for (let depth = 1; depth <= maxLayer; depth += 1) {
      sortLayerByBarycenter(depth, "forward");
    }
    for (let depth = maxLayer - 1; depth >= 0; depth -= 1) {
      sortLayerByBarycenter(depth, "backward");
    }
  }

  const nodes: GraphNode[] = [];
  const layerValues = [...byLayer.keys()].sort((a, b) => a - b);
  let maxRows = 1;
  for (const depth of layerValues) {
    const rowsInLayer = byLayer.get(depth)?.length ?? 0;
    if (rowsInLayer > maxRows) {
      maxRows = rowsInLayer;
    }
  }
  for (const depth of layerValues) {
    const entries = byLayer.get(depth) ?? [];
    entries.forEach((state, idx) => {
      nodes.push({
        id: state,
        x: PADDING_X + depth * LAYER_GAP,
        y: PADDING_TOP + idx * ROW_GAP,
        layer: depth,
      });
    });
  }

  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const outgoing = new Map<string, GraphEdge[]>();
  const incoming = new Map<string, GraphEdge[]>();
  for (const edge of transitions) {
    outgoing.set(edge.from, [...(outgoing.get(edge.from) ?? []), edge]);
    incoming.set(edge.to, [...(incoming.get(edge.to) ?? []), edge]);
  }

  const offsetForIndex = (idx: number, total: number): number => {
    if (total <= 1) {
      return 0;
    }
    const gap = Math.max(4, Math.min(10, 28 / (total - 1)));
    return -((total - 1) * gap) / 2 + idx * gap;
  };

  for (const list of outgoing.values()) {
    list.sort((a, b) => {
      const an = nodeById.get(a.to);
      const bn = nodeById.get(b.to);
      const ay = an ? an.y : 0;
      const by = bn ? bn.y : 0;
      if (ay !== by) {
        return ay - by;
      }
      return a.id.localeCompare(b.id);
    });
    list.forEach((edge, idx) => {
      edge.fromOffsetY = offsetForIndex(idx, list.length);
    });
  }

  for (const list of incoming.values()) {
    list.sort((a, b) => {
      const an = nodeById.get(a.from);
      const bn = nodeById.get(b.from);
      const ay = an ? an.y : 0;
      const by = bn ? bn.y : 0;
      if (ay !== by) {
        return ay - by;
      }
      return a.id.localeCompare(b.id);
    });
    list.forEach((edge, idx) => {
      edge.toOffsetY = offsetForIndex(idx, list.length);
    });
  }

  const backEdgeRankByLayer = new Map<number, number>();
  for (const edge of transitions) {
    const fromLayer = layer.get(edge.from) ?? 0;
    const toLayer = layer.get(edge.to) ?? 0;
    edge.isBackEdge = toLayer <= fromLayer;
    if (!edge.isBackEdge) {
      edge.backRank = 0;
      continue;
    }
    const nextRank = backEdgeRankByLayer.get(fromLayer) ?? 0;
    edge.backRank = nextRank;
    backEdgeRankByLayer.set(fromLayer, nextRank + 1);
  }

  const width = Math.max(960, PADDING_X * 2 + (maxLayer + 1) * LAYER_GAP + NODE_WIDTH);
  const height = Math.max(
    300,
    PADDING_TOP + (maxRows - 1) * ROW_GAP + NODE_HEIGHT + PADDING_BOTTOM
  );
  return { nodes, edges: transitions, width, height };
}

function valueToDraftText(value: unknown): string {
  if (value == null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

type GraphCanvasProps = {
  graph: {
    nodes: GraphNode[];
    edges: GraphEdge[];
    width: number;
    height: number;
  };
  activeState: string;
  focusCurrentState: boolean;
  highlightedAction: string | null;
  colorScheme: "light" | "dark";
  height: number | string;
  zoom: number;
  onZoomChange: (zoom: number) => void;
};

function GraphCanvas({
  graph,
  activeState,
  focusCurrentState,
  highlightedAction,
  colorScheme,
  height,
  zoom,
  onZoomChange,
}: GraphCanvasProps) {
  const markerId = `sm-arrow-${useId().replace(/:/g, "")}`;
  const nodeById = useMemo(() => {
    const map = new Map<string, GraphNode>();
    for (const node of graph.nodes) {
      map.set(node.id, node);
    }
    return map;
  }, [graph.nodes]);
  const focusActive = focusCurrentState && activeState.length > 0;

  const focusedEdgeIds = useMemo(() => {
    if (!focusActive) {
      return null;
    }
    const ids = new Set<string>();
    for (const edge of graph.edges) {
      if (edge.from === activeState || edge.to === activeState) {
        ids.add(edge.id);
      }
    }
    return ids;
  }, [activeState, focusActive, graph.edges]);

  const highlightedActionEdgeIds = useMemo(() => {
    const action = String(highlightedAction ?? "").trim();
    if (!action) {
      return null;
    }
    const ids = new Set<string>();
    for (const edge of graph.edges) {
      if (edge.actions.includes(action)) {
        ids.add(edge.id);
      }
    }
    return ids;
  }, [graph.edges, highlightedAction]);

  const emphasizedEdgeIds = useMemo(() => {
    let ids = new Set<string>(graph.edges.map((edge) => edge.id));
    if (focusedEdgeIds) {
      ids = new Set([...ids].filter((id) => focusedEdgeIds.has(id)));
    }
    if (highlightedActionEdgeIds) {
      ids = new Set([...ids].filter((id) => highlightedActionEdgeIds.has(id)));
    }
    return ids;
  }, [focusedEdgeIds, graph.edges, highlightedActionEdgeIds]);

  const emphasizedNodeIds = useMemo(() => {
    const ids = new Set<string>();
    if (activeState) {
      ids.add(activeState);
    }
    for (const edge of graph.edges) {
      if (emphasizedEdgeIds.has(edge.id)) {
        ids.add(edge.from);
        ids.add(edge.to);
      }
    }
    return ids;
  }, [activeState, emphasizedEdgeIds, graph.edges]);

  const scaledWidth = Math.max(1, Math.round(graph.width * zoom));
  const scaledHeight = Math.max(1, Math.round(graph.height * zoom));

  return (
    <ScrollArea
      h={height}
      offsetScrollbars
      onWheel={(event) => {
        if (!(event.ctrlKey || event.metaKey)) {
          return;
        }
        event.preventDefault();
        const delta = event.deltaY < 0 ? GRAPH_ZOOM_STEP : -GRAPH_ZOOM_STEP;
        onZoomChange(clampGraphZoom(zoom + delta));
      }}
    >
      <svg width={scaledWidth} height={scaledHeight} style={{ display: "block" }}>
        <defs>
          <marker id={markerId} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 Z" fill={colorScheme === "dark" ? "#bfbfbf" : "#666"} />
          </marker>
        </defs>
        <g transform={`scale(${zoom})`} transform-origin="0 0">
        {graph.edges.map((edge) => {
          const from = nodeById.get(edge.from);
          const to = nodeById.get(edge.to);
          if (!from || !to) {
            return null;
          }
          const x1 = from.x + NODE_WIDTH;
          const y1 = from.y + NODE_HEIGHT / 2 + edge.fromOffsetY;
          const x2 = to.x;
          const y2 = to.y + NODE_HEIGHT / 2 + edge.toOffsetY;
          let d = "";
          let labelX = (x1 + x2) / 2;
          let labelY = (y1 + y2) / 2 - 6;
          if (edge.isBackEdge || x2 <= x1 + 24) {
            const laneOffset = 54 + Math.floor(edge.backRank / 2) * 34;
            const routeAbove = edge.backRank % 2 === 0;
            const yRouteRaw = routeAbove
              ? Math.min(y1, y2) - laneOffset
              : Math.max(y1, y2) + laneOffset;
            const yRoute = Math.max(18, Math.min(graph.height - 18, yRouteRaw));
            const lead = 42;
            const controlX1 = x1 + lead;
            const controlX2 = x2 - lead;
            const midX = (x1 + x2) / 2;
            d = `M ${x1} ${y1} C ${controlX1} ${y1}, ${controlX1} ${yRoute}, ${midX} ${yRoute} C ${controlX2} ${yRoute}, ${controlX2} ${y2}, ${x2} ${y2}`;
            labelX = midX;
            labelY = routeAbove ? yRoute - 8 : yRoute + 14;
          } else {
            const c1 = x1 + 108;
            const c2 = x2 - 108;
            d = `M ${x1} ${y1} C ${c1} ${y1}, ${c2} ${y2}, ${x2} ${y2}`;
            labelX = (x1 + x2) / 2;
            labelY = (y1 + y2) / 2 - 6;
          }
          const label = edgeLabelShort(edge.actions, edge.note);
          const emphasized = emphasizedEdgeIds.has(edge.id);
          const actionHighlighted =
            highlightedAction != null && edge.actions.includes(highlightedAction);
          const stroke = actionHighlighted
            ? colorScheme === "dark"
              ? "#5cb8ff"
              : "#1971c2"
            : colorScheme === "dark"
              ? "#b4b4b4"
              : "#555";
          const labelWidth = Math.max(22, Math.min(220, label.length * 6.3 + 10));
          return (
            <g key={edge.id}>
              <path
                d={d}
                fill="none"
                stroke={stroke}
                strokeWidth={actionHighlighted ? 2.3 : 1.6}
                opacity={emphasized ? 1 : 0.3}
                markerEnd={`url(#${markerId})`}
              />
              {label ? (
                <g opacity={emphasized ? 1 : 0.35}>
                  <rect
                    x={labelX - labelWidth / 2}
                    y={labelY - 12}
                    width={labelWidth}
                    height={16}
                    rx={4}
                    ry={4}
                    fill={colorScheme === "dark" ? "#1c1f24" : "#f7f7f7"}
                    stroke={colorScheme === "dark" ? "#4a4f57" : "#c8cdd3"}
                  />
                  <text
                    x={labelX}
                    y={labelY}
                    textAnchor="middle"
                    fontSize={11}
                    fill={colorScheme === "dark" ? "#dfe3e8" : "#2f3b4a"}
                  >
                    {label}
                  </text>
                </g>
              ) : null}
            </g>
          );
        })}
        {graph.nodes.map((node) => {
          const active = node.id === activeState;
          const emphasized = emphasizedNodeIds.has(node.id);
          return (
            <g key={node.id} opacity={emphasized ? 1 : 0.28}>
              <rect
                x={node.x}
                y={node.y}
                width={NODE_WIDTH}
                height={NODE_HEIGHT}
                rx={8}
                ry={8}
                fill={active ? (colorScheme === "dark" ? "#124a46" : "#d9f4f1") : (colorScheme === "dark" ? "#1f1f1f" : "#f7f7f7")}
                stroke={active ? (colorScheme === "dark" ? "#31b4a8" : "#0f766e") : (colorScheme === "dark" ? "#555" : "#c7c7c7")}
                strokeWidth={active ? 2 : 1}
              />
              <text
                x={node.x + NODE_WIDTH / 2}
                y={node.y + NODE_HEIGHT / 2 + 4}
                textAnchor="middle"
                fontSize={12}
                fill={colorScheme === "dark" ? "#f2f2f2" : "#2f2f2f"}
              >
                {node.id}
              </text>
            </g>
          );
        })}
        </g>
      </svg>
    </ScrollArea>
  );
}

export function StateMachinesModal({
  opened,
  onClose,
  summary,
  rows,
  selectedProcessId,
  onSelectProcess,
  onRefresh,
  onRefreshProcess,
  onRefreshGraph,
  onExecuteAction,
  colorScheme,
}: Props) {
  const compactLayout = useMediaQuery("(max-width: 72em)");
  const selectedRow =
    rows.find((row) => row.process.process_id === selectedProcessId) ?? rows[0] ?? null;
  const [selectedActionName, setSelectedActionName] = useState<string>("");
  const [actionParamValues, setActionParamValues] = useState<Record<string, string>>({});
  const [graphExpanded, setGraphExpanded] = useState(false);
  const [graphZoom, setGraphZoom] = useState(1);
  const [focusCurrentState, setFocusCurrentState] = useState(true);
  const [highlightSelectedActionPath, setHighlightSelectedActionPath] = useState(false);
  const actionSelectionRef = useRef<{ processId: string | null; action: string | null }>({
    processId: null,
    action: null,
  });

  useEffect(() => {
    if (!selectedRow) {
      return;
    }
    if (selectedProcessId !== selectedRow.process.process_id) {
      onSelectProcess(selectedRow.process.process_id);
    }
  }, [onSelectProcess, selectedProcessId, selectedRow]);

  useEffect(() => {
    if (!selectedRow) {
      setSelectedActionName("");
      setActionParamValues({});
      actionSelectionRef.current = { processId: null, action: null };
      return;
    }
    const actions = selectedRow.binding.actionMembers;
    if (actions.length === 0) {
      setSelectedActionName("");
      setActionParamValues({});
      actionSelectionRef.current = {
        processId: selectedRow.process.process_id,
        action: null,
      };
      return;
    }
    const existing = actions.some((member) => member.name === selectedActionName);
    const nextAction = existing ? selectedActionName : String(actions[0].name ?? "");
    if (nextAction !== selectedActionName) {
      setSelectedActionName(nextAction);
    }
    const previous = actionSelectionRef.current;
    const changed =
      previous.processId !== selectedRow.process.process_id || previous.action !== nextAction;
    if (changed) {
      const selectedMember = actions.find((member) => member.name === nextAction) ?? null;
      const nextParams: Record<string, string> = {};
      for (const param of selectedMember?.params ?? []) {
        nextParams[param.name] = valueToDraftText(param.default);
      }
      setActionParamValues(nextParams);
    }
    actionSelectionRef.current = {
      processId: selectedRow.process.process_id,
      action: nextAction || null,
    };
  }, [selectedActionName, selectedRow]);

  const selectedAction = useMemo(() => {
    if (!selectedRow || !selectedActionName) {
      return null;
    }
    return selectedRow.binding.actionMembers.find((m) => m.name === selectedActionName) ?? null;
  }, [selectedActionName, selectedRow]);

  const graph = useMemo(() => (selectedRow ? buildGraphLayout(selectedRow) : null), [selectedRow]);
  const activeState = String(selectedRow?.status?.state ?? "").trim();
  const highlightedAction = highlightSelectedActionPath
    ? String(selectedActionName ?? "").trim() || null
    : null;

  useEffect(() => {
    setGraphZoom(1);
  }, [selectedRow?.process.process_id]);

  return (
    <Modal opened={opened} onClose={onClose} title="State Machines" size="clamp(56rem, 92vw, 96rem)" centered>
      <Stack gap="sm">
        <Group justify="space-between">
          <Group gap="xs">
            <Badge variant="light" color="gray">{summary.total} total</Badge>
            <Badge variant="light" color="teal">{summary.active} active</Badge>
            <Badge variant="light" color="red">{summary.error} error</Badge>
            <Badge variant="light" color="yellow">{summary.stale} stale</Badge>
          </Group>
          <Button size="xs" variant="light" leftSection={<IconRefresh size={14} />} onClick={() => { void onRefresh(); }}>
            Refresh
          </Button>
        </Group>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: compactLayout ? "1fr" : "minmax(16rem, 19rem) minmax(0, 1fr)",
            gap: "var(--mantine-spacing-sm)",
            alignItems: "stretch",
          }}
        >
          <Card p="xs" radius="sm" style={{ border: "1px solid var(--card-border)", minWidth: 0 }}>
            <ScrollArea h={compactLayout ? 220 : 640}>
              <Stack gap="xs">
                {rows.length === 0 ? (
                  <Text size="sm" c="dimmed">No state-machine processes detected.</Text>
                ) : rows.map((row) => {
                  const active = selectedRow?.process.process_id === row.process.process_id;
                  return (
                    <Card
                      key={row.process.process_id}
                      p="xs"
                      radius="sm"
                      withBorder
                      style={{ cursor: "pointer", borderColor: active ? "var(--mantine-color-teal-5)" : undefined }}
                      onClick={() => onSelectProcess(row.process.process_id)}
                    >
                      <Stack gap={4}>
                        <Group justify="space-between" gap="xs" wrap="nowrap">
                          <Text fw={600} size="sm" truncate>{row.process.process_id}</Text>
                          <Badge size="xs" variant="light" color={processStateColor(row.process.state)}>{row.process.state}</Badge>
                        </Group>
                        <Group gap="xs" wrap="wrap">
                          <Badge size="xs" variant="light" color={stateMachineStateColor(row.status?.state)}>{row.status?.state ?? "n/a"}</Badge>
                          <Badge size="xs" variant="outline" color={row.stale ? "yellow" : "gray"}>{row.stale ? "stale" : "fresh"}</Badge>
                        </Group>
                        <Text size="xs" c="dimmed">hb age {formatDurationSeconds(row.process.hb_age_s)}</Text>
                      </Stack>
                    </Card>
                  );
                })}
              </Stack>
            </ScrollArea>
          </Card>

          <Card p="xs" radius="sm" style={{ border: "1px solid var(--card-border)", minWidth: 0 }}>
            {!selectedRow ? (
              <Text size="sm" c="dimmed">Select a state-machine process.</Text>
            ) : (
              <Stack gap="sm">
                <Group justify="space-between" align="center">
                  <Text fw={600}>{selectedRow.process.process_id}</Text>
                  <Group gap="xs">
                    <Button size="xs" variant="light" leftSection={<IconRefresh size={14} />} loading={selectedRow.statusLoading} onClick={() => { void onRefreshProcess(selectedRow.process.process_id); }}>
                      Status
                    </Button>
                    <Button size="xs" variant="light" leftSection={<IconRefresh size={14} />} loading={selectedRow.graphLoading} onClick={() => { void onRefreshGraph(selectedRow.process.process_id); }}>
                      Graph
                    </Button>
                    <Button
                      size="xs"
                      variant="light"
                      disabled={!graph || !selectedRow.graph}
                      onClick={() => setGraphExpanded(true)}
                    >
                      Expand graph
                    </Button>
                  </Group>
                </Group>
                <Group gap="xs" wrap="wrap">
                  <Badge variant="light" color={stateMachineStateColor(selectedRow.status?.state)}>{selectedRow.status?.state ?? "n/a"}</Badge>
                  <Badge variant="outline" color={selectedRow.stale ? "yellow" : "gray"}>status age {formatDurationSeconds(selectedRow.statusAgeS)}</Badge>
                  {selectedRow.status?.last_error ? <Badge variant="light" color="red">{selectedRow.status.last_error}</Badge> : null}
                </Group>
                <Group gap="xs" wrap="wrap">
                  <Button
                    size="xs"
                    variant="light"
                    onClick={() => setGraphZoom((prev) => clampGraphZoom(prev - GRAPH_ZOOM_STEP))}
                  >
                    -
                  </Button>
                  <Button
                    size="xs"
                    variant="light"
                    onClick={() => setGraphZoom(1)}
                  >
                    Reset
                  </Button>
                  <Button
                    size="xs"
                    variant="light"
                    onClick={() => setGraphZoom((prev) => clampGraphZoom(prev + GRAPH_ZOOM_STEP))}
                  >
                    +
                  </Button>
                  <Text size="xs" c="dimmed">{Math.round(graphZoom * 100)}%</Text>
                </Group>
                <Group gap="xs" wrap="wrap">
                  <Button
                    size="xs"
                    variant={focusCurrentState ? "filled" : "light"}
                    color={focusCurrentState ? "teal" : "gray"}
                    disabled={!activeState}
                    onClick={() => setFocusCurrentState((prev) => !prev)}
                  >
                    Focus current state
                  </Button>
                  <Button
                    size="xs"
                    variant={highlightSelectedActionPath ? "filled" : "light"}
                    color={highlightSelectedActionPath ? "blue" : "gray"}
                    disabled={!selectedActionName}
                    onClick={() => setHighlightSelectedActionPath((prev) => !prev)}
                  >
                    Highlight selected action
                  </Button>
                </Group>

                {selectedRow.graphError ? <Text size="xs" c="red">{selectedRow.graphError}</Text> : null}
                {!selectedRow.graph || !graph ? (
                  <Text size="sm" c="dimmed">No graph metadata reported by this process.</Text>
                ) : (
                  <GraphCanvas
                    graph={graph}
                    activeState={activeState}
                    focusCurrentState={focusCurrentState}
                    highlightedAction={highlightedAction}
                    colorScheme={colorScheme}
                    height={GRAPH_CANVAS_HEIGHT}
                    zoom={graphZoom}
                    onZoomChange={setGraphZoom}
                  />
                )}

                {selectedRow.binding.actionMembers.length > 0 ? (
                  <Card p="xs" radius="sm" withBorder>
                    <Stack gap="xs">
                      <Text size="sm" fw={600}>Run Action</Text>
                      <Select
                        size="xs"
                        data={selectedRow.binding.actionMembers.map((m) => ({ value: m.name, label: m.name }))}
                        value={selectedActionName || null}
                        onChange={(value) => setSelectedActionName(String(value ?? ""))}
                        allowDeselect={false}
                        searchable
                      />
                      {(selectedAction?.params ?? []).map((param) => (
                        <ParamInput
                          key={param.name}
                          param={param}
                          value={actionParamValues[param.name] ?? ""}
                          onChange={(next) =>
                            setActionParamValues((prev) => ({ ...prev, [param.name]: next }))
                          }
                        />
                      ))}
                      <Group justify="flex-end">
                        <Button
                          size="xs"
                          onClick={async () => {
                            if (!selectedRow || !selectedActionName) {
                              return;
                            }
                            const params: Record<string, unknown> = {};
                            for (const param of selectedAction?.params ?? []) {
                              const raw = String(actionParamValues[param.name] ?? "").trim();
                              if (!raw) {
                                if (param.required) {
                                  notifications.show({
                                    color: "red",
                                    title: "Missing parameter",
                                    message: `${param.name} is required`,
                                  });
                                  return;
                                }
                                continue;
                              }
                              params[param.name] = coerceParamValue(raw, param);
                            }
                            await onExecuteAction(
                              selectedRow.process.process_id,
                              selectedActionName,
                              params
                            );
                          }}
                        >
                          Execute
                        </Button>
                      </Group>
                    </Stack>
                  </Card>
                ) : null}
              </Stack>
            )}
          </Card>
        </div>
      </Stack>

      <Modal
        opened={graphExpanded}
        onClose={() => setGraphExpanded(false)}
        title={`State Graph ${selectedRow?.process.process_id ?? ""}`}
        size="clamp(64rem, 96vw, 120rem)"
        centered
      >
        {!selectedRow?.graph || !graph ? (
          <Text size="sm" c="dimmed">No graph metadata reported by this process.</Text>
        ) : (
          <GraphCanvas
            graph={graph}
            activeState={activeState}
            focusCurrentState={focusCurrentState}
            highlightedAction={highlightedAction}
            colorScheme={colorScheme}
            height="78vh"
            zoom={graphZoom}
            onZoomChange={setGraphZoom}
          />
        )}
      </Modal>
    </Modal>
  );
}
