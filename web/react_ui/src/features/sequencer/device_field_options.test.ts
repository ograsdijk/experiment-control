import { describe, expect, it } from "vitest";
import {
  callableActionNames,
  deviceNames,
  settableMemberNames,
} from "./device_field_options";
import type { CapabilityMember, DeviceStatus } from "../../types";

const dev = (device_id: string): DeviceStatus =>
  ({ device_id } as unknown as DeviceStatus);

const member = (
  name: string,
  extra: Partial<CapabilityMember> = {}
): CapabilityMember => ({ name, ...extra });

describe("device_field_options", () => {
  it("deviceNames is sorted, de-duplicated, and drops blanks", () => {
    expect(deviceNames([dev("zaber"), dev("bristol"), dev("zaber"), dev("")])).toEqual([
      "bristol",
      "zaber",
    ]);
  });

  it("callableActionNames keeps methods and stream__ calls, drops properties/attributes", () => {
    const members = [
      member("move_absolute", { kind: "method" }),
      member("stream__read_waveform_frame", { kind: "method" }),
      member("temperature", { kind: "property", readable: true, settable: false }),
      member("offset", { kind: "attribute", readable: true, settable: true }),
      member("stream__weird", { kind: "stream" }), // stream-named but odd kind: still kept
    ];
    expect(callableActionNames(members)).toEqual([
      "move_absolute",
      "stream__read_waveform_frame",
      "stream__weird",
    ]);
    expect(callableActionNames(null)).toEqual([]);
  });

  it("settableMemberNames keeps only settable members", () => {
    const members = [
      member("setpoint", { kind: "property", settable: true }),
      member("voltage", { kind: "attribute", settable: true }),
      member("temperature", { kind: "property", settable: false }),
      member("fire", { kind: "method" }),
    ];
    expect(settableMemberNames(members)).toEqual(["setpoint", "voltage"]);
    expect(settableMemberNames(undefined)).toEqual([]);
  });
});
