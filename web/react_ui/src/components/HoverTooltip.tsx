import { Tooltip, type TooltipProps } from "@mantine/core";
import {
  cloneElement,
  useEffect,
  useId,
  useState,
  type MouseEvent,
  type MouseEventHandler,
  type ReactElement,
} from "react";

type HoverTooltipProps = Omit<TooltipProps, "children" | "opened"> & {
  children: ReactElement;
};

const TARGET_ATTR = "data-hover-tooltip-target";

function isInsideTarget(node: EventTarget | null, id: string): boolean {
  for (let el = node instanceof Element ? node : null; el; el = el.parentElement) {
    if (el.getAttribute(TARGET_ATTR) === id) {
      return true;
    }
  }
  return false;
}

/**
 * A Mantine Tooltip that is only mounted while its target is hovered.
 *
 * Mantine mounts every Tooltip's portal container even while closed, and
 * React gives each portal container its full set of delegated event
 * listeners; a Tooltip also runs Floating UI hooks on every render. Use this
 * for tooltips repeated per row (device signals, commands, devices). Like
 * Mantine's default, it responds to hover only.
 *
 * Wrapping the target in a Tooltip on hover remounts its DOM node. If the
 * pointer leaves before the new node sees a mouseover, the browser sends the
 * mouseout to the detached node and onMouseLeave never fires, so while open
 * we also close on any document mouseover outside the target.
 */
export function HoverTooltip({ children, ...tooltipProps }: HoverTooltipProps) {
  const [hovered, setHovered] = useState(false);
  const id = useId();
  const childProps = children.props as {
    onMouseEnter?: MouseEventHandler;
    onMouseLeave?: MouseEventHandler;
  };

  useEffect(() => {
    if (!hovered) {
      return;
    }
    const onOver = (event: globalThis.MouseEvent) => {
      if (!isInsideTarget(event.target, id)) {
        setHovered(false);
      }
    };
    const onOut = (event: globalThis.MouseEvent) => {
      if (event.relatedTarget === null) {
        setHovered(false);
      }
    };
    document.addEventListener("mouseover", onOver, true);
    document.addEventListener("mouseout", onOut, true);
    return () => {
      document.removeEventListener("mouseover", onOver, true);
      document.removeEventListener("mouseout", onOut, true);
    };
  }, [hovered, id]);

  const target = cloneElement(children, {
    [TARGET_ATTR]: id,
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
