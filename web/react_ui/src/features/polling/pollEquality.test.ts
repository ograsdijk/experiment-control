import { describe, expect, it } from "vitest";

import { sameStreamCatalog } from "./pollEquality";
import type { StreamCatalogEntry } from "../../types";

const ENTRY: StreamCatalogEntry = {
  device_id: "pxie5171",
  stream: "waveforms",
  dtype: "int16",
  shape: [5, 4096],
  units: "ADC",
  description: "raw digitizer waveform",
  x_units: "s",
  x_label: "time since YAG trigger",
  x_increment: 1e-5,
  x_origin: 0.8e-3,
  x_axis_source: "run_metadata",
};

describe("sameStreamCatalog", () => {
  it("treats an identical catalog as unchanged", () => {
    expect(sameStreamCatalog([ENTRY], [{ ...ENTRY }])).toBe(true);
  });

  it("detects a changed sample period", () => {
    // Without this the panels keep the stale axis after a digitizer is
    // reconfigured, because polling short-circuits on equality.
    expect(
      sameStreamCatalog([ENTRY], [{ ...ENTRY, x_increment: 2e-5 }])
    ).toBe(false);
  });

  it("detects changes to the other axis fields", () => {
    const changes: Array<Partial<StreamCatalogEntry>> = [
      { x_units: "ms" },
      { x_label: "time" },
      { x_origin: 0 },
      { x_axis_source: "unresolved" },
    ];
    for (const change of changes) {
      expect(sameStreamCatalog([ENTRY], [{ ...ENTRY, ...change }])).toBe(false);
    }
  });

  it("still detects the pre-existing fields", () => {
    expect(sameStreamCatalog([ENTRY], [{ ...ENTRY, units: "V" }])).toBe(false);
    expect(sameStreamCatalog([ENTRY], [{ ...ENTRY, shape: [5, 2048] }])).toBe(false);
  });
});
