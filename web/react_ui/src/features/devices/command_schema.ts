import type { CapabilityMember } from "../../types";

export type CapabilityParamMeta = NonNullable<CapabilityMember["params"]>[number];

export function effectiveDeviceMemberParams(
  member: CapabilityMember | undefined
): CapabilityParamMeta[] {
  if (!member) {
    return [];
  }
  if (member.kind === "property" && member.settable) {
    return [
      {
        name: "value",
        required: false,
        annotation: member.return_annotation ?? "any",
        default: undefined,
      },
    ];
  }
  return member.params ?? [];
}

export function buildParamDefaults(
  member: CapabilityMember | undefined
): Record<string, string> {
  const nextValues: Record<string, string> = {};
  for (const param of effectiveDeviceMemberParams(member)) {
    if (param.default !== undefined && param.default !== null) {
      nextValues[param.name] = String(param.default);
    } else {
      nextValues[param.name] = "";
    }
  }
  return nextValues;
}

export function mapDeviceActionForMember(
  member: CapabilityMember | undefined,
  action: string,
  params: Record<string, unknown>
): { action: string; params: Record<string, unknown> } {
  if (!member || member.kind !== "property") {
    return { action, params };
  }
  if (Object.prototype.hasOwnProperty.call(params, "value")) {
    return {
      action: "set",
      params: { name: action, value: params.value },
    };
  }
  return {
    action: "get",
    params: { name: action },
  };
}
