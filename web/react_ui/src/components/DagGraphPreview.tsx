import { useEffect, useMemo, useRef, useState } from "react";
import { ActionIcon, Group, Text } from "@mantine/core";
import { IconZoomIn, IconZoomOut, IconZoomReset } from "@tabler/icons-react";

export type DagGraphNode = {
  nodeId: string;
  op: string;
  inputs: Record<string, string>;
  params?: Record<string, unknown>;
};

export type DagGraphOutput = {
  outputId: string;
  nodeId: string;
};

type DagGraphPreviewProps = {
  nodes: DagGraphNode[];
  outputs?: DagGraphOutput[];
  resettableNodeIds?: Set<string>;
  onResetNode?: (nodeId: string) => void;
  onNodeClick?: (nodeId: string) => void;
  resetNodeBusyId?: string | null;
  height?: number;
};

type NodePos = {
  node: DagGraphNode;
  x: number;
  y: number;
};

const DAG_INPUT_PORTS: Record<string, string[]> = {
  "source.stream": [],
  "source.context_field": [],
  "source.telemetry_nearest": [],
  "scalar.add": ["a", "b"],
  "scalar.subtract": ["a", "b"],
  "scalar.multiply": ["a", "b"],
  "scalar.divide": ["a", "b"],
  "trace.divide": ["a", "b"],
  "trace.add_scalar": ["trace", "scalar"],
  "trace.subtract_scalar": ["trace", "scalar"],
  "trace.multiply_scalar": ["trace", "scalar"],
  "trace.divide_scalar": ["trace", "scalar"],
  "trace.crop": ["trace"],
  "trace.subtract_background": ["trace"],
  "trace.integrate": ["trace"],
  "aggregate.bin_stats": ["x", "y"],
  "aggregate.bin2d_stats": ["x", "y", "z"],
};

function nodeColor(op: string): string {
  if (op.startsWith("source.")) {
    return "#2563eb";
  }
  if (op.startsWith("trace.")) {
    return "#059669";
  }
  if (op.startsWith("aggregate.")) {
    return "#b45309";
  }
  return "#475569";
}

function paramAsText(params: Record<string, unknown> | undefined, key: string): string {
  const value = params?.[key];
  if (value === null || value === undefined) {
    return "";
  }
  const text = String(value).trim();
  return text;
}

function paramAsInt(params: Record<string, unknown> | undefined, key: string): number | null {
  const raw = params?.[key];
  if (raw === null || raw === undefined) {
    return null;
  }
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    return null;
  }
  return Math.trunc(value);
}

function paramAsNumber(params: Record<string, unknown> | undefined, key: string): number | null {
  const raw = params?.[key];
  if (raw === null || raw === undefined) {
    return null;
  }
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    return null;
  }
  return value;
}

function nodeMeta(node: DagGraphNode): { summary: string | null; warning: string | null } {
  if (node.op === "source.stream") {
    const deviceId = paramAsText(node.params, "device_id");
    const stream = paramAsText(node.params, "stream");
    const channel = paramAsInt(node.params, "channel_index");
    const modeRaw = paramAsText(node.params, "channel_mode").toLowerCase();
    const mode =
      modeRaw === "sum" ? "sum" : modeRaw === "average" || modeRaw === "mean" ? "average" : "single";
    const channelsText = paramAsText(node.params, "channel_indices");
    const channels = channelsText
      ? channelsText
          .split(/[\s,;]+/)
          .map((item) => item.trim())
          .filter((item) => item.length > 0)
      : [];
    const missing: string[] = [];
    if (!deviceId) {
      missing.push("device_id");
    }
    if (!stream) {
      missing.push("stream");
    }
    const summaryParts: string[] = [];
    if (deviceId || stream) {
      summaryParts.push(`${deviceId || "?"}.${stream || "?"}`);
    }
    if (mode === "single") {
      if (channels.length > 0) {
        summaryParts.push(`ch ${channels[0]}`);
      } else if (channel !== null) {
        summaryParts.push(`ch ${channel}`);
      }
    } else {
      summaryParts.push(`${mode}(${channels.length > 0 ? channels.join(",") : "all"})`);
    }
    return {
      summary: summaryParts.length > 0 ? summaryParts.join("  ") : null,
      warning: missing.length > 0 ? `missing: ${missing.join(", ")}` : null,
    };
  }
  if (node.op === "source.context_field") {
    const field = paramAsText(node.params, "field");
    return {
      summary: field ? `field: ${field}` : null,
      warning: field ? null : "missing: field",
    };
  }
  if (node.op === "source.telemetry_nearest") {
    const deviceId = paramAsText(node.params, "device_id");
    const signal = paramAsText(node.params, "signal");
    const maxDt = paramAsNumber(node.params, "max_dt_s");
    const missing: string[] = [];
    if (!deviceId) {
      missing.push("device_id");
    }
    if (!signal) {
      missing.push("signal");
    }
    const sourceText = `${deviceId || "?"}.${signal || "?"}`;
    const dtText =
      maxDt !== null && maxDt > 0 ? `  dt<=${maxDt.toFixed(2)}s` : "";
    return {
      summary: `${sourceText}${dtText}`,
      warning: missing.length > 0 ? `missing: ${missing.join(", ")}` : null,
    };
  }
  return { summary: null, warning: null };
}

export function DagGraphPreview({
  nodes,
  outputs = [],
  resettableNodeIds,
  onResetNode,
  onNodeClick,
  resetNodeBusyId = null,
  height = 420,
}: DagGraphPreviewProps) {
  const minZoom = 0.5;
  const maxZoom = 3.0;

  const containerRef = useRef<HTMLDivElement | null>(null);
  const panRef = useRef<{
    pointerId: number;
    startClientX: number;
    startClientY: number;
    startCenterX: number;
    startCenterY: number;
  } | null>(null);
  const [zoom, setZoom] = useState(1.0);
  const [viewCenter, setViewCenter] = useState<{ x: number; y: number } | null>(null);
  const [isPanning, setIsPanning] = useState(false);

  const layout = useMemo(() => {
    const cleanNodes = nodes.filter(
      (node) => String(node.nodeId ?? "").trim().length > 0
    );
    const byId = new Map(cleanNodes.map((node) => [node.nodeId, node]));
    const indexById = new Map(cleanNodes.map((node, idx) => [node.nodeId, idx]));
    const depthCache = new Map<string, number>();

    const depthFor = (nodeId: string, visiting = new Set<string>()): number => {
      if (depthCache.has(nodeId)) {
        return depthCache.get(nodeId) ?? 0;
      }
      if (visiting.has(nodeId)) {
        return 0;
      }
      const node = byId.get(nodeId);
      if (!node) {
        return 0;
      }
      visiting.add(nodeId);
      let maxDepth = 0;
      for (const src of Object.values(node.inputs ?? {})) {
        const sourceId = String(src ?? "").trim();
        if (!sourceId || !byId.has(sourceId)) {
          continue;
        }
        maxDepth = Math.max(maxDepth, depthFor(sourceId, visiting) + 1);
      }
      visiting.delete(nodeId);
      depthCache.set(nodeId, maxDepth);
      return maxDepth;
    };

    const layers = new Map<number, DagGraphNode[]>();
    for (const node of cleanNodes) {
      const depth = depthFor(node.nodeId);
      const list = layers.get(depth) ?? [];
      list.push(node);
      layers.set(depth, list);
    }
    for (const [, list] of layers.entries()) {
      list.sort((a, b) => (indexById.get(a.nodeId) ?? 0) - (indexById.get(b.nodeId) ?? 0));
    }

    const nodeW = 220;
    const nodeH = 98;
    const hGap = 72;
    const vGap = 18;
    const marginX = 20;
    const marginY = 16;

    const depthKeys = [...layers.keys()].sort((a, b) => a - b);
    const maxDepth = depthKeys.length > 0 ? Math.max(...depthKeys) : 0;
    const maxPerLayer = Math.max(1, ...[...layers.values()].map((v) => v.length));
    const naturalH = marginY * 2 + maxPerLayer * nodeH + Math.max(0, maxPerLayer - 1) * vGap;
    const viewH = Math.max(height, naturalH);

    const positioned = new Map<string, NodePos>();
    for (const depth of depthKeys) {
      const layerNodes = layers.get(depth) ?? [];
      const layerHeight =
        layerNodes.length * nodeH + Math.max(0, layerNodes.length - 1) * vGap;
      const yStart = (viewH - layerHeight) / 2;
      for (let i = 0; i < layerNodes.length; i += 1) {
        const node = layerNodes[i];
        positioned.set(node.nodeId, {
          node,
          x: marginX + depth * (nodeW + hGap),
          y: yStart + i * (nodeH + vGap),
        });
      }
    }

    const edges: Array<{
      fromX: number;
      fromY: number;
      toX: number;
      toY: number;
      valid: boolean;
      label: string;
    }> = [];
    for (const target of cleanNodes) {
      const to = positioned.get(target.nodeId);
      if (!to) {
        continue;
      }
      const expectedPorts =
        DAG_INPUT_PORTS[target.op] ?? Object.keys(target.inputs ?? {});
      const portCount = Math.max(1, expectedPorts.length);
      for (let portIndex = 0; portIndex < expectedPorts.length; portIndex += 1) {
        const port = expectedPorts[portIndex];
        const rawSource = target.inputs?.[port];
        const sourceId = String(rawSource ?? "").trim();
        const toY = to.y + ((portIndex + 1) * nodeH) / (portCount + 1);
        const from = sourceId ? positioned.get(sourceId) : undefined;
        if (from) {
          edges.push({
            fromX: from.x + nodeW,
            fromY: from.y + nodeH / 2,
            toX: to.x,
            toY,
            valid: true,
            label: port,
          });
          continue;
        }
        edges.push({
          fromX: to.x - 70,
          fromY: toY - 12,
          toX: to.x,
          toY,
          valid: false,
          label: `${port}: ${sourceId || "missing"}`,
        });
      }
    }

    const viewW = marginX * 2 + (maxDepth + 1) * nodeW + maxDepth * hGap;
    return { nodeW, nodeH, viewW, viewH, positioned, edges };
  }, [nodes, height]);

  const safeZoom = Math.max(minZoom, Math.min(maxZoom, zoom));

  const clampCenterForZoom = (
    center: { x: number; y: number },
    zoomValue: number
  ): { x: number; y: number } => {
    const safe = Math.max(minZoom, Math.min(maxZoom, zoomValue));
    const viewWidth = layout.viewW / safe;
    const viewHeight = layout.viewH / safe;
    const halfW = viewWidth * 0.5;
    const halfH = viewHeight * 0.5;
    const minX = halfW;
    const maxX = layout.viewW - halfW;
    const minY = halfH;
    const maxY = layout.viewH - halfH;
    const x = Math.min(Math.max(center.x, minX), maxX);
    const y = Math.min(Math.max(center.y, minY), maxY);
    return { x, y };
  };

  useEffect(() => {
    const fallback = { x: layout.viewW * 0.5, y: layout.viewH * 0.5 };
    setViewCenter((prev) => {
      const base = prev ?? fallback;
      const next = clampCenterForZoom(base, safeZoom);
      if (prev && Math.abs(prev.x - next.x) < 1e-9 && Math.abs(prev.y - next.y) < 1e-9) {
        return prev;
      }
      return next;
    });
  }, [layout.viewW, layout.viewH, safeZoom]);

  if (nodes.length <= 0) {
    return (
      <div className="plot-panel" style={{ height, display: "grid", placeItems: "center" }}>
        <span style={{ fontSize: 12, opacity: 0.75 }}>No nodes to render.</span>
      </div>
    );
  }

  const viewWidth = layout.viewW / safeZoom;
  const viewHeight = layout.viewH / safeZoom;
  const effectiveCenter = clampCenterForZoom(
    viewCenter ?? { x: layout.viewW * 0.5, y: layout.viewH * 0.5 },
    safeZoom
  );
  const viewX = effectiveCenter.x - viewWidth * 0.5;
  const viewY = effectiveCenter.y - viewHeight * 0.5;

  useEffect(() => {
    const el = containerRef.current;
    if (!el) {
      return;
    }
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      event.stopPropagation();
      const direction = event.deltaY > 0 ? -1 : 1;
      const nextZoom = Math.max(minZoom, Math.min(maxZoom, safeZoom + direction * 0.1));
      if (Math.abs(nextZoom - safeZoom) < 1e-9) {
        return;
      }
      const rect = el.getBoundingClientRect();
      const pxRaw = rect.width > 0 ? (event.clientX - rect.left) / rect.width : 0.5;
      const pyRaw = rect.height > 0 ? (event.clientY - rect.top) / rect.height : 0.5;
      const px = Math.min(Math.max(pxRaw, 0), 1);
      const py = Math.min(Math.max(pyRaw, 0), 1);

      const oldViewW = layout.viewW / safeZoom;
      const oldViewH = layout.viewH / safeZoom;
      const oldViewX = effectiveCenter.x - oldViewW * 0.5;
      const oldViewY = effectiveCenter.y - oldViewH * 0.5;
      const anchorX = oldViewX + px * oldViewW;
      const anchorY = oldViewY + py * oldViewH;

      const newViewW = layout.viewW / nextZoom;
      const newViewH = layout.viewH / nextZoom;
      const newViewX = anchorX - px * newViewW;
      const newViewY = anchorY - py * newViewH;
      setZoom(nextZoom);
      setViewCenter(
        clampCenterForZoom(
          {
            x: newViewX + newViewW * 0.5,
            y: newViewY + newViewH * 0.5,
          },
          nextZoom
        )
      );
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      el.removeEventListener("wheel", onWheel);
    };
  }, [effectiveCenter, layout.viewH, layout.viewW, safeZoom]);

  return (
    <div
      ref={containerRef}
      className="plot-panel"
      style={{
        height,
        overflow: "hidden",
        border: "1px solid var(--card-border)",
        borderRadius: 10,
        position: "relative",
        touchAction: "none",
        cursor: isPanning ? "grabbing" : "grab",
      }}
      onPointerDown={(event) => {
        if (event.button !== 0 && event.pointerType !== "touch") {
          return;
        }
        const target = event.target;
        if (
          target instanceof Element &&
          target.closest?.("[data-dag-clickable='true']")
        ) {
          return;
        }
        const el = containerRef.current;
        if (!el) {
          return;
        }
        panRef.current = {
          pointerId: event.pointerId,
          startClientX: event.clientX,
          startClientY: event.clientY,
          startCenterX: effectiveCenter.x,
          startCenterY: effectiveCenter.y,
        };
        setIsPanning(true);
        el.setPointerCapture(event.pointerId);
        event.preventDefault();
      }}
      onPointerMove={(event) => {
        const pan = panRef.current;
        if (!pan || pan.pointerId !== event.pointerId) {
          return;
        }
        const el = containerRef.current;
        if (!el) {
          return;
        }
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) {
          return;
        }
        const dx = event.clientX - pan.startClientX;
        const dy = event.clientY - pan.startClientY;
        const worldDx = (dx / rect.width) * viewWidth;
        const worldDy = (dy / rect.height) * viewHeight;
        setViewCenter(
          clampCenterForZoom(
            {
              x: pan.startCenterX - worldDx,
              y: pan.startCenterY - worldDy,
            },
            safeZoom
          )
        );
        event.preventDefault();
      }}
      onPointerUp={(event) => {
        const pan = panRef.current;
        if (!pan || pan.pointerId !== event.pointerId) {
          return;
        }
        const el = containerRef.current;
        if (el?.hasPointerCapture(event.pointerId)) {
          el.releasePointerCapture(event.pointerId);
        }
        panRef.current = null;
        setIsPanning(false);
      }}
      onPointerCancel={(event) => {
        const pan = panRef.current;
        if (!pan || pan.pointerId !== event.pointerId) {
          return;
        }
        const el = containerRef.current;
        if (el?.hasPointerCapture(event.pointerId)) {
          el.releasePointerCapture(event.pointerId);
        }
        panRef.current = null;
        setIsPanning(false);
      }}
    >
      <Group
        gap={6}
        style={{
          position: "absolute",
          top: 8,
          right: 8,
          zIndex: 2,
          background: "rgba(15,23,42,0.18)",
          borderRadius: 8,
          padding: 4,
        }}
      >
        <Text size="xs" c="#f8fafc" fw={700} style={{ minWidth: 44, textAlign: "center" }}>
          {Math.round(safeZoom * 100)}%
        </Text>
        <ActionIcon
          size="sm"
          variant="light"
          onClick={() => {
            const nextZoom = Math.max(minZoom, safeZoom - 0.1);
            if (Math.abs(nextZoom - safeZoom) < 1e-9) {
              return;
            }
            setZoom(nextZoom);
            setViewCenter(clampCenterForZoom(effectiveCenter, nextZoom));
          }}
          title="Zoom out"
        >
          <IconZoomOut size={14} />
        </ActionIcon>
        <ActionIcon
          size="sm"
          variant="light"
          onClick={() => {
            setZoom(1);
            setViewCenter({ x: layout.viewW * 0.5, y: layout.viewH * 0.5 });
          }}
          title="Fit / reset zoom"
        >
          <IconZoomReset size={14} />
        </ActionIcon>
        <ActionIcon
          size="sm"
          variant="light"
          onClick={() => {
            const nextZoom = Math.min(maxZoom, safeZoom + 0.1);
            if (Math.abs(nextZoom - safeZoom) < 1e-9) {
              return;
            }
            setZoom(nextZoom);
            setViewCenter(clampCenterForZoom(effectiveCenter, nextZoom));
          }}
          title="Zoom in"
        >
          <IconZoomIn size={14} />
        </ActionIcon>
      </Group>

      <svg
        width="100%"
        height="100%"
        viewBox={`${viewX} ${viewY} ${viewWidth} ${viewHeight}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          <marker
            id="dag-arrow"
            markerWidth="8"
            markerHeight="8"
            refX="7"
            refY="4"
            orient="auto"
            markerUnits="strokeWidth"
          >
            <path d="M0,0 L8,4 L0,8 z" fill="#64748b" />
          </marker>
          <marker
            id="dag-arrow-error"
            markerWidth="8"
            markerHeight="8"
            refX="7"
            refY="4"
            orient="auto"
            markerUnits="strokeWidth"
          >
            <path d="M0,0 L8,4 L0,8 z" fill="#dc2626" />
          </marker>
        </defs>

        {[...layout.positioned.values()].map(({ node, x, y }) => {
          const outputLabels = outputs
            .filter((item) => item.nodeId === node.nodeId)
            .map((item) => item.outputId);
          const resettable = resettableNodeIds?.has(node.nodeId) === true;
          const resetBusy = resetNodeBusyId === node.nodeId;
          const meta = nodeMeta(node);
          const outputY = meta.summary || meta.warning ? y + 66 : y + 56;
          return (
            <g
              key={node.nodeId}
              data-dag-clickable="true"
              style={{ cursor: onNodeClick ? "pointer" : undefined }}
              onClick={() => onNodeClick?.(node.nodeId)}
            >
            <rect
              x={x}
              y={y}
              rx={10}
              ry={10}
              width={layout.nodeW}
              height={layout.nodeH}
              fill={nodeColor(node.op)}
              stroke={outputLabels.length > 0 ? "#f8fafc" : "rgba(255,255,255,0.25)"}
              strokeWidth={outputLabels.length > 0 ? 2 : 1}
            />
            <text x={x + 10} y={y + 22} fontSize={13} fill="#ffffff" fontWeight="700">
              {node.nodeId}
            </text>
            <text x={x + 10} y={y + 40} fontSize={11} fill="rgba(255,255,255,0.95)">
              {node.op}
            </text>
              {meta.summary ? (
                <text x={x + 10} y={y + 54} fontSize={10} fill="rgba(226,232,240,0.95)">
                  {meta.summary}
                </text>
              ) : null}
              {meta.warning ? (
                <text x={x + 10} y={y + 64} fontSize={10} fill="#fecaca">
                  {meta.warning}
                </text>
              ) : null}
              {outputLabels.map((label, idx) => (
              <g key={`${node.nodeId}:out:${label}:${idx}`}>
                <rect
                  x={x + 10 + idx * 74}
                  y={outputY}
                  rx={6}
                  ry={6}
                  width={68}
                  height={20}
                  fill="rgba(248,250,252,0.22)"
                  stroke="rgba(248,250,252,0.75)"
                />
                <text
                  x={x + 44 + idx * 74}
                  y={outputY + 14}
                  fontSize={10}
                  fill="#f8fafc"
                  textAnchor="middle"
                >
                  {label}
                </text>
              </g>
            ))}
              {resettable ? (
                <g
                  data-dag-clickable="true"
                  style={{ cursor: resetBusy ? "default" : "pointer" }}
                  onClick={(event) => {
                    event.stopPropagation();
                    if (resetBusy) {
                      return;
                    }
                    onResetNode?.(node.nodeId);
                  }}
                >
                  <rect
                    x={x + layout.nodeW - 62}
                    y={y + 8}
                    rx={6}
                    ry={6}
                    width={54}
                    height={18}
                    fill={resetBusy ? "rgba(203,213,225,0.45)" : "rgba(254,242,242,0.95)"}
                    stroke={resetBusy ? "rgba(203,213,225,0.9)" : "rgba(220,38,38,0.95)"}
                  />
                  <text
                    x={x + layout.nodeW - 35}
                    y={y + 21}
                    fontSize={10}
                    fill={resetBusy ? "#334155" : "#b91c1c"}
                    textAnchor="middle"
                  >
                    {resetBusy ? "..." : "reset"}
                  </text>
                </g>
              ) : null}
            </g>
          );
        })}

        {layout.edges.map((edge, idx) => {
          const curve = Math.max(40, Math.abs(edge.toX - edge.fromX) * 0.45);
          const d = `M ${edge.fromX} ${edge.fromY} C ${edge.fromX + curve} ${edge.fromY}, ${
            edge.toX - curve
          } ${edge.toY}, ${edge.toX} ${edge.toY}`;
          const color = edge.valid ? "#64748b" : "#dc2626";
          const marker = edge.valid ? "url(#dag-arrow)" : "url(#dag-arrow-error)";
          const midX = (edge.fromX + edge.toX) / 2;
          const midY = (edge.fromY + edge.toY) / 2 - 8;
          return (
            <g key={`edge-${idx}`}>
              <path
                d={d}
                fill="none"
                stroke="rgba(15, 23, 42, 0.7)"
                strokeWidth={3.2}
                strokeDasharray={edge.valid ? undefined : "5 3"}
              />
              <path
                d={d}
                fill="none"
                stroke={color}
                strokeWidth={1.6}
                strokeDasharray={edge.valid ? undefined : "5 3"}
                markerEnd={marker}
              />
              <text
                x={midX}
                y={midY}
                fontSize={10}
                fill={color}
                textAnchor="middle"
              >
                {edge.label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
