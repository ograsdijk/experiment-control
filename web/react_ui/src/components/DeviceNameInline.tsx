import { Text, Tooltip, useComputedColorScheme } from "@mantine/core";
import { IconTopologyStar3 } from "@tabler/icons-react";
import type { ReactNode } from "react";
import type { DeviceStatus } from "../types";

type Props = {
  deviceId: string;
  device?: DeviceStatus | null;
  size?: string;
  fw?: number;
  c?: string;
  suffix?: ReactNode;
};

export function DeviceNameInline({
  deviceId,
  device,
  size,
  fw,
  c,
  suffix = null,
}: Props) {
  const computedColorScheme = useComputedColorScheme("light");
  const isRemote =
    Boolean(device?.is_remote) || device?.source_kind === "federated";
  const remotePeerId = String(device?.owner_peer_id ?? "").trim();
  const remoteTooltip = remotePeerId
    ? `Remote device (peer: ${remotePeerId})`
    : "Remote device";
  const remoteIconColor =
    computedColorScheme === "dark"
      ? "var(--mantine-color-blue-4)"
      : "var(--mantine-color-blue-6)";

  return (
    <>
      {isRemote ? (
        <Tooltip label={remoteTooltip} withArrow>
          <span
            style={{
              display: "inline-flex",
              verticalAlign: "text-bottom",
              lineHeight: 0,
              marginRight: 4,
              color: remoteIconColor,
            }}
          >
            <IconTopologyStar3 size={14} stroke={1.8} />
          </span>
        </Tooltip>
      ) : null}
      <Text span size={size} fw={fw} c={c}>
        {deviceId}
      </Text>
      {suffix}
    </>
  );
}
