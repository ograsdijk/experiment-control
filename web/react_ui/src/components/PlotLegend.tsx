import { memo } from "react";

export type PlotLegendItem = {
  /** Stable identity for the series — also the seriesLabels key. */
  key: string;
  label: string;
  color: string;
  /** Longer form for the tooltip: the machine id, units, or a description. */
  title?: string;
};

type PlotLegendProps = {
  items: PlotLegendItem[];
  /** When set, each entry becomes a button that renames the series. */
  onRenameSeries?: (key: string) => void;
};

/**
 * Compact series legend rendered under a plot.
 *
 * uPlot's built-in legend stays disabled — it lives inside the plot
 * element and reflows the canvas — so series identity is rendered here
 * instead. Before this existed, the only hint of which colour was which
 * series was the badge row, which did not actually map colours at all.
 */
function PlotLegendImpl({ items, onRenameSeries }: PlotLegendProps) {
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="plot-legend">
      {items.map((item) =>
        onRenameSeries ? (
          <button
            key={item.key}
            type="button"
            className="plot-legend-item"
            data-no-activate="true"
            title={item.title ?? `${item.label} — click to rename`}
            onClick={() => onRenameSeries(item.key)}
          >
            <span
              className="plot-legend-swatch"
              style={{ background: item.color }}
            />
            <span className="plot-legend-label">{item.label}</span>
          </button>
        ) : (
          <span
            key={item.key}
            className="plot-legend-item"
            style={{ cursor: "default" }}
            title={item.title ?? item.label}
          >
            <span
              className="plot-legend-swatch"
              style={{ background: item.color }}
            />
            <span className="plot-legend-label">{item.label}</span>
          </span>
        )
      )}
    </div>
  );
}

export const PlotLegend = memo(PlotLegendImpl);
