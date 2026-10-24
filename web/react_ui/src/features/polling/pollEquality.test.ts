import { describe, expect, it } from "vitest";

import {
  ageBucket,
  rssBucket,
  sameDeviceStatuses,
  sameDeviceStatusesCoarse,
  sameProcessStatuses,
  sameProcessStatusesCoarse,
  sameStreamCatalog,
} from "./pollEquality";
import { formatCoarseAge } from "../devices/health";
import type { DeviceStatus, ProcessStatus, StreamCatalogEntry } from "../../types";

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

const DEVICE: DeviceStatus = {
  device_id: "dev",
  liveness: "ONLINE",
  hb_age_s: 0.4,
  telemetry_age_s: 0.2,
  device_state: "CONNECTED",
};

const PROCESS: ProcessStatus = {
  process_id: "proc",
  state: "RUNNING",
  rss_bytes: 100 * 1024 * 1024,
  hb_age_s: 0.3,
};

describe("coarse status equality", () => {
  it("buckets ages so a ticking fresh heartbeat is not a change", () => {
    expect(ageBucket(0.1)).toBe(ageBucket(1.9));
    expect(ageBucket(1.9)).not.toBe(ageBucket(2.1));
    expect(ageBucket(31)).toBe(ageBucket(59));
    expect(ageBucket(61)).not.toBe(ageBucket(121));
    expect(ageBucket(null)).toBe(-1);
    expect(ageBucket(Number.NaN)).toBe(-1);
  });

  it("ignores in-bucket age/RSS jitter but not real changes", () => {
    const jitter = { ...DEVICE, hb_age_s: 1.2, telemetry_age_s: 0.9 };
    expect(sameDeviceStatusesCoarse([DEVICE], [jitter])).toBe(true);
    expect(sameDeviceStatuses([DEVICE], [jitter])).toBe(false);
    expect(
      sameDeviceStatusesCoarse([DEVICE], [{ ...DEVICE, hb_age_s: 6 }])
    ).toBe(false);
    expect(
      sameDeviceStatusesCoarse([DEVICE], [{ ...DEVICE, liveness: "STALE" }])
    ).toBe(false);
    expect(
      sameDeviceStatusesCoarse([DEVICE], [{ ...DEVICE, hb_age_s: null }])
    ).toBe(false);

    const procJitter = { ...PROCESS, hb_age_s: 1.1, rss_bytes: PROCESS.rss_bytes! + 512 * 1024 };
    expect(sameProcessStatusesCoarse([PROCESS], [procJitter])).toBe(true);
    expect(sameProcessStatuses([PROCESS], [procJitter])).toBe(false);
    expect(
      sameProcessStatusesCoarse([PROCESS], [{ ...PROCESS, rss_bytes: 200 * 1024 * 1024 }])
    ).toBe(false);
    expect(
      sameProcessStatusesCoarse([PROCESS], [{ ...PROCESS, state: "STOPPED" }])
    ).toBe(false);
    expect(rssBucket(undefined)).toBe(-1);
  });

  it("labels ages with the same bucket bounds", () => {
    expect(formatCoarseAge(0.4)).toBe("<2 s");
    expect(formatCoarseAge(3)).toBe("2–5 s");
    expect(formatCoarseAge(45)).toBe("30–60 s");
    expect(formatCoarseAge(130)).toBe("2+ min");
    expect(formatCoarseAge(null)).toBe("n/a");
  });
});
