import { describe, expect, it } from "vitest";

import {
  MAX_AUTO_PLOT_HEIGHT_PX,
  MIN_AUTO_PLOT_HEIGHT_PX,
  autoPlotHeight,
} from "./usePlotAreaHeight";

describe("autoPlotHeight", () => {
  it("scales with card width between the readable bounds", () => {
    const narrow = autoPlotHeight(700);
    const wide = autoPlotHeight(820);
    expect(wide).toBeGreaterThan(narrow);
    expect(narrow).toBeGreaterThanOrEqual(MIN_AUTO_PLOT_HEIGHT_PX);
    expect(wide).toBeLessThanOrEqual(MAX_AUTO_PLOT_HEIGHT_PX);
  });

  it("keeps a narrow single-column card readable", () => {
    expect(autoPlotHeight(360)).toBe(MIN_AUTO_PLOT_HEIGHT_PX);
  });

  it("stops a very wide card turning into a banner", () => {
    expect(autoPlotHeight(4000)).toBe(MAX_AUTO_PLOT_HEIGHT_PX);
  });

  it("survives a first render with no measurement yet", () => {
    expect(autoPlotHeight(0)).toBe(MIN_AUTO_PLOT_HEIGHT_PX);
    expect(autoPlotHeight(Number.NaN)).toBe(MIN_AUTO_PLOT_HEIGHT_PX);
  });
});
