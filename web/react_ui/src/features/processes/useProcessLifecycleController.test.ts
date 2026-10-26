// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import { restartProcess, startProcess, stopProcess } from "../../api";
import type { ProcessStatus } from "../../types";
import { useProcessLifecycleController } from "./useProcessLifecycleController";

vi.mock("../../api", () => ({
  startProcess: vi.fn().mockResolvedValue({ ok: true }),
  stopProcess: vi.fn().mockResolvedValue({ ok: true }),
  restartProcess: vi.fn().mockResolvedValue({ ok: true }),
}));
vi.mock("@mantine/notifications", () => ({ notifications: { show: vi.fn() } }));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("process lifecycle ownership", () => {
  it.each(["start", "stop", "restart"] as const)("%s only submits for local processes", async (action) => {
    vi.clearAllMocks();
    const processes: ProcessStatus[] = [
      { process_id: "local", state: "STOPPED" },
      { process_id: "remote_flag", state: "STOPPED", is_remote: true },
      { process_id: "remote_source", state: "RUNNING", source_kind: "federated" },
    ];
    const args = {
      processes,
      processBusyById: {},
      setProcessBusy: vi.fn(),
      invalidateProcessCapabilities: vi.fn(),
      refreshProcesses: vi.fn().mockResolvedValue(processes),
      refreshHdfWriterStatus: vi.fn(),
      refreshSequencerStatus: vi.fn(),
    };
    let controller!: ReturnType<typeof useProcessLifecycleController>;
    function Probe() {
      controller = useProcessLifecycleController(args);
      return null;
    }
    const root = createRoot(document.createElement("div"));
    try {
      await act(async () => root.render(createElement(Probe)));
      for (const id of ["remote_flag", "remote_source", "missing"]) {
        await act(async () => controller.handleProcessAction(id, action));
      }
      expect(startProcess).not.toHaveBeenCalled();
      expect(stopProcess).not.toHaveBeenCalled();
      expect(restartProcess).not.toHaveBeenCalled();
      expect(args.setProcessBusy).not.toHaveBeenCalled();
      await act(async () => controller.handleProcessAction("local", action));
      const api = { start: startProcess, stop: stopProcess, restart: restartProcess }[action];
      expect(api).toHaveBeenCalledOnce();
      expect(api).toHaveBeenCalledWith("local");
      expect(args.refreshProcesses).toHaveBeenCalledOnce();
      expect(args.setProcessBusy.mock.calls).toEqual([["local", true], ["local", false]]);
    } finally {
      act(() => root.unmount());
    }
  });
});
