import { memo } from "react";

export type PanelStatusItem = {
  text: string;
  /** Renders in the warning colour — reserved for degraded state. */
  warn?: boolean;
};

type PanelStatusLineProps = {
  items?: PanelStatusItem[];
};

/**
 * One dimmed line of *live* state under a plot — what the badge row used
 * to carry once its configuration half moved into Settings.
 *
 * Every item here is conditional, so a healthy panel renders no row at
 * all and the plot gets the space. The link indicator lives in the header
 * instead, where the row exists anyway; a link that goes *down* still
 * appears here as a warning, because a red dot alone is easy to miss.
 * Settings shows the full set unconditionally, so nothing is lost.
 */
function PanelStatusLineImpl({ items = [] }: PanelStatusLineProps) {
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="panel-card-status">
      {items.map((item) => (
        <span
          key={item.text}
          className={item.warn ? "panel-card-status-warn" : undefined}
        >
          {item.text}
        </span>
      ))}
    </div>
  );
}

export const PanelStatusLine = memo(PanelStatusLineImpl);
