import {
  ActionIcon,
  Badge,
  Box,
  Card,
  Group,
  Modal,
  ScrollArea,
  SegmentedControl,
  Table,
  Text,
  UnstyledButton,
} from "@mantine/core";
import { IconX } from "@tabler/icons-react";
import { useMemo, useState, type ReactNode } from "react";

import type { DeviceStatus } from "../types";
import {
  deviceDriverProcess,
  deviceErrorText,
  deviceHasIssue,
  deviceHealth,
  deviceHealthRank,
  formatAge,
} from "../features/devices/health";
import { useDeviceListView } from "../features/devices/useDeviceListView";
import { livenessColor, processStateColor } from "../features/runtime/helpers";
import { HealthMark } from "./HealthMark";
import { ResourceListToolbar } from "./ResourceListToolbar";
import { DeviceNameInline } from "./DeviceNameInline";

type TableColumn = "device" | "health" | "link" | "driver" | "state" | "hb" | "tlm";

const TABLE_COLUMNS: { key: TableColumn; label: string; detail?: boolean }[] = [
  { key: "device", label: "Device" },
  { key: "health", label: "Health" },
  { key: "link", label: "Link" },
  { key: "driver", label: "Driver" },
  // Hidden while a device is selected: the detail card shows them instead.
  { key: "state", label: "Device state", detail: true },
  { key: "hb", label: "HB age", detail: true },
  { key: "tlm", label: "Telemetry age", detail: true },
];

type Props = {
  opened: boolean;
  onClose: () => void;
  devices: DeviceStatus[];
  renderDeviceCard: (device: DeviceStatus) => ReactNode;
};

function upper(value: unknown): string {
  return String(value ?? "").toUpperCase();
}

function compareNullableNumber(a: number | null | undefined, b: number | null | undefined) {
  const av = typeof a === "number" && Number.isFinite(a) ? a : Number.POSITIVE_INFINITY;
  const bv = typeof b === "number" && Number.isFinite(b) ? b : Number.POSITIVE_INFINITY;
  return av - bv;
}

function tableSortValue(device: DeviceStatus, column: TableColumn): string | number | null {
  switch (column) {
    case "device":
      return device.device_id.toLowerCase();
    case "health":
      return deviceHealthRank(device);
    case "link":
      return upper(device.liveness);
    case "driver":
      return upper(deviceDriverProcess(device).state);
    case "state":
      return upper(device.device_state);
    case "hb":
      return device.hb_age_s;
    case "tlm":
      return device.telemetry_age_s;
  }
}

function DeviceTile({
  device,
  selected,
  onSelect,
}: {
  device: DeviceStatus;
  selected: boolean;
  onSelect: () => void;
}) {
  const health = deviceHealth(device);
  const error = deviceErrorText(device);
  const procState = upper(deviceDriverProcess(device).state);
  const deviceState = upper(device.device_state);
  return (
    <UnstyledButton
      className={`resource-overview-tile resource-overview-tile-${health}${
        deviceHasIssue(device) ? " resource-overview-tile-issue" : ""
      }${selected ? " resource-overview-tile-selected" : ""}`}
      onClick={onSelect}
      aria-pressed={selected}
    >
      <Group gap={6} wrap="nowrap" justify="space-between">
        <Group gap={6} wrap="nowrap" style={{ minWidth: 0 }}>
          <HealthMark health={health} />
          <Text size="sm" fw={600} truncate>
            <DeviceNameInline deviceId={device.device_id} device={device} size="sm" fw={600} />
          </Text>
        </Group>
        <Badge size="xs" variant="light" color={livenessColor(device.liveness)}>
          {upper(device.liveness) || "?"}
        </Badge>
      </Group>
      <Group gap={6} mt={6} wrap="nowrap">
        {procState ? (
          <Badge size="xs" variant="outline" color={processStateColor(procState)}>
            {procState}
          </Badge>
        ) : null}
        {deviceState ? (
          <Text size="xs" c="dimmed" truncate>
            {deviceState}
          </Text>
        ) : null}
      </Group>
      <Text size="xs" c="dimmed" mt={4}>
        hb {formatAge(device.hb_age_s)} · tlm {formatAge(device.telemetry_age_s)}
      </Text>
      {error ? (
        <Text size="xs" c="red" mt={4} lineClamp={2} title={error}>
          {error}
        </Text>
      ) : null}
    </UnstyledButton>
  );
}

export function DeviceOverviewModal({ opened, onClose, devices, renderDeviceCard }: Props) {
  const view = useDeviceListView(devices, { persist: false, initialSort: "health" });
  const [layout, setLayout] = useState<"grid" | "table">("table");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tableSort, setTableSort] = useState<{ column: TableColumn; reverse: boolean } | null>(
    null
  );

  const selectedDevice = useMemo(
    () => devices.find((device) => device.device_id === selectedId) ?? null,
    [devices, selectedId]
  );

  const tableRows = useMemo(() => {
    if (!tableSort) {
      return view.visibleDevices;
    }
    const { column, reverse } = tableSort;
    const rows = [...view.visibleDevices].sort((a, b) => {
      const av = tableSortValue(a, column);
      const bv = tableSortValue(b, column);
      const cmp =
        typeof av === "string" && typeof bv === "string"
          ? av.localeCompare(bv)
          : compareNullableNumber(av as number | null, bv as number | null);
      return cmp || a.device_id.localeCompare(b.device_id);
    });
    return reverse ? rows.reverse() : rows;
  }, [tableSort, view.visibleDevices]);

  const compactTable = selectedDevice !== null;
  const visibleColumns = compactTable
    ? TABLE_COLUMNS.filter((column) => !column.detail)
    : TABLE_COLUMNS;

  const toggleSelected = (deviceId: string) =>
    setSelectedId((prev) => (prev === deviceId ? null : deviceId));

  const content =
    view.visibleDevices.length === 0 ? (
      <Text size="sm" c="dimmed" ta="center" py="xl">
        {devices.length === 0 ? "No devices." : "No devices match the current filter."}
      </Text>
    ) : layout === "grid" ? (
      <div className="resource-overview-grid">
        {view.visibleDevices.map((device) => (
          <DeviceTile
            key={device.device_id}
            device={device}
            selected={device.device_id === selectedId}
            onSelect={() => toggleSelected(device.device_id)}
          />
        ))}
      </div>
    ) : (
      <Table
        className="resource-overview-table"
        highlightOnHover
        stickyHeader
        verticalSpacing={4}
        fz="xs"
      >
        <Table.Thead>
          <Table.Tr>
            {visibleColumns.map(({ key, label }) => {
              const active = tableSort?.column === key;
              return (
                <Table.Th key={key}>
                  <UnstyledButton
                    className="resource-overview-th"
                    onClick={() =>
                      setTableSort((prev) =>
                        prev?.column === key
                          ? prev.reverse
                            ? null
                            : { column: key, reverse: true }
                          : { column: key, reverse: false }
                      )
                    }
                  >
                    {label}
                    {active ? (tableSort?.reverse ? " ▼" : " ▲") : ""}
                  </UnstyledButton>
                </Table.Th>
              );
            })}
            {compactTable ? null : <Table.Th>Error</Table.Th>}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {tableRows.map((device) => {
            const health = deviceHealth(device);
            const procState = upper(deviceDriverProcess(device).state);
            const error = deviceErrorText(device);
            return (
              <Table.Tr
                key={device.device_id}
                className={`resource-overview-row${
                  device.device_id === selectedId ? " resource-overview-row-selected" : ""
                }`}
                onClick={() => toggleSelected(device.device_id)}
              >
                <Table.Td>
                  <DeviceNameInline deviceId={device.device_id} device={device} size="xs" fw={600} />
                </Table.Td>
                <Table.Td>
                  <HealthMark health={health} /> {health}
                </Table.Td>
                <Table.Td>
                  <Badge size="xs" variant="light" color={livenessColor(device.liveness)}>
                    {upper(device.liveness) || "?"}
                  </Badge>
                </Table.Td>
                <Table.Td>
                  {procState ? (
                    <Badge size="xs" variant="outline" color={processStateColor(procState)}>
                      {procState}
                    </Badge>
                  ) : null}
                </Table.Td>
                {compactTable ? null : (
                  <>
                    <Table.Td>{upper(device.device_state)}</Table.Td>
                    <Table.Td>{formatAge(device.hb_age_s)}</Table.Td>
                    <Table.Td>{formatAge(device.telemetry_age_s)}</Table.Td>
                    <Table.Td className="resource-overview-error-cell">
                      <Text size="xs" c="red" truncate title={error || undefined}>
                        {error}
                      </Text>
                    </Table.Td>
                  </>
                )}
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
    );

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Devices"
      size="clamp(56rem, 92vw, 96rem)"
      centered
    >
      <Group gap="sm" align="flex-start" wrap="nowrap" mb="sm">
        <Box style={{ flex: 1, maxWidth: 640 }}>
          <ResourceListToolbar view={view} noun="devices" healthyTitle="Online and OK" autoFocus />
        </Box>
        <SegmentedControl
          size="xs"
          value={layout}
          onChange={(value) => setLayout(value === "table" ? "table" : "grid")}
          data={[
            { value: "table", label: "Table" },
            { value: "grid", label: "Grid" },
          ]}
        />
      </Group>
      <Group gap="md" align="flex-start" wrap="nowrap">
        <ScrollArea.Autosize
          style={{ flex: 1, minWidth: 0 }}
          mah="min(70vh, 48rem)"
          type="auto"
          offsetScrollbars
        >
          {content}
        </ScrollArea.Autosize>
        {selectedDevice ? (
          <Box className="resource-overview-detail">
            <Group justify="space-between" mb={6}>
              <Text size="xs" c="dimmed">
                Selected device
              </Text>
              <ActionIcon
                size="sm"
                variant="subtle"
                color="gray"
                aria-label="Close device details"
                onClick={() => setSelectedId(null)}
              >
                <IconX size={14} />
              </ActionIcon>
            </Group>
            <ScrollArea.Autosize mah="calc(min(70vh, 48rem) - 2rem)" type="auto">
              <Card withBorder radius="lg" p="md" className="device-card">
                {renderDeviceCard(selectedDevice)}
              </Card>
            </ScrollArea.Autosize>
          </Box>
        ) : null}
      </Group>
    </Modal>
  );
}
