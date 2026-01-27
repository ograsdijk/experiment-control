import type { ApiError } from "../../api";

type ErrorToastContext = {
  targetKind?: "device" | "process";
  targetId?: string | null;
  action?: string | null;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function uniquePush(parts: string[], part: string) {
  const clean = part.trim();
  if (!clean) {
    return;
  }
  if (!parts.includes(clean)) {
    parts.push(clean);
  }
}

export function formatApiErrorToastMessage(
  error: ApiError | undefined,
  context: ErrorToastContext = {}
): string {
  const errorObj = asRecord(error);
  const baseMessage =
    asString(errorObj?.message) || asString(errorObj?.code) || "Unknown error";

  const sourceProcessId = asString(errorObj?.process_id);
  const sourceDeviceId = asString(errorObj?.device_id);
  const sourceAction = asString(errorObj?.action) || asString(context.action);
  const targetKind = context.targetKind;
  const targetId = asString(context.targetId);

  const detailParts: string[] = [];

  if (sourceProcessId) {
    uniquePush(detailParts, `process: ${sourceProcessId}`);
  }

  if (targetKind === "device" && targetId) {
    uniquePush(detailParts, `driver: ${targetId}`);
  } else if (targetKind === "process" && targetId && sourceProcessId !== targetId) {
    uniquePush(detailParts, `process: ${targetId}`);
  }

  if (!targetId && sourceDeviceId) {
    uniquePush(detailParts, `driver: ${sourceDeviceId}`);
  }

  const commandDevice = sourceDeviceId || targetId;
  if (commandDevice && sourceAction) {
    uniquePush(detailParts, `command: ${commandDevice}.${sourceAction}`);
  }

  const details = asRecord(errorObj?.details);
  const nestedDetails = asRecord(details?.details);
  const telemetryDevice = asString(nestedDetails?.device);
  const telemetrySignal = asString(nestedDetails?.signal);
  const telemetryBinding = asString(nestedDetails?.binding);
  if (telemetryDevice && telemetrySignal) {
    const label = telemetryBinding
      ? `telemetry: ${telemetryDevice}.${telemetrySignal} (${telemetryBinding})`
      : `telemetry: ${telemetryDevice}.${telemetrySignal}`;
    uniquePush(detailParts, label);
  }

  if (detailParts.length <= 0) {
    return baseMessage;
  }
  return `${baseMessage} (${detailParts.join(" | ")})`;
}

