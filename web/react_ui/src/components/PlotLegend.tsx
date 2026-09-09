import { memo } from "react";

export type PlotLegendItem = {
  /** Stable identity for the series — also the seriesLabels key. */
  key: string;
  label: string;
  color: string;
  /** Longer form for the tooltip: the machine id, units, or a description. */
  title?: string;
  /** False for derived curves (fits) that have no series of their own to rename. */
  renamable?: boolean;
};

type PlotLegendProps = {
  items: PlotLegendItem[];
  /** When set, each renamable entry becomes a button that starts a rename. */
  onRenameSeries?: (key: string) => void;
  /** Key currently being renamed; that entry renders an input instead. */
  editingKey?: string | null;
  editingValue?: string;
  onEditingValueChange?: (value: string) => void;
  onCommitRename?: () => void;
  onCancelRename?: () => void;
};

/**
 * Compact series legend rendered under a plot.
 *
 * uPlot's built-in legend stays disabled — it lives inside the plot
 * element and reflows the canvas — so series identity is rendered here
 * instead. Before this existed, the only hint of which colour was which
 * series was the badge row, which did not actually map colours at all.
 *
 * Entries are also the rename surface: the device and workspace schemas
 * carry no display name for a trace, so the name a person wants lives in
 * the panel's own `seriesLabels`, edited here.
 */
function PlotLegendImpl({
  items,
  onRenameSeries,
  editingKey,
  editingValue = "",
  onEditingValueChange,
  onCommitRename,
  onCancelRename,
}: PlotLegendProps) {
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="plot-legend">
      {items.map((item) => {
        const swatch = (
          <span
            className="plot-legend-swatch"
            style={{ background: item.color }}
          />
        );
        if (editingKey === item.key) {
          return (
            <span
              key={item.key}
              className="plot-legend-item"
              data-no-activate="true"
            >
              {swatch}
              <input
                className="plot-legend-input"
                autoFocus
                aria-label={`Rename ${item.label}`}
                value={editingValue}
                placeholder={item.title ?? item.key}
                onChange={(event) =>
                  onEditingValueChange?.(event.currentTarget.value)
                }
                onBlur={() => onCommitRename?.()}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    onCommitRename?.();
                  } else if (event.key === "Escape") {
                    event.preventDefault();
                    onCancelRename?.();
                  }
                }}
              />
            </span>
          );
        }
        const renamable = item.renamable !== false && Boolean(onRenameSeries);
        return renamable ? (
          <button
            key={item.key}
            type="button"
            className="plot-legend-item"
            data-no-activate="true"
            title={item.title ?? `${item.label} — double-click to rename`}
            onDoubleClick={() => onRenameSeries?.(item.key)}
          >
            {swatch}
            <span className="plot-legend-label">{item.label}</span>
          </button>
        ) : (
          <span
            key={item.key}
            className="plot-legend-item"
            style={{ cursor: "default" }}
            title={item.title ?? item.label}
          >
            {swatch}
            <span className="plot-legend-label">{item.label}</span>
          </span>
        );
      })}
    </div>
  );
}

export const PlotLegend = memo(PlotLegendImpl);
