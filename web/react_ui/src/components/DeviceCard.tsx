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
import type { ReactNode } from "react";
import {
  CapabilityMember,
  DeviceStatus,
  PinnedCommand,
  TelemetrySignal,
} from "../types";
import { DeviceNameInline } from "./DeviceNameInline";

type CapabilityParamMeta = NonNullable<CapabilityMember["params"]>[number];

type DeviceCardProps = {
  device: DeviceStatus;
  signals: Record<string, TelemetrySignal> | undefined;
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
  if (liveness === "DISCONNECTED") return "badge-disconnected";
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

export function DeviceCard({
  device,
  signals,
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
  const computedColorScheme = useComputedColorScheme("light");
  const darkTooltipStyles =
    computedColorScheme === "dark"
      ? {
          tooltip: {
            backgroundColor: "var(--mantine-color-dark-6)",
            color: "var(--mantine-color-gray-0)",
            border: "1px solid var(--mantine-color-dark-4)",
          },
        }
      : undefined;
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
  const signalEntries = Object.entries(signals ?? {}).sort((a, b) =>
    a[0].localeCompare(b[0])
  );
  const renderValue = (value: TelemetrySignal["value"]) => {
    if (value === null || value === undefined) {
      return { display: "n/a", full: null as string | null, numeric: false };
    }
    if (typeof value === "boolean") {
      return {
        display: value ? "true" : "false",
        full: null as string | null,
        numeric: false,
      };
    }
    if (typeof value === "number") {
      if (Number.isNaN(value)) {
        return { display: "NaN", full: null as string | null, numeric: false };
      }
      return {
        display: formatNumericShort(value),
        full: formatNumericFull(value),
        numeric: true,
      };
    }
    return { display: String(value), full: null as string | null, numeric: false };
  };
  const copyTelemetryValue = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      notifications.show({
        color: "teal",
        title: "Telemetry value copied",
        message: text,
      });
    } catch (error) {
      notifications.show({
        color: "red",
        title: "Copy failed",
        message: error instanceof Error ? error.message : "Clipboard write failed",
      });
    }
  };
  const disconnected =
    String(device.device_state ?? "").toUpperCase() === "DISCONNECTED" ||
    String(device.liveness ?? "").toUpperCase() === "DISCONNECTED";
  return (
    <Stack gap="xs">
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
            <Badge className={livenessClass(device.liveness)} variant="light">
              {device.liveness}
            </Badge>
            <Button
              size="xs"
              variant="light"
              color={disconnected ? "gray" : "yellow"}
              onClick={disconnected ? onConnect : onDisconnect}
              disabled={busy}
            >
              {disconnected ? "Connect" : "Disconnect"}
            </Button>
            <Tooltip label="Restart driver" withArrow>
              <ActionIcon
                variant="light"
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
            <Stack gap={4}>
              {signalEntries.length === 0 && (
                <Text size="xs" c="dimmed">
                  No telemetry yet
                </Text>
              )}
              {signalEntries.map(([name, sig]) => (
                (() => {
                  const rendered = renderValue(sig.value);
                  const fullWithUnits =
                    rendered.numeric && rendered.full
                      ? `${rendered.full}${sig.units ? ` ${sig.units}` : ""}`
                      : null;
                  return (
                    <DraggableTelemetrySignalRow
                      key={name}
                      deviceId={device.device_id}
                      signal={name}
                    >
                      <Group justify="space-between" align="center" component="div">
                        <Text size="sm">{name}</Text>
                        <Group gap={6}>
                          {fullWithUnits ? (
                            <Tooltip
                              label={`${fullWithUnits} (click to copy)`}
                              withArrow
                              styles={darkTooltipStyles}
                            >
                              <Text
                                size="sm"
                                fw={500}
                                style={{ cursor: "copy" }}
                                onClick={() => {
                                  void copyTelemetryValue(fullWithUnits);
                                }}
                              >
                                {rendered.display}
                              </Text>
                            </Tooltip>
                          ) : (
                            <Text size="sm" fw={500}>
                              {rendered.display}
                            </Text>
                          )}
                          {sig.units && (
                            <Text size="xs" c="dimmed">
                              {sig.units}
                            </Text>
                          )}
                          <Tooltip label="Add to plot" withArrow>
                            <ActionIcon
                              variant="light"
                              color="teal"
                              size="sm"
                              onClick={() => onPlot(name)}
                            >
                              <IconChartLine size={14} />
                            </ActionIcon>
                          </Tooltip>
                        </Group>
                      </Group>
                    </DraggableTelemetrySignalRow>
                  );
                })()
              ))}
            </Stack>
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

