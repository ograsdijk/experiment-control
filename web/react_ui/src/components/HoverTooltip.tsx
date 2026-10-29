import { Tooltip, type TooltipProps } from "@mantine/core";
import { useMergedRef } from "@mantine/hooks";
import {
  cloneElement,
  forwardRef,
  useImperativeHandle,
  useRef,
  useState,
  type MouseEvent,
  type MouseEventHandler,
  type ReactElement,
  type Ref,
  type RefObject,
} from "react";

type HoverTooltipProps = Omit<TooltipProps, "children" | "opened" | "events"> & {
  children: ReactElement;
};

/**
 * Stands in for the target as the Tooltip's reference: Floating UI accepts a
 * virtual element, so the tooltip is positioned against the real target
 * without wrapping (and so remounting) it.
 */
const TargetAnchor = forwardRef(function TargetAnchor(
  { target }: { target: RefObject<Element> },
  ref
) {
  useImperativeHandle(
    ref,
    () => ({
      getBoundingClientRect: () =>
        target.current?.getBoundingClientRect() ?? new DOMRect(),
      contextElement: target.current ?? undefined,
    }),
    [target]
  );
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
 * onMouseLeave and the tooltip stayed open.
 */
export function HoverTooltip({ children, ...tooltipProps }: HoverTooltipProps) {
  const [hovered, setHovered] = useState(false);
  const targetRef = useRef<Element>(null);
  const childProps = children.props as {
    onMouseEnter?: MouseEventHandler;
    onMouseLeave?: MouseEventHandler;
  };
  const mergedRef = useMergedRef(targetRef, (children as { ref?: Ref<Element> }).ref);
  const target = cloneElement(children, {
    ref: mergedRef,
    onMouseEnter: (event: MouseEvent) => {
      childProps.onMouseEnter?.(event);
      setHovered(true);
    },
    onMouseLeave: (event: MouseEvent) => {
      childProps.onMouseLeave?.(event);
      setHovered(false);
    },
  });
  return (
    <>
      {target}
      {hovered ? (
        <Tooltip {...tooltipProps} opened events={{ hover: false, focus: false, touch: false }}>
          <TargetAnchor target={targetRef} />
        </Tooltip>
      ) : null}
    </>
  );
}
