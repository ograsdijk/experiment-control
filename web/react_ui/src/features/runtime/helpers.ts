import type { CapabilityMember, DeviceStatus, ProcessStatus } from "../../types";

export function pinnedCommandKey(deviceId: string, action: string) {
  return `${deviceId}:${action}`;
}

export function interlockRuleKey(processId: string, scopeId: string, ruleId: string) {
  return `${processId}:${scopeId}:${ruleId}`;
}

export function isDeviceDisconnected(device: DeviceStatus) {
  const deviceState = String(device.device_state ?? "").toUpperCase();
  if (deviceState === "DISCONNECTED") {
    return true;
  }
  return String(device.liveness ?? "").toUpperCase() === "DISCONNECTED";
}

export function isDeviceDriverStarted(device: DeviceStatus) {
  const driverProcess = (
    device as DeviceStatus & { driver_process?: { state?: unknown } }
  ).driver_process;
  const processState = String(driverProcess?.state ?? "").toUpperCase();
  return processState === "RUNNING" || processState === "STARTING";
}

export function shouldPreloadCapabilities(device: DeviceStatus) {
  if (isDeviceDisconnected(device)) {
    return false;
  }
  if (device.registered === false) {
    return false;
  }
  return true;
}

export function processStateColor(state: string | null | undefined): string {
  const normalized = String(state ?? "").toUpperCase();
  if (normalized === "RUNNING") {
    return "teal";
  }
  if (normalized === "STARTING" || normalized === "STOPPING") {
    return "yellow";
  }
  if (normalized === "FAILED" || normalized === "CRASHLOOP") {
    return "red";
  }
  if (normalized === "EXITED" || normalized === "STOPPED") {
    return "gray";
  }
  return "blue";
}

export function sequencerRuntimeStateColor(
  runtimeState: string | null | undefined,
  processState: string | null | undefined
): string {
  const proc = String(processState ?? "").toUpperCase();
  if (proc && proc !== "RUNNING") {
    return processStateColor(proc);
  }
  const runtime = String(runtimeState ?? "").toUpperCase();
  if (runtime === "RUNNING") {
    return "teal";
  }
  if (runtime === "PAUSED" || runtime === "STOP_REQUESTED") {
    return "yellow";
  }
  if (runtime === "ERROR") {
    return "red";
  }
  if (runtime === "IDLE" || runtime === "STOPPED") {
    return "gray";
  }
  return "blue";
}

export function fileNameFromPath(path: string | null | undefined): string | null {
  if (!path) {
    return null;
  }
  const raw = String(path).trim();
  if (!raw) {
    return null;
  }
  const parts = raw.split(/[\\/]/);
  const last = parts[parts.length - 1];
  return last && last.length > 0 ? last : raw;
}

export function isHdfWriterProcess(process: ProcessStatus): boolean {
  const processId = String(process.process_id ?? "").toLowerCase();
  if (processId === "hdf_writer" || processId.includes("hdf")) {
    return true;
  }
  const argv = Array.isArray(process.argv) ? process.argv : [];
  return argv.some((arg) => {
    const normalized = String(arg).toLowerCase();
    return (
      normalized.includes("hdf_writer.py") ||
      normalized.includes("processes.hdf_writer")
    );
  });
}

export function isSequencerProcess(process: ProcessStatus): boolean {
  const processId = String(process.process_id ?? "").toLowerCase();
  if (processId === "sequencer") {
    return true;
  }
  const argv = Array.isArray(process.argv) ? process.argv : [];
  return argv.some((arg) => {
    const normalized = String(arg).toLowerCase();
    return (
      normalized.includes("experiment_control.sequencer") ||
      normalized.includes("sequencer.py")
    );
  });
}

export function isProcessRpcStateAvailable(process: ProcessStatus): boolean {
  const state = String(process.state ?? "").toUpperCase();
  return ["RUNNING", "STARTING", "STOPPING"].includes(state);
}

export function supportsProcessCapability(
  capabilities: CapabilityMember[],
  name: string
): boolean {
  return capabilities.some((member) => member.name === name);
}

export function formatFreqHz(value: number): string {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  const abs = Math.abs(value);
  if (abs > 0 && (abs >= 1e6 || abs < 1e-2)) {
    return `${value.toExponential(3)} Hz`;
  }
  return `${value.toFixed(3).replace(/\.?0+$/, "")} Hz`;
}
