import {
  Badge,
  Button,
  Card,
  Group,
  Modal,
  ScrollArea,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
} from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import type { MutableRefObject } from "react";
import {
  formatLogTime,
  logEntryKey,
  logSourceKindColor,
} from "../features/logs/utils";
import type { DeviceStatus, LogEntry, ProcessStatus } from "../types";
import { DeviceNameInline } from "./DeviceNameInline";

type Props = {
  opened: boolean;
  onClose: () => void;
  connected: boolean;
  filteredRows: LogEntry[];
  totalRows: number;
  autoScroll: boolean;
  onAutoScrollChange: (value: boolean) => void;
  loading: boolean;
  onReload: () => void;
  onClear: () => void;
  severityFilter: string;
  onSeverityFilterChange: (value: string) => void;
  sourceFilter: string;
  onSourceFilterChange: (value: string) => void;
  deviceFilter: string;
  onDeviceFilterChange: (value: string) => void;
  processFilter: string;
  onProcessFilterChange: (value: string) => void;
  textFilter: string;
  onTextFilterChange: (value: string) => void;
  devices: DeviceStatus[];
  processes: ProcessStatus[];
  viewportRef: MutableRefObject<HTMLDivElement | null>;
  expandedByKey: Record<string, boolean>;
  onToggleExpanded: (key: string) => void;
  onCopyMessage: (message: string) => void;
};

export function LogsModal({
  opened,
  onClose,
  connected,
  filteredRows,
  totalRows,
  autoScroll,
  onAutoScrollChange,
  loading,
  onReload,
  onClear,
  severityFilter,
  onSeverityFilterChange,
  sourceFilter,
  onSourceFilterChange,
  deviceFilter,
  onDeviceFilterChange,
  processFilter,
  onProcessFilterChange,
  textFilter,
  onTextFilterChange,
  devices,
  processes,
  viewportRef,
  expandedByKey,
  onToggleExpanded,
  onCopyMessage,
}: Props) {
  const deviceById = new Map(devices.map((device) => [device.device_id, device]));
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Logs"
      size="xl"
      centered
      zIndex={430}
    >
      <Stack gap="sm">
        <Group justify="space-between">
          <Group gap="xs">
            <Badge variant="light" color={connected ? "teal" : "red"}>
              {connected ? "Live" : "Disconnected"}
            </Badge>
            <Text size="xs" c="dimmed">
              {filteredRows.length} shown / {totalRows} loaded
            </Text>
          </Group>
          <Group gap="xs">
            <Switch
              size="sm"
              checked={autoScroll}
              onChange={(event) => onAutoScrollChange(event.currentTarget.checked)}
              label="Auto-scroll"
            />
            <Button
              size="xs"
              variant="light"
              loading={loading}
              leftSection={<IconRefresh size={14} />}
              onClick={onReload}
            >
              Reload
            </Button>
            <Button size="xs" variant="light" color="red" onClick={onClear}>
              Clear
            </Button>
          </Group>
        </Group>
        <Group grow align="flex-end">
          <Select
            label="Severity"
            comboboxProps={{ zIndex: 500 }}
            value={severityFilter}
            onChange={(value) => onSeverityFilterChange(value ?? "all")}
            data={[
              { value: "all", label: "All severities" },
              { value: "debug", label: "Debug" },
              { value: "info", label: "Info" },
              { value: "warning", label: "Warning" },
              { value: "error", label: "Error" },
              { value: "critical", label: "Critical" },
            ]}
          />
          <Select
            label="Source"
            comboboxProps={{ zIndex: 500 }}
            value={sourceFilter}
            onChange={(value) => onSourceFilterChange(value ?? "all")}
            data={[
              { value: "all", label: "All sources" },
              { value: "manager", label: "Manager" },
              { value: "driver", label: "Driver" },
              { value: "process", label: "Process" },
            ]}
          />
          <Select
            label="Device"
            comboboxProps={{ zIndex: 500 }}
            value={deviceFilter}
            onChange={(value) => onDeviceFilterChange(value ?? "all")}
            data={[
              { value: "all", label: "All devices" },
              ...devices.map((device) => ({
                value: device.device_id,
                label: `${
                  device.is_remote || device.source_kind === "federated"
                    ? "⇄ "
                    : ""
                }${device.device_id}`,
              })),
            ]}
          />
          <Select
            label="Process"
            comboboxProps={{ zIndex: 500 }}
            value={processFilter}
            onChange={(value) => onProcessFilterChange(value ?? "all")}
            data={[
              { value: "all", label: "All processes" },
              ...processes.map((process) => ({
                value: process.process_id,
                label: process.process_id,
              })),
            ]}
          />
        </Group>
        <TextInput
          label="Text Search"
          placeholder="Search topic/message/payload"
          value={textFilter}
          onChange={(event) => onTextFilterChange(event.currentTarget.value)}
        />
        <ScrollArea h="55vh" viewportRef={viewportRef}>
          <Stack gap={6}>
            {filteredRows.length === 0 && (
              <Text size="sm" c="dimmed">
                No log entries match the current filters.
              </Text>
            )}
            {filteredRows.map((entry, idx) => {
              const severity = String(entry.severity ?? "info").toLowerCase();
              const badgeColor =
                severity === "critical" || severity === "error"
                  ? "red"
                  : severity === "warning"
                    ? "yellow"
                    : "gray";
              const sourceKindColor = logSourceKindColor(entry.source_kind);
              const source = entry.source_id ?? entry.device_id ?? entry.process_id ?? "-";
              const sourceDevice =
                typeof source === "string" && source !== "-"
                  ? (deviceById.get(source) ?? null)
                  : null;
              const entryKey = logEntryKey(entry);
              const fullMessage = entry.message ?? "";
              const messageLines = fullMessage.split(/\r?\n/);
              const isLongMessage = messageLines.length > 4 || fullMessage.length > 320;
              const expanded = Boolean(expandedByKey[entryKey]);
              const visibleMessage =
                isLongMessage && !expanded
                  ? `${messageLines.slice(0, 4).join("\n")}\n...`
                  : fullMessage;
              return (
                <Card
                  key={`${entryKey}:${idx}`}
                  p="xs"
                  radius="sm"
                  style={{ border: "1px solid var(--card-border)" }}
                >
                  <Stack gap={4}>
                    <Group justify="space-between" align="flex-start" gap="xs">
                      <Group gap="xs" wrap="wrap">
                        <Text size="xs" c="dimmed">
                          {formatLogTime(entry)}
                        </Text>
                        <Badge size="xs" variant="light" color={badgeColor}>
                          {severity}
                        </Badge>
                        <Badge size="xs" variant="outline" color={sourceKindColor}>
                          {entry.source_kind ?? "manager"}
                        </Badge>
                        <Text size="xs" c="dimmed">
                          {sourceDevice ? (
                            <DeviceNameInline
                              deviceId={source}
                              device={sourceDevice}
                              size="xs"
                              c="dimmed"
                            />
                          ) : (
                            source
                          )}
                        </Text>
                      </Group>
                      <Group gap="xs">
                        <Text size="xs" c="dimmed">
                          {entry.topic ?? "-"}
                        </Text>
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          color="gray"
                          disabled={!fullMessage}
                          onClick={() => onCopyMessage(fullMessage)}
                        >
                          Copy message
                        </Button>
                      </Group>
                    </Group>
                    <Text
                      size="sm"
                      style={{
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                      }}
                    >
                      {visibleMessage}
                    </Text>
                    {isLongMessage && (
                      <Group justify="flex-end">
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          color="gray"
                          onClick={() => onToggleExpanded(entryKey)}
                        >
                          {expanded ? "Hide" : "Show more"}
                        </Button>
                      </Group>
                    )}
                    {entry.payload_json && entry.payload_json.length > 0 && (
                      <details>
                        <summary>
                          <Text span size="xs" c="dimmed">
                            Payload
                          </Text>
                        </summary>
                        <Text
                          size="xs"
                          style={{
                            whiteSpace: "pre-wrap",
                            wordBreak: "break-word",
                            marginTop: 4,
                          }}
                        >
                          {entry.payload_json}
                        </Text>
                      </details>
                    )}
                  </Stack>
                </Card>
              );
            })}
          </Stack>
        </ScrollArea>
      </Stack>
    </Modal>
  );
}
