import { Tooltip, type TooltipProps } from "@mantine/core";
import {
  cloneElement,
  useState,
  type MouseEvent,
  type MouseEventHandler,
  type ReactElement,
} from "react";

type HoverTooltipProps = Omit<TooltipProps, "children" | "opened"> & {
  children: ReactElement;
};

/**
 * A Mantine Tooltip that is only mounted while its target is hovered.
 *
 * Mantine mounts every Tooltip's portal container even while closed, and
 * React gives each portal container its full set of delegated event
 * listeners; a Tooltip also runs Floating UI hooks on every render. Use this
 * for tooltips repeated per row (device signals, commands, devices). Like
 * Mantine's default, it responds to hover only.
 */
export function HoverTooltip({ children, ...tooltipProps }: HoverTooltipProps) {
  const [hovered, setHovered] = useState(false);
  const childProps = children.props as {
    onMouseEnter?: MouseEventHandler;
    onMouseLeave?: MouseEventHandler;
  };
  const target = cloneElement(children, {
    onMouseEnter: (event: MouseEvent) => {
      childProps.onMouseEnter?.(event);
      setHovered(true);
    },
    onMouseLeave: (event: MouseEvent) => {
      childProps.onMouseLeave?.(event);
      setHovered(false);
    },
  });
  if (!hovered) {
    return target;
  }
  return (
    <Tooltip {...tooltipProps} opened>
      {target}
    </Tooltip>
  );
}
