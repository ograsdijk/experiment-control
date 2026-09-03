import {
  ActionIcon,
  Badge,
  Button,
  Group,
  Menu,
  Stack,
  Text,
  TextInput,
  Tooltip,
  useComputedColorScheme,
} from "@mantine/core";
import { useDraggable } from "@dnd-kit/core";
import { notifications } from "@mantine/notifications";
import {
  IconChartLine,
  IconDotsVertical,
  IconPlayerPlay,
  IconPlaylistAdd,
  IconRefresh,
  IconTerminal2,
} from "@tabler/icons-react";
import {
  memo,
  useCallback,
  useRef,
  type ReactNode,
} from "react";
import {
  CapabilityMember,
  DeviceStatus,
  PinnedCommand,
  TelemetrySignal,
} from "../types";
import { DeviceNameInline } from "./DeviceNameInline";
import { copyToClipboard } from "../utils/clipboard";
import { perfCountScoped } from "../features/performance/perfInstrumentation";
import { useDisplayedTelemetrySignal } from "../features/telemetry/DeviceTelemetryPresentationStore";
import { useDeviceTelemetrySignalNames } from "../features/telemetry/TelemetryLatestStore";
import { useNearViewport } from "../features/layout/useNearViewport";

type CapabilityParamMeta = NonNullable<CapabilityMember["params"]>[number];
type TelemetryTooltipStyles =
  | { tooltip: { backgroundColor: string; color: string; border: string } }
  | undefined;

const DARK_TELEMETRY_TOOLTIP_STYLES: TelemetryTooltipStyles = {
  tooltip: {
    backgroundColor: "var(--mantine-color-dark-6)",
    color: "var(--mantine-color-gray-0)",
    border: "1px solid var(--mantine-color-dark-4)",
  },
};
const TELEMETRY_COPY_STYLE = { cursor: "copy" } as const;

type DeviceCardProps = {
  device: DeviceStatus;
  busy: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
  onRestart: () => void;
  onPlot: (signal: string) => void;
  onCommand: () => void;
  telemetryCollapsed: boolean;
  onTelemetryToggle: () => void;
  pinnedCommands: PinnedCommand[];
  onPinnedCommand: (action: string) => void;
  onAddPinnedToDeck: (action: string) => void;
  onAddAllPinnedToDeck: () => void;
  capabilities: CapabilityMember[];
  pinnedParamValuesByAction: Record<string, Record<string, string>>;
  pinnedBusyByAction: Record<string, boolean>;
  onPinnedParamChange: (
    action: string,
    paramName: string,
    value: string
  ) => void;
  onPinnedSend: (action: string) => void;
};

function livenessClass(liveness: string) {
  if (liveness === "ONLINE") return "badge-online";
  if (liveness === "DISCONNECTED" || liveness === "STALE") {
    return "badge-disconnected";
  }
  return "badge-offline";
}

function trimNumericString(raw: string): string {
  const text = raw.trim();
  if (!text) {
    return text;
  }
  const expIdx = Math.max(text.indexOf("e"), text.indexOf("E"));
  if (expIdx >= 0) {
    const mantissa = text.slice(0, expIdx).replace(/\.?0+$/, "");
    const exponent = text.slice(expIdx + 1).replace(/^\+/, "");
    return `${mantissa}e${exponent}`;
  }
  return text.replace(/\.?0+$/, "");
}

function formatNumericShort(value: number): string {
  const abs = Math.abs(value);
  if (abs > 0 && (abs >= 1e4 || abs < 1e-3)) {
    return value.toExponential(3);
  }
  return value.toFixed(3).replace(/\.?0+$/, "");
}

function formatNumericFull(value: number): string {
  if (!Number.isFinite(value)) {
    return "";
  }
  return trimNumericString(value.toPrecision(12));
}

type DraggableTelemetrySignalRowProps = {
  deviceId: string;
  signal: string;
  children: ReactNode;
};

function DraggableTelemetrySignalRow({
  deviceId,
  signal,
  children,
}: DraggableTelemetrySignalRowProps) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `signal:${deviceId}:${signal}`,
    data: {
      kind: "signal",
      deviceId,
      signal,
    },
  });
  return (
    <div
      ref={setNodeRef}
      style={{ cursor: "grab", opacity: isDragging ? 0.55 : 1 }}
      {...attributes}
      {...listeners}
    >
      {children}
    </div>
  );
}

function renderTelemetryValue(value: TelemetrySignal["value"] | undefined) {
  if (value === null || value === undefined) {
    return { display: "n/a", full: null as string | null, numeric: false };
  }
  if (typeof value === "boolean") {
    return { display: value ? "true" : "false", full: null, numeric: false };
  }
  if (typeof value === "number") {
    if (Number.isNaN(value)) {
      return { display: "NaN", full: null, numeric: false };
    }
    return {
      display: formatNumericShort(value),
      full: formatNumericFull(value),
      numeric: true,
    };
  }
  return { display: String(value), full: null, numeric: false };
}

async function copyTelemetryValue(text: string): Promise<void> {
  const copied = await copyToClipboard(text);
  notifications.show({
    color: copied ? "teal" : "red",
    title: copied ? "Telemetry value copied" : "Copy failed",
    message: copied ? text : "Clipboard write failed",
  });
}

function DeviceTelemetryValueCell({
  deviceId,
  signalName,
  enabled,
  tooltipStyles,
  perfScope,
}: {
  deviceId: string;
  signalName: string;
  enabled: boolean;
  tooltipStyles: TelemetryTooltipStyles;
  perfScope: string;
}) {
  perfCountScoped("react.DeviceTelemetryValueCell", perfScope, 1, ".renders");
  const signal = useDisplayedTelemetrySignal(deviceId, signalName, enabled);
  const rendered = renderTelemetryValue(signal?.value);
  const fullWithUnits = rendered.numeric && rendered.full
    ? `${rendered.full}${signal?.units ? ` ${signal.units}` : ""}`
    : null;
  return fullWithUnits ? (
    <Tooltip label={`${fullWithUnits} (click to copy)`} withArrow styles={tooltipStyles}>
      <Text size="sm" fw={500} style={TELEMETRY_COPY_STYLE} onClick={() => void copyTelemetryValue(fullWithUnits)}>
        {rendered.display}{signal?.units ? ` ${signal.units}` : ""}
      </Text>
    </Tooltip>
  ) : (
    <Text size="sm" fw={500}>{rendered.display}{signal?.units ? ` ${signal.units}` : ""}</Text>
  );
}

const DeviceTelemetrySignalRow = memo(function DeviceTelemetrySignalRow({
  deviceId,
  signalName,
  onPlot,
  enabled,
  tooltipStyles,
}: {
  deviceId: string;
  signalName: string;
  onPlot: (signal: string) => void;
  enabled: boolean;
  tooltipStyles: TelemetryTooltipStyles;
}) {
  const perfScope = `${deviceId}:${signalName}`;
  return (
    <DraggableTelemetrySignalRow deviceId={deviceId} signal={signalName}>
      <Group justify="space-between" align="center" component="div">
        <Text size="sm">{signalName}</Text>
        <Group gap={6}>
          <DeviceTelemetryValueCell
            deviceId={deviceId}
            signalName={signalName}
            enabled={enabled}
            tooltipStyles={tooltipStyles}
            perfScope={perfScope}
          />
          <ActionIcon size="sm" variant="subtle" onClick={() => onPlot(signalName)} aria-label={`Plot ${signalName}`}>
            <IconChartLine size={14} />
          </ActionIcon>
        </Group>
      </Group>
    </DraggableTelemetrySignalRow>
  );
});

const DeviceTelemetryBody = memo(function DeviceTelemetryBody({
  deviceId,
  onPlot,
  enabled,
}: {
  deviceId: string;
  onPlot: (signal: string) => void;
  enabled: boolean;
}) {
  perfCountScoped("react.DeviceTelemetryBody", deviceId, 1, ".renders");
  const signalNames = useDeviceTelemetrySignalNames(deviceId, enabled);
  const computedColorScheme = useComputedColorScheme("light");
  const tooltipStyles = computedColorScheme === "dark"
    ? DARK_TELEMETRY_TOOLTIP_STYLES
    : undefined;
  return (
    <Stack gap={4}>
      {signalNames.length === 0 && <Text size="xs" c="dimmed">No telemetry yet</Text>}
      {signalNames.map((signalName) => (
        <DeviceTelemetrySignalRow
          key={signalName}
          deviceId={deviceId}
          signalName={signalName}
          onPlot={onPlot}
          enabled={enabled}
          tooltipStyles={tooltipStyles}
        />
      ))}
    </Stack>
  );
});

export function DeviceCard({
  device,
  busy,
  onConnect,
  onDisconnect,
  onRestart,
  onPlot,
  onCommand,
  telemetryCollapsed,
  onTelemetryToggle,
  pinnedCommands,
  onPinnedCommand,
  onAddPinnedToDeck,
  onAddAllPinnedToDeck,
  capabilities,
  pinnedParamValuesByAction,
  pinnedBusyByAction,
  onPinnedParamChange,
  onPinnedSend,
}: DeviceCardProps) {
  perfCountScoped("react.DeviceCard", device.device_id, 1, ".renders");
  const visibilityNodeRef = useRef<HTMLDivElement | null>(null);
  const telemetryNearViewport = useNearViewport(visibilityNodeRef);
  const onPlotRef = useRef(onPlot);
  onPlotRef.current = onPlot;
  const onTelemetryPlot = useCallback(
    (signal: string) => onPlotRef.current(signal),
    []
  );
  const effectiveParams = (
    member: CapabilityMember | undefined
  ): CapabilityParamMeta[] => {
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
  };
  const deviceStateUpper = String(device.device_state ?? "").toUpperCase();
  const livenessUpper = String(device.liveness ?? "").toUpperCase();
  const driverProcess = (
    device as DeviceStatus & { driver_process?: { state?: unknown } }
  ).driver_process;
  const driverProcessState = String(driverProcess?.state ?? "").toUpperCase();
  // STALE is a first-class manager liveness state. Trust the backend rather
  // than inferring it from process state; in particular, STARTING/OFFLINE is a
  // normal launch state and must not be presented as a blocked driver loop.
  const effectiveLiveness = livenessUpper;
  const driverStale = effectiveLiveness === "STALE";
  const driverOffline = effectiveLiveness === "OFFLINE";
  const healthy = deviceStateUpper === "OK" && effectiveLiveness === "ONLINE";
  const livenessLabel = driverStale
    ? `${driverProcessState || "RUNNING"} / STALE`
    : effectiveLiveness;
  // Primary connection action. The driver performs a disconnect+reconnect for
  // any non-OK state, so "Recover" and "Connect" call the same handler; the
  // label just reflects whether the device dropped from a live session
  // (Recover) or is cleanly/never connected (Connect).
  let connectAction: {
    label: string;
    color: string;
    onClick: () => void;
    disabled: boolean;
  };
  if (driverOffline) {
    // Driver process not reporting — a device-level connect RPC can't reach it;
    // steer the operator to Restart driver instead.
    connectAction = { label: "Connect", color: "gray", onClick: onConnect, disabled: true };
  } else if (driverStale) {
    // The driver process still exists but its event loop is blocked. Do not add
    // another device RPC behind the blocked operation; the dedicated restart
    // control remains available if the operator wants to intervene manually.
    connectAction = { label: "Stale", color: "orange", onClick: onConnect, disabled: true };
  } else if (healthy) {
    connectAction = {
      label: "Disconnect",
      color: "yellow",
      onClick: onDisconnect,
      disabled: false,
    };
  } else if (
    deviceStateUpper === "DISCONNECTED" ||
    deviceStateUpper === "UNKNOWN" ||
    deviceStateUpper === ""
  ) {
    connectAction = { label: "Connect", color: "gray", onClick: onConnect, disabled: false };
  } else {
    // DEGRADED / FAULT, or reachable-but-unhealthy (liveness DISCONNECTED while
    // the driver is alive) — reconnect to clear the bad state.
    connectAction = { label: "Recover", color: "orange", onClick: onConnect, disabled: false };
  }
  return (
    <Stack ref={visibilityNodeRef} gap="xs">
        <Group justify="space-between" align="center">
          <Stack gap={2}>
            <Text fw={600}>
              <DeviceNameInline deviceId={device.device_id} device={device} fw={600} />
            </Text>
            <Text size="xs" c="dimmed">
              hb age {device.hb_age_s?.toFixed(2) ?? "n/a"} s
            </Text>
          </Stack>
          <Group gap={6} align="center">
            <Badge className={livenessClass(effectiveLiveness)} variant="light">
              {livenessLabel}
            </Badge>
            <Button
              size="xs"
              variant="light"
              color={connectAction.color}
              onClick={connectAction.onClick}
              disabled={busy || connectAction.disabled}
            >
              {connectAction.label}
            </Button>
            <Tooltip
              label={driverOffline ? "Driver offline — restart driver" : "Restart driver"}
              withArrow
            >
              <ActionIcon
                variant={driverOffline ? "filled" : "light"}
                color="red"
                onClick={onRestart}
                disabled={busy}
              >
                <IconRefresh size={14} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>
        <Stack gap={4}>
          <Group justify="space-between" align="center">
            <Text size="xs" c="dimmed">
              Telemetry
            </Text>
            <Button
              size="compact-xs"
              variant="subtle"
              color="gray"
              onClick={onTelemetryToggle}
            >
              {telemetryCollapsed ? "Show" : "Hide"}
            </Button>
          </Group>
          {!telemetryCollapsed && (
            <DeviceTelemetryBody
              deviceId={device.device_id}
              onPlot={onTelemetryPlot}
              enabled={telemetryNearViewport}
            />
          )}
        </Stack>
        {pinnedCommands.length > 0 && (
          <Stack gap={4}>
            <Group justify="space-between" align="center">
              <Text size="xs" c="dimmed">
                Pinned commands
              </Text>
              <Menu shadow="md" width={220} position="bottom-end" withArrow withinPortal>
                <Menu.Target>
                  <ActionIcon size="xs" variant="subtle" color="gray">
                    <IconDotsVertical size={14} />
                  </ActionIcon>
                </Menu.Target>
                <Menu.Dropdown>
                  <Menu.Item
                    leftSection={<IconPlaylistAdd size={14} />}
                    onClick={onAddAllPinnedToDeck}
                  >
                    Add all to command deck
                  </Menu.Item>
                </Menu.Dropdown>
              </Menu>
            </Group>
            <Stack gap={6}>
              {pinnedCommands.map((entry) => {
                const label = entry.label?.trim();
                const buttonText = label || entry.action;
                const showTooltip = Boolean(label && label !== entry.action);
                const capability = capabilities.find(
                  (member) => member.name === entry.action
                );
                const params = effectiveParams(capability);
                const paramValues = pinnedParamValuesByAction[entry.action] ?? {};
                const busyPinned = Boolean(pinnedBusyByAction[entry.action]);
                const commandNameButton = (
                  <Button
                    key={`${entry.action}:open`}
                    size="xs"
                    variant="subtle"
                    color="gray"
                    className="pinned-command-name-button"
                    onClick={() => onPinnedCommand(entry.action)}
                  >
                    {buttonText}
                  </Button>
                );
                const buttonWithTooltip = showTooltip ? (
                  <Tooltip key={`${entry.action}:tooltip`} label={entry.action} withArrow>
                    {commandNameButton}
                  </Tooltip>
                ) : (
                  commandNameButton
                );
                return (
                  <div key={entry.action} className="pinned-command-chip">
                    <div className="pinned-command-segment pinned-command-name">
                      {buttonWithTooltip}
                    </div>
                    <div className="pinned-command-segment pinned-command-more">
                      <Menu
                        shadow="md"
                        width={220}
                        position="bottom-end"
                        withArrow
                        withinPortal
                      >
                        <Menu.Target>
                          <ActionIcon size="sm" variant="subtle" color="gray">
                            <IconDotsVertical size={14} />
                          </ActionIcon>
                        </Menu.Target>
                        <Menu.Dropdown>
                          <Menu.Item
                            leftSection={<IconTerminal2 size={14} />}
                            onClick={() => onPinnedCommand(entry.action)}
                          >
                            Open command editor
                          </Menu.Item>
                          <Menu.Item
                            leftSection={<IconPlaylistAdd size={14} />}
                            onClick={() => onAddPinnedToDeck(entry.action)}
                          >
                            Add to command deck
                          </Menu.Item>
                        </Menu.Dropdown>
                      </Menu>
                    </div>
                    <div className="pinned-command-segment pinned-command-inputs">
                      {params.map((param) => (
                        <TextInput
                          key={`${entry.action}:${param.name}`}
                          size="xs"
                          w={110}
                          value={paramValues[param.name] ?? ""}
                          onChange={(event) =>
                            onPinnedParamChange(
                              entry.action,
                              param.name,
                              event.currentTarget.value
                            )
                          }
                          onKeyDown={(event) => {
                            if (event.key !== "Enter") {
                              return;
                            }
                            if (params.length !== 1) {
                              return;
                            }
                            event.preventDefault();
                            onPinnedSend(entry.action);
                          }}
                          placeholder={
                            param.required ? `${param.name} *` : param.name
                          }
                        />
                      ))}
                    </div>
                    <div className="pinned-command-segment pinned-command-send">
                      <Tooltip label="Send command" withArrow>
                        <ActionIcon
                          variant="light"
                          color="teal"
                          size="sm"
                          onClick={() => onPinnedSend(entry.action)}
                          disabled={busyPinned || busy}
                        >
                          <IconPlayerPlay size={14} />
                        </ActionIcon>
                      </Tooltip>
                    </div>
                  </div>
                );
              })}
            </Stack>
          </Stack>
        )}
        <Group justify="space-between" align="center" mt="xs">
          <Button
            size="xs"
            variant="light"
            leftSection={<IconTerminal2 size={14} />}
            onClick={onCommand}
          >
            Command
          </Button>
          {device.last_error && (
            <Text size="xs" c="red">
              {device.last_error}
            </Text>
          )}
        </Group>
    </Stack>
  );
}
