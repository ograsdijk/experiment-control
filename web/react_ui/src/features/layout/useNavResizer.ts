import { useEffect, type PointerEvent as ReactPointerEvent } from "react";

import {
  LAYOUT_NAV_MAX_WIDTH,
  LAYOUT_NAV_MIN_WIDTH,
  useLayout,
} from "./LayoutContext";

/**
 * Nav-sidebar resize controller.
 *
 * Owns the pointer-drag resize for the device-panel sidebar plus the
 * helpers that flip the panel collapsed flag while cancelling any
 * in-flight RAF callback. Split from the App-level handler block so
 * the resize state machine + its window listeners + its handlers
 * live in one place.
 *
 * Returns:
 *
 * - `handleNavResizeStart(event)` — pointer-down handler bound to
 *   the drag handle in the JSX.
 * - `setDevicePanelCollapsed(boolean)` — flips collapsed state and
 *   cancels any in-flight resize RAF first.
 * - `collapseDevicePanel()` / `expandDevicePanel()` — thin wrappers.
 *
 * Internally consumes nav width + collapsed state + resize refs +
 * isResizing flag from LayoutContext.
 */

export function useNavResizer() {
  const {
    navWidth,
    setNavWidth,
    isResizing,
    setIsResizing,
    resizeRef,
    resizePendingWidthRef,
    resizeRafRef,
    isDevicePanelCollapsed,
    setIsDevicePanelCollapsed,
  } = useLayout();

  // Window-level pointer listeners that drive the live width update
  // while a drag is in flight. Skipped when not resizing or when the
  // panel is currently collapsed (no handle visible).
  useEffect(() => {
    if (!isResizing || isDevicePanelCollapsed) {
      return;
    }
    const handleMove = (event: PointerEvent) => {
      if (!resizeRef.current) {
        return;
      }
      const delta = event.clientX - resizeRef.current.startX;
      const proposed = resizeRef.current.startWidth + delta;
      const max = Math.min(LAYOUT_NAV_MAX_WIDTH, window.innerWidth - 320);
      const safeMax = Math.max(LAYOUT_NAV_MIN_WIDTH, max);
      const nextWidth = Math.max(
        LAYOUT_NAV_MIN_WIDTH,
        Math.min(safeMax, proposed)
      );
      resizePendingWidthRef.current = nextWidth;
      if (resizeRafRef.current !== null) {
        return;
      }
      resizeRafRef.current = window.requestAnimationFrame(() => {
        resizeRafRef.current = null;
        const pending = resizePendingWidthRef.current;
        if (pending == null) {
          return;
        }
        setNavWidth(pending);
      });
    };
    const handleUp = () => {
      if (resizeRafRef.current !== null) {
        window.cancelAnimationFrame(resizeRafRef.current);
        resizeRafRef.current = null;
      }
      const pending = resizePendingWidthRef.current;
      resizePendingWidthRef.current = null;
      if (pending != null) {
        setNavWidth(pending);
      }
      resizeRef.current = null;
      setIsResizing(false);
    };
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    return () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
      if (resizeRafRef.current !== null) {
        window.cancelAnimationFrame(resizeRafRef.current);
        resizeRafRef.current = null;
      }
      resizePendingWidthRef.current = null;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [isResizing, isDevicePanelCollapsed]);

  const handleNavResizeStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (isDevicePanelCollapsed) {
      return;
    }
    event.preventDefault();
    resizeRef.current = { startX: event.clientX, startWidth: navWidth };
    setIsResizing(true);
  };

  const setDevicePanelCollapsed = (collapsed: boolean) => {
    if (resizeRafRef.current !== null) {
      window.cancelAnimationFrame(resizeRafRef.current);
      resizeRafRef.current = null;
    }
    resizePendingWidthRef.current = null;
    resizeRef.current = null;
    setIsResizing(false);
    setIsDevicePanelCollapsed(collapsed);
  };

  const collapseDevicePanel = () => {
    setDevicePanelCollapsed(true);
  };

  const expandDevicePanel = () => {
    setDevicePanelCollapsed(false);
  };

  return {
    handleNavResizeStart,
    setDevicePanelCollapsed,
    collapseDevicePanel,
    expandDevicePanel,
  };
}
