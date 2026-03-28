import {
  Badge,
  Button,
  Card,
  Group,
  Modal,
  Stack,
  Table,
  Text,
} from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import { processStateColor } from "../features/runtime/helpers";
import type { InfluxWriterStatus } from "../features/influx/types";

type Props = {
  opened: boolean;
  onClose: () => void;
  title: string;
  influxWriterState: string;
  influxWriterProcessId: string | null;
  influxWriterStatus: InfluxWriterStatus | null;
  influxWriterLoading: boolean;
  influxProcessCapabilitiesError: string | null;
  influxCommandsBlocked: boolean;
  influxAnyCommandBusy: boolean;
  influxSupportsStatus: boolean;
  influxSupportsEnable: boolean;
  influxSupportsDisable: boolean;
  influxSupportsFlush: boolean;
  influxStatusBusy: boolean;
  influxEnableBusy: boolean;
  influxDisableBusy: boolean;
  influxFlushBusy: boolean;
  onRefreshStatus: () => Promise<unknown> | void;
  onEnable: () => Promise<unknown> | void;
  onDisable: () => Promise<unknown> | void;
  onFlush: () => Promise<unknown> | void;
};

function formatEpochSeconds(epochSeconds: number | null): string {
  if (epochSeconds === null || !Number.isFinite(epochSeconds)) {
    return "n/a";
  }
  try {
    return new Date(epochSeconds * 1000).toLocaleString(undefined, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return "n/a";
  }
}

export function InfluxWriterModal({
  opened,
  onClose,
  title,
  influxWriterState,
  influxWriterProcessId,
  influxWriterStatus,
  influxWriterLoading,
  influxProcessCapabilitiesError,
  influxCommandsBlocked,
  influxAnyCommandBusy,
  influxSupportsStatus,
  influxSupportsEnable,
  influxSupportsDisable,
  influxSupportsFlush,
  influxStatusBusy,
  influxEnableBusy,
  influxDisableBusy,
  influxFlushBusy,
  onRefreshStatus,
  onEnable,
  onDisable,
  onFlush,
}: Props) {
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={title}
      size="clamp(56rem, 92vw, 96rem)"
      centered
      zIndex={450}
    >
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" wrap="wrap">
          <Stack gap={2}>
            <Group gap="xs">
              <Badge variant="light" color={processStateColor(influxWriterState)}>
                {influxWriterState}
              </Badge>
              <Text size="xs" c="dimmed">
                {influxWriterProcessId ?? "No Influx writer process"}
              </Text>
              <Badge
                variant="light"
                color={influxWriterStatus?.enabled === false ? "orange" : "teal"}
              >
                {influxWriterStatus?.enabled === false ? "disabled" : "enabled"}
              </Badge>
            </Group>
            <Text size="xs" c="dimmed">
              queue {influxWriterStatus?.queueDepth ?? "n/a"} /{" "}
              {influxWriterStatus?.queueCapacity ?? "n/a"} | routes{" "}
              {influxWriterStatus?.routesCount ?? 0}
            </Text>
          </Stack>
          <Group gap="xs">
            <Button
              size="xs"
              variant="light"
              color="teal"
              loading={influxEnableBusy}
              disabled={
                influxCommandsBlocked ||
                !influxSupportsEnable ||
                influxAnyCommandBusy ||
                influxWriterStatus?.enabled === true
              }
              onClick={() => {
                void onEnable();
              }}
            >
              Enable
            </Button>
            <Button
              size="xs"
              variant="light"
              color="orange"
              loading={influxDisableBusy}
              disabled={
                influxCommandsBlocked ||
                !influxSupportsDisable ||
                influxAnyCommandBusy ||
                influxWriterStatus?.enabled === false
              }
              onClick={() => {
                void onDisable();
              }}
            >
              Disable
            </Button>
            <Button
              size="xs"
              variant="light"
              color="blue"
              loading={influxFlushBusy}
              disabled={influxCommandsBlocked || !influxSupportsFlush || influxAnyCommandBusy}
              onClick={() => {
                void onFlush();
              }}
            >
              Flush
            </Button>
            <Button
              size="xs"
              variant="light"
              leftSection={<IconRefresh size={14} />}
              loading={influxWriterLoading || influxStatusBusy}
              disabled={influxCommandsBlocked || !influxSupportsStatus || influxAnyCommandBusy}
              onClick={() => {
                void onRefreshStatus();
              }}
            >
              Refresh status
            </Button>
          </Group>
        </Group>

        {influxProcessCapabilitiesError ? (
          <Text size="sm" c="red">
            {influxProcessCapabilitiesError}
          </Text>
        ) : null}
        {influxWriterStatus?.error ? (
          <Text size="sm" c="red">
            {influxWriterStatus.error}
          </Text>
        ) : null}
        {influxWriterStatus?.lastError ? (
          <Text size="sm" c="red">
            last write error: {influxWriterStatus.lastError}
          </Text>
        ) : null}

        <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
          <Stack gap={8}>
            <Group gap="xs" wrap="wrap">
              <Badge variant="light" color="gray">
                points written {influxWriterStatus?.counters.pointsWritten ?? 0}
              </Badge>
              <Badge variant="light" color="gray">
                write errors {influxWriterStatus?.counters.writeErrors ?? 0}
              </Badge>
              <Badge variant="light" color="gray">
                dropped {influxWriterStatus?.counters.pointsDroppedOverflow ?? 0}
              </Badge>
              <Badge variant="light" color="gray">
                skipped remote {influxWriterStatus?.counters.pointsSkippedRemote ?? 0}
              </Badge>
              <Badge variant="light" color="gray">
                batches {influxWriterStatus?.counters.batchesWritten ?? 0}
              </Badge>
              <Badge variant="light" color="gray">
                flush interval {influxWriterStatus?.flushIntervalS ?? 0}s
              </Badge>
              <Badge variant="light" color="gray">
                batch size {influxWriterStatus?.batchMaxPoints ?? 0}
              </Badge>
              <Badge variant="light" color="gray">
                overflow {influxWriterStatus?.overflowPolicy ?? "n/a"}
              </Badge>
              <Badge variant="light" color="gray">
                default destination {influxWriterStatus?.defaultDestination ?? "n/a"}
              </Badge>
              <Badge variant="light" color="gray">
                last flush {formatEpochSeconds(influxWriterStatus?.lastFlushWallS ?? null)}
              </Badge>
            </Group>
            <Text size="xs" c="dimmed">
              disabled devices:{" "}
              {influxWriterStatus?.disabledDevices.length
                ? influxWriterStatus.disabledDevices.join(", ")
                : "none"}
            </Text>
            <Text size="xs" c="dimmed">
              device tag keys:{" "}
              {influxWriterStatus?.deviceTagKeys.length
                ? influxWriterStatus.deviceTagKeys.join(", ")
                : "none"}
            </Text>
          </Stack>
        </Card>

        <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
          <Stack gap="xs">
            <Text fw={600} size="sm">
              Destinations
            </Text>
            {influxWriterStatus?.destinationsInfo.length ? (
              <Table.ScrollContainer minWidth={980} type="native">
                <Table
                  fz="xs"
                  verticalSpacing="xs"
                  horizontalSpacing="sm"
                  striped
                  highlightOnHover
                  stickyHeader
                >
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Name</Table.Th>
                      <Table.Th>Host</Table.Th>
                      <Table.Th ta="right">Port</Table.Th>
                      <Table.Th>Org</Table.Th>
                      <Table.Th>Bucket</Table.Th>
                      <Table.Th>Precision</Table.Th>
                      <Table.Th>Fallback Measurement</Table.Th>
                      <Table.Th ta="right">Timeout (s)</Table.Th>
                      <Table.Th>URL</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {influxWriterStatus.destinationsInfo.map((dest) => (
                      <Table.Tr key={dest.name}>
                        <Table.Td>
                          <Badge size="xs" variant="light" color="gray">
                            {dest.name}
                          </Badge>
                        </Table.Td>
                        <Table.Td>{dest.host || "n/a"}</Table.Td>
                        <Table.Td ta="right">{dest.port ?? "n/a"}</Table.Td>
                        <Table.Td>{dest.org || "n/a"}</Table.Td>
                        <Table.Td>{dest.bucket || "n/a"}</Table.Td>
                        <Table.Td>{dest.precision || "n/a"}</Table.Td>
                        <Table.Td>
                          <Text size="xs" lineClamp={1} style={{ maxWidth: 220 }}>
                            {dest.measurement || "n/a"}
                          </Text>
                        </Table.Td>
                        <Table.Td ta="right">{dest.requestTimeoutS ?? "n/a"}</Table.Td>
                        <Table.Td>
                          <Text
                            size="xs"
                            lineClamp={1}
                            style={{ maxWidth: 260, wordBreak: "break-all" }}
                          >
                            {dest.url || "n/a"}
                          </Text>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            ) : (
              <Text size="xs" c="dimmed">
                No destination info available.
              </Text>
            )}
          </Stack>
        </Card>

        <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
          <Stack gap="xs">
            <Text fw={600} size="sm">
              Measurement Resolution
            </Text>
            <Text size="xs" c="dimmed">
              Resolved measurement used per known device (route overrides and device class
              mapping applied).
            </Text>
            {influxWriterStatus?.measurementResolution.length ? (
              <Table.ScrollContainer minWidth={980} type="native">
                <Table
                  fz="xs"
                  verticalSpacing="xs"
                  horizontalSpacing="sm"
                  striped
                  highlightOnHover
                  stickyHeader
                >
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Device</Table.Th>
                      <Table.Th>Device Class</Table.Th>
                      <Table.Th>Destination</Table.Th>
                      <Table.Th>Resolved Measurement</Table.Th>
                      <Table.Th>Route Override: Measurement</Table.Th>
                      <Table.Th>Route Override: Class</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {[...influxWriterStatus.measurementResolution]
                      .sort((a, b) => a.deviceId.localeCompare(b.deviceId))
                      .map((row) => (
                        <Table.Tr key={row.deviceId}>
                          <Table.Td>{row.deviceId}</Table.Td>
                          <Table.Td>{row.deviceType ?? "n/a"}</Table.Td>
                          <Table.Td>
                            <Badge size="xs" variant="light" color="gray">
                              {row.destination}
                            </Badge>
                          </Table.Td>
                          <Table.Td>{row.measurement}</Table.Td>
                          <Table.Td>{row.routeMeasurement ?? "n/a"}</Table.Td>
                          <Table.Td>{row.routeDeviceType ?? "n/a"}</Table.Td>
                        </Table.Tr>
                      ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            ) : (
              <Text size="xs" c="dimmed">
                No resolved measurement rows available yet.
              </Text>
            )}
          </Stack>
        </Card>
      </Stack>
    </Modal>
  );
}
