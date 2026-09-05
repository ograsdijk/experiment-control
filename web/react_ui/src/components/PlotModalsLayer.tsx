import { Modal } from "@mantine/core";
import type { ReactNode } from "react";

type Props = {
  expandedPlotOpened: boolean;
  onCloseExpandedPlot: () => void;
  expandedPlotTitle: string;
  expandedPlotContent: ReactNode;
};

/**
 * The expanded-plot modal.
 *
 * This layer used to carry the four per-kind `Stream*OptionsModal`s as
 * well, and ~55 props of pass-through to feed them. Those settings now
 * live in the panel's own settings popover, next to the plot they
 * configure, so only the one modal that genuinely needs the screen is
 * left here.
 */
export function PlotModalsLayer({
  expandedPlotOpened,
  onCloseExpandedPlot,
  expandedPlotTitle,
  expandedPlotContent,
}: Props) {
  return (
    <Modal
      opened={expandedPlotOpened}
      onClose={onCloseExpandedPlot}
      title={expandedPlotTitle}
      size="clamp(48rem, 92vw, 110rem)"
      centered
    >
      {expandedPlotContent}
    </Modal>
  );
}
