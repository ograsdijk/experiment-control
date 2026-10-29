import { getRefProp, Tooltip, type TooltipProps } from "@mantine/core";
import { useMergedRef } from "@mantine/hooks";
import {
  cloneElement,
  forwardRef,
  useEffect,
  useImperativeHandle,
  useState,
  type MouseEvent,
  type PointerEvent,
  type ReactElement,
} from "react";

type HoverTooltipProps = Omit<TooltipProps, "children" | "opened" | "events"> & {
  children: ReactElement;
};

// Tooltip props Mantine applies to its reference element. HoverTooltip's
// reference is TargetAnchor, so these are forwarded to the real target.
type TargetHandlers = {
  onClick?: (event: MouseEvent) => void;
  onMouseEnter?: (event: MouseEvent) => void;
  onMouseLeave?: (event: MouseEvent) => void;
  onMouseMove?: (event: MouseEvent) => void;
  onPointerDown?: (event: PointerEvent) => void;
  onPointerEnter?: (event: PointerEvent) => void;
};
const TARGET_HANDLERS = [
  "onClick",
  "onMouseEnter",
  "onMouseLeave",
  "onMouseMove",
  "onPointerDown",
  "onPointerEnter",
] as const;

/**
 * Stands in for the target as the Tooltip's reference: Floating UI accepts a
 * virtual element, so the tooltip is positioned against the real target
 * without wrapping (and so remounting) it. The aria-describedby Mantine gives
 * its reference is copied onto the target.
 */
const TargetAnchor = forwardRef(function TargetAnchor(
  { target, "aria-describedby": describedBy }: { target: Element; "aria-describedby"?: string },
  ref
) {
  useImperativeHandle(
    ref,
    () => ({
      getBoundingClientRect: () => target.getBoundingClientRect(),
      contextElement: target,
    }),
    [target]
  );
  useEffect(() => {
    if (!describedBy) {
      return;
    }
    const previous = target.getAttribute("aria-describedby");
    target.setAttribute("aria-describedby", previous ? `${previous} ${describedBy}` : describedBy);
    return () => {
      if (previous === null) {
        target.removeAttribute("aria-describedby");
      } else {
        target.setAttribute("aria-describedby", previous);
      }
    };
  }, [target, describedBy]);
  return null;
});

/**
 * A Mantine Tooltip that is only mounted while its target is hovered.
 *
 * Mantine mounts every Tooltip's portal container even while closed, and
 * React gives each portal container its full set of delegated event
 * listeners; a Tooltip also runs Floating UI hooks on every render. Use this
 * for tooltips repeated per row (device signals, commands, devices). Like
 * Mantine's default, it responds to hover only.
 *
 * The Tooltip is rendered next to the target, not around it: wrapping the
 * target on hover would change its element type and remount its DOM node, so
 * a pointer that left before the new node saw a mouseover never fired
 * onMouseLeave and the tooltip stayed open. While open it also closes on a
 * mouseover outside the target, for browsers that send no mouseout from a
 * target that became disabled while hovered (Chrome < 116, Firefox < 110).
 */
export function HoverTooltip({ children, ...props }: HoverTooltipProps) {
  const [hovered, setHovered] = useState(false);
  const [targetNode, setTargetNode] = useState<Element | null>(null);
  const tooltipProps: Omit<HoverTooltipProps, "children"> = { ...props };
  const tooltipHandlers: TargetHandlers = {};
  for (const name of TARGET_HANDLERS) {
    tooltipHandlers[name] = props[name] as never;
    delete tooltipProps[name];
  }
  const childHandlers = children.props as TargetHandlers;
  const ref = useMergedRef(setTargetNode, getRefProp(children));

  useEffect(() => {
    if (!hovered || !targetNode) {
      return;
    }
    const onOver = (event: globalThis.MouseEvent) => {
      if (!(event.target instanceof Node && targetNode.contains(event.target))) {
        setHovered(false);
      }
    };
    document.addEventListener("mouseover", onOver, true);
    return () => document.removeEventListener("mouseover", onOver, true);
  }, [hovered, targetNode]);

  const handlers: Partial<Record<keyof TargetHandlers, (event: never) => void>> = {};
  for (const name of TARGET_HANDLERS) {
    const own = tooltipHandlers[name] as ((event: never) => void) | undefined;
    const child = childHandlers[name] as ((event: never) => void) | undefined;
    if (own || child) {
      handlers[name] = (event: never) => {
        own?.(event);
        child?.(event);
      };
    }
  }
  const target = cloneElement(children, {
    ...handlers,
    ref,
    onMouseEnter: (event: MouseEvent) => {
      handlers.onMouseEnter?.(event as never);
      setHovered(true);
    },
    onMouseLeave: (event: MouseEvent) => {
      handlers.onMouseLeave?.(event as never);
      setHovered(false);
    },
  });
  return (
    <>
      {target}
      {hovered && targetNode ? (
        <Tooltip {...tooltipProps} opened events={{ hover: false, focus: false, touch: false }}>
          <TargetAnchor target={targetNode} />
        </Tooltip>
      ) : null}
    </>
  );
}
