import { getRefProp, Tooltip, type TooltipProps } from "@mantine/core";
import { useMergedRef } from "@mantine/hooks";
import {
  cloneElement,
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type MouseEvent,
  type ReactElement,
} from "react";

type HoverTooltipProps = Omit<TooltipProps, "children" | "opened" | "events"> & {
  children: ReactElement;
};

type Handler = (event: never) => void;

// Tooltip props Mantine applies to its reference element. HoverTooltip's
// reference is TargetAnchor, so these are forwarded to the real target.
const TARGET_HANDLERS = [
  "onClick",
  "onMouseEnter",
  "onMouseLeave",
  "onMouseMove",
  "onPointerDown",
  "onPointerEnter",
] as const;
type TargetHandlers = Partial<Record<(typeof TARGET_HANDLERS)[number], Handler>>;

function withoutToken(value: string | null, token: string): string {
  return (value ?? "")
    .split(/\s+/)
    .filter((part) => part && part !== token)
    .join(" ");
}

/**
 * Stands in for the target as the Tooltip's reference: Floating UI accepts a
 * virtual element, so the tooltip is positioned against the real target
 * without wrapping (and so remounting) it. The aria-describedby Mantine gives
 * its reference is added to the target's own, and only that id is removed.
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
    const own = withoutToken(target.getAttribute("aria-describedby"), describedBy);
    target.setAttribute("aria-describedby", own ? `${own} ${describedBy}` : describedBy);
    return () => {
      const rest = withoutToken(target.getAttribute("aria-describedby"), describedBy);
      if (rest) {
        target.setAttribute("aria-describedby", rest);
      } else {
        target.removeAttribute("aria-describedby");
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
 * Mantine's default, it responds to hover only, and until then it renders
 * only the target.
 *
 * The Tooltip is rendered next to the target, not around it: wrapping the
 * target on hover would change its element type and remount its DOM node, so
 * a pointer that left before the new node saw a mouseover never fired
 * onMouseLeave and the tooltip stayed open. While open it also closes on a
 * mouseover outside the target, for browsers that send no mouseout from a
 * target that became disabled while hovered (Chrome < 116, Firefox < 110).
 */
export function HoverTooltip({ children, ...props }: HoverTooltipProps) {
  // The hovered target node; null while closed.
  const [hoverNode, setHoverNode] = useState<Element | null>(null);
  const openRef = useRef(false);

  const tooltipProps: Omit<HoverTooltipProps, "children"> = { ...props };
  const tooltipHandlers: TargetHandlers = {};
  for (const name of TARGET_HANDLERS) {
    tooltipHandlers[name] = props[name] as Handler | undefined;
    delete tooltipProps[name];
  }
  const childProps = children.props as TargetHandlers & { className?: string };
  const handlers: TargetHandlers = {};
  for (const name of TARGET_HANDLERS) {
    const own = tooltipHandlers[name];
    const child = childProps[name];
    if (own || child) {
      handlers[name] = (event: never) => {
        own?.(event);
        child?.(event);
      };
    }
  }
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const open = (node: Element) => {
    openRef.current = true;
    setHoverNode(node);
  };
  const close = () => {
    openRef.current = false;
    setHoverNode(null);
  };

  // Re-anchor if the child's element is replaced while open; no state
  // update (and so no render) while closed.
  const trackNode = useCallback((node: Element | null) => {
    if (node && openRef.current) {
      setHoverNode(node);
    }
  }, []);
  const ref = useMergedRef(trackNode, getRefProp(children));

  useEffect(() => {
    if (!hoverNode) {
      return;
    }
    const onOver = (event: globalThis.MouseEvent) => {
      if (
        openRef.current &&
        !(event.target instanceof Node && hoverNode.contains(event.target))
      ) {
        handlersRef.current.onMouseLeave?.(event as never);
        openRef.current = false;
        setHoverNode(null);
      }
    };
    document.addEventListener("mouseover", onOver, true);
    return () => document.removeEventListener("mouseover", onOver, true);
  }, [hoverNode]);

  const className =
    [tooltipProps.className, childProps.className].filter(Boolean).join(" ") || undefined;
  const target = cloneElement(children, {
    ...handlers,
    className,
    ref,
    onMouseEnter: (event: MouseEvent) => {
      handlers.onMouseEnter?.(event as never);
      open(event.currentTarget);
    },
    onMouseLeave: (event: MouseEvent) => {
      // Already closed (and the leave handlers called) by the fallback.
      if (openRef.current) {
        handlers.onMouseLeave?.(event as never);
        close();
      }
    },
  });
  return (
    <>
      {target}
      {hoverNode ? (
        <Tooltip {...tooltipProps} opened events={{ hover: false, focus: false, touch: false }}>
          <TargetAnchor target={hoverNode} />
        </Tooltip>
      ) : null}
    </>
  );
}
