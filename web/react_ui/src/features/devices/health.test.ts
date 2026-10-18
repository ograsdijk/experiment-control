import { describe, expect, it } from "vitest";

import type { DeviceStatus, ProcessStatus } from "../../types";
import {
  deviceHasIssue,
  deviceHealth,
  deviceMatchesQuery,
  isResourceViewFiltered,
  selectVisibleDevices,
  summarizeDeviceHealth,
  processAdapter,
  processHealth,
  resourceHasIssue,
  selectVisibleResources,
  summarizeResources,
} from "./health";

function device(
  overrides: Partial<DeviceStatus> & { driver_process?: { state?: string; last_error?: string } }
): DeviceStatus {
  return {
    device_id: "dev",
    liveness: "ONLINE",
    hb_age_s: 0.5,
    telemetry_age_s: 0.5,
    driver_state: "RUNNING",
    device_state: "OK",
    last_error: null,
    driver_process: { state: "RUNNING" },
    ...overrides,
  } as DeviceStatus;
}

describe("deviceHealth (mirrors TUI _device_health)", () => {
  it.each([
    [{}, "healthy"],
    [{ driver_process: { state: "CRASHLOOP" } }, "failed"],
    [{ driver_process: { state: "FAILED" } }, "failed"],
    [{ device_state: "FAULT" }, "failed"],
    [{ driver_state: "FAULT" }, "failed"],
    [{ liveness: "STALE" }, "stale"],
    [{ device_state: "DEGRADED" }, "degraded"],
    [{ driver_process: { state: "STARTING" }, liveness: "OFFLINE", device_state: null }, "transition"],
    [{ device_state: "DISCONNECTED", liveness: "DISCONNECTED" }, "neutral"],
    [{ driver_process: { state: "STOPPED" }, liveness: "OFFLINE", device_state: null }, "neutral"],
    [{ liveness: "DISCONNECTED", device_state: "UNKNOWN" }, "unknown"],
  ] as const)("%j -> %s", (overrides, expected) => {
    expect(deviceHealth(device(overrides))).toBe(expected);
  });

  it("prefers failed over stale", () => {
    expect(deviceHealth(device({ liveness: "STALE", device_state: "FAULT" }))).toBe("failed");
  });

  it("normalizes enum-like values", () => {
    expect(deviceHealth(device({ liveness: "Liveness.STALE" }))).toBe("stale");
  });
});

describe("deviceHasIssue", () => {
  it("flags failed/stale/degraded only", () => {
    expect(deviceHasIssue(device({}))).toBe(false);
    expect(deviceHasIssue(device({ device_state: "DEGRADED" }))).toBe(true);
    expect(deviceHasIssue(device({ device_state: "DISCONNECTED", liveness: "DISCONNECTED" }))).toBe(false);
  });

  it("flags an offline remote device even when neutral", () => {
    const remote = device({
      is_remote: true,
      liveness: "OFFLINE",
      device_state: null,
      driver_process: { state: "STOPPED" },
    });
    expect(deviceHealth(remote)).toBe("neutral");
    expect(deviceHasIssue(remote)).toBe(true);
  });
});

describe("deviceMatchesQuery", () => {
  const d = device({
    device_id: "synth_hd",
    device_state: "FAULT",
    last_error: "Serial timeout on COM7",
  });

  it("matches id, state and error text, all terms required", () => {
    expect(deviceMatchesQuery(d, "")).toBe(true);
    expect(deviceMatchesQuery(d, "SYNTH")).toBe(true);
    expect(deviceMatchesQuery(d, "fault")).toBe(true);
    expect(deviceMatchesQuery(d, "com7 synth")).toBe(true);
    expect(deviceMatchesQuery(d, "com7 laser")).toBe(false);
  });

  it("falls back to the driver process error", () => {
    const proc = device({ driver_process: { state: "CRASHLOOP", last_error: "ImportError foo" } });
    expect(deviceMatchesQuery(proc, "importerror")).toBe(true);
  });
});

describe("selectVisibleDevices", () => {
  const ok = device({ device_id: "b_ok" });
  const failed = device({ device_id: "c_failed", device_state: "FAULT" });
  const stale = device({ device_id: "a_stale", liveness: "STALE" });
  const ordered = [ok, failed, stale];

  it("keeps custom order and filters issues", () => {
    expect(
      selectVisibleDevices(ordered, { query: "", issuesOnly: false, sort: "custom" })
    ).toEqual(ordered);
    expect(
      selectVisibleDevices(ordered, { query: "", issuesOnly: true, sort: "custom" }).map(
        (d) => d.device_id
      )
    ).toEqual(["c_failed", "a_stale"]);
  });

  it("sorts by name and by health", () => {
    expect(
      selectVisibleDevices(ordered, { query: "", issuesOnly: false, sort: "name" }).map(
        (d) => d.device_id
      )
    ).toEqual(["a_stale", "b_ok", "c_failed"]);
    expect(
      selectVisibleDevices(ordered, { query: "", issuesOnly: false, sort: "health" }).map(
        (d) => d.device_id
      )
    ).toEqual(["c_failed", "a_stale", "b_ok"]);
  });

  it("reports whether the view differs from custom order", () => {
    expect(isResourceViewFiltered({ query: " ", issuesOnly: false, sort: "custom" })).toBe(false);
    expect(isResourceViewFiltered({ query: "x", issuesOnly: false, sort: "custom" })).toBe(true);
    expect(isResourceViewFiltered({ query: "", issuesOnly: false, sort: "name" })).toBe(true);
  });
});

describe("summarizeDeviceHealth", () => {
  it("counts healthy, issues, failed and remote", () => {
    expect(
      summarizeDeviceHealth([
        device({}),
        device({ device_state: "FAULT" }),
        device({ liveness: "STALE", is_remote: true }),
      ])
    ).toEqual({ total: 3, healthy: 1, issues: 2, failed: 1, remote: 1 });
  });
});

function proc(overrides: Partial<ProcessStatus>): ProcessStatus {
  return { process_id: "p", state: "RUNNING", ...overrides };
}

describe("processHealth (mirrors TUI _process_health)", () => {
  it.each([
    ["RUNNING", "healthy"],
    ["STARTING", "transition"],
    ["STOPPING", "transition"],
    ["FAILED", "failed"],
    ["CRASHLOOP", "failed"],
    ["STOPPED", "neutral"],
    ["EXITED", "neutral"],
  ] as const)("%s -> %s", (state, expected) => {
    expect(processHealth(proc({ state }))).toBe(expected);
  });
});

describe("process list view", () => {
  const local = proc({ process_id: "b_local" });
  const crash = proc({ process_id: "c_crash", state: "CRASHLOOP", last_error: "exit 3" });
  const remoteDown = proc({
    process_id: "a_remote",
    state: "STOPPED",
    is_remote: true,
    liveness: "OFFLINE",
  });

  it("treats an offline mirrored process as a failed issue", () => {
    expect(resourceHasIssue(remoteDown, processAdapter)).toBe(true);
    expect(summarizeResources([local, crash, remoteDown], processAdapter)).toEqual({
      total: 3,
      healthy: 1,
      issues: 2,
      failed: 2,
      remote: 1,
    });
  });

  it("filters by error text and sorts issues first", () => {
    const all = [local, crash, remoteDown];
    expect(
      selectVisibleResources(all, processAdapter, {
        query: "exit",
        issuesOnly: false,
        sort: "custom",
      }).map((p) => p.process_id)
    ).toEqual(["c_crash"]);
    expect(
      selectVisibleResources(all, processAdapter, {
        query: "",
        issuesOnly: false,
        sort: "health",
      }).map((p) => p.process_id)
    ).toEqual(["c_crash", "a_remote", "b_local"]);
  });
});
