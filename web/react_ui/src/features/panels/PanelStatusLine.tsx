import { memo } from "react";

export type PanelStatusItem = {
  text: string;
  /** Renders in the warning colour — reserved for degraded state. */
  warn?: boolean;
};

type PanelStatusLineProps = {
  /** Null hides the link indicator entirely (panels with no live link). */
  connected: boolean | null;
  linkLabel: string;
  items?: PanelStatusItem[];
};

/**
 * One dimmed line of *live* state under a plot — what the badge row used
 * to carry once its configuration half moved into Settings.
 *
 * Deliberately not everything: the connection dot is always present, and
 * counters appear only when they mean something. Settings shows the full
 * set unconditionally, so nothing is lost — but a climbing dropped-sample
 * count must be visible without opening a modal.
 */
function PanelStatusLineImpl({
  connected,
  linkLabel,
  items = [],
}: PanelStatusLineProps) {
  if (connected === null && items.length === 0) {
    return null;
  }
  return (
    <div className="panel-card-status">
      {connected === null ? null : (
        <span
          className="panel-card-status-item"
          title={`${linkLabel} link ${connected ? "connected" : "disconnected"}`}
        >
          <span
            className="panel-card-status-dot"
            style={{
              background: connected
                ? "var(--mantine-color-teal-6)"
                : "var(--mantine-color-red-6)",
            }}
          />
          {connected ? null : <span> {linkLabel} link down</span>}
        </span>
      )}
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
