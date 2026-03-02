import {
  Badge,
  Button,
  Card,
  Group,
  Modal,
  NumberInput,
  ScrollArea,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
} from "@mantine/core";
import type { MutableRefObject } from "react";
import { formatWallTimeSeconds, toPrettyJson } from "../features/logs/utils";
import type { CommandHistoryEntry } from "../features/commands/types";
import type { DeviceStatus } from "../types";
import { DeviceNameInline } from "./DeviceNameInline";

type Props = {
  opened: boolean;
  onClose: () => void;
  filteredRows: CommandHistoryEntry[];
  devices: DeviceStatus[];
  totalRows: number;
  persistLimit: number;
  persistLimitMin: number;
  persistLimitMax: number;
  onPersistLimitChange: (value: number) => void;
  autoScroll: boolean;
  onAutoScrollChange: (value: boolean) => void;
  onClear: () => void;
  targetFilter: string;
  onTargetFilterChange: (value: string) => void;
  statusFilter: string;
  onStatusFilterChange: (value: string) => void;
  sourceFilter: string;
  onSourceFilterChange: (value: string) => void;
  sourceOptions: string[];
  textFilter: string;
  onTextFilterChange: (value: string) => void;
  viewportRef: MutableRefObject<HTMLDivElement | null>;
  onCopyJson: (label: string, payload: unknown) => void;
};

export function CommandHistoryModal({
  opened,
  onClose,
  filteredRows,
  devices,
  totalRows,
  persistLimit,
  persistLimitMin,
  persistLimitMax,
  onPersistLimitChange,
  autoScroll,
  onAutoScrollChange,
  onClear,
  targetFilter,
  onTargetFilterChange,
  statusFilter,
  onStatusFilterChange,
  sourceFilter,
  onSourceFilterChange,
  sourceOptions,
  textFilter,
  onTextFilterChange,
  viewportRef,
  onCopyJson,
}: Props) {
  const deviceById = new Map(devices.map((device) => [device.device_id, device]));
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Commands & replies"
      size="clamp(56rem, 92vw, 96rem)"
      centered
      zIndex={435}
    >
      <Stack gap="sm">
        <Group justify="space-between">
          <Text size="xs" c="dimmed">
            {filteredRows.length} shown / {totalRows} stored
          </Text>
          <Group gap="xs" align="flex-end">
            <NumberInput
              size="xs"
              label="Persist N"
              min={persistLimitMin}
              max={persistLimitMax}
              step={10}
              value={persistLimit}
              onChange={(value) => {
                const numeric = typeof value === "number" ? value : Number(value);
                if (!Number.isFinite(numeric)) {
                  return;
                }
                onPersistLimitChange(numeric);
              }}
              styles={{ input: { width: 120 } }}
            />
            <Switch
              size="sm"
              checked={autoScroll}
              onChange={(event) => onAutoScrollChange(event.currentTarget.checked)}
              label="Auto-scroll"
            />
            <Button size="xs" variant="light" color="red" onClick={onClear}>
              Clear
            </Button>
          </Group>
        </Group>
        <Group grow align="flex-end">
          <Select
            label="Target"
            comboboxProps={{ zIndex: 500 }}
            value={targetFilter}
            onChange={(value) => onTargetFilterChange(value ?? "all")}
            data={[
              { value: "all", label: "All targets" },
              { value: "device", label: "Device" },
              { value: "process", label: "Process" },
            ]}
          />
          <Select
            label="Status"
            comboboxProps={{ zIndex: 500 }}
            value={statusFilter}
            onChange={(value) => onStatusFilterChange(value ?? "all")}
            data={[
              { value: "all", label: "All statuses" },
              { value: "ok", label: "OK" },
              { value: "error", label: "Error" },
            ]}
          />
          <Select
            label="Source"
            comboboxProps={{ zIndex: 500 }}
            value={sourceFilter}
            onChange={(value) => onSourceFilterChange(value ?? "all")}
            data={[
              { value: "all", label: "All sources" },
              ...sourceOptions.map((source) => ({
                value: source,
                label: source,
              })),
            ]}
          />
        </Group>
        <TextInput
          label="Text Search"
          placeholder="Search target, action, params, and replies"
          value={textFilter}
          onChange={(event) => onTextFilterChange(event.currentTarget.value)}
        />
        <ScrollArea h="55vh" viewportRef={viewportRef}>
          <Stack gap={6}>
            {filteredRows.length === 0 && (
              <Text size="sm" c="dimmed">
                No command entries match the current filters.
              </Text>
            )}
            {filteredRows.map((row) => {
              const ok = row.response.ok === true;
              const targetBadgeColor =
                row.target_kind === "device" ? "orange" : "violet";
              const requestPayload = {
                target_kind: row.target_kind,
                target_id: row.target_id,
                action: row.action,
                params: row.params,
                source: row.source,
                ts_wall_s: row.ts_wall_s,
              };
              const responsePayload = row.response;
              const errorMessage =
                row.response.error?.message ?? row.response.error?.code;
              return (
                <Card
                  key={row.id}
                  p="xs"
                  radius="sm"
                  style={{ border: "1px solid var(--card-border)" }}
                >
                  <Stack gap={4}>
                    <Group justify="space-between" align="flex-start" gap="xs">
                      <Stack gap={2}>
                        <Group gap="xs" wrap="wrap">
                          <Text size="xs" c="dimmed">
                            {formatWallTimeSeconds(row.ts_wall_s)}
                          </Text>
                          <Badge size="xs" variant="light" color={ok ? "teal" : "red"}>
                            {ok ? "ok" : "error"}
                          </Badge>
                          <Badge size="xs" variant="outline" color={targetBadgeColor}>
                            {row.target_kind}
                          </Badge>
                          <Badge size="xs" variant="outline" color="gray">
                            {row.source}
                          </Badge>
                        </Group>
                        <Text size="sm" fw={600}>
                          {row.target_kind === "device" ? (
                            <DeviceNameInline
                              deviceId={row.target_id}
                              device={deviceById.get(row.target_id) ?? null}
                              size="sm"
                              fw={600}
                              suffix={`.${row.action}`}
                            />
                          ) : (
                            `${row.target_id}.${row.action}`
                          )}
                        </Text>
                        {errorMessage && (
                          <Text size="xs" c="red">
                            {errorMessage}
                          </Text>
                        )}
                      </Stack>
                      <Group gap="xs">
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          color="gray"
                          onClick={() => onCopyJson("Request JSON", requestPayload)}
                        >
                          Copy request JSON
                        </Button>
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          color="gray"
                          onClick={() => onCopyJson("Reply JSON", responsePayload)}
                        >
                          Copy reply JSON
                        </Button>
                      </Group>
                    </Group>
                    <details>
                      <summary>
                        <Text span size="xs" c="dimmed">
                          Request
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
                        {toPrettyJson(requestPayload)}
                      </Text>
                    </details>
                    <details>
                      <summary>
                        <Text span size="xs" c="dimmed">
                          Reply
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
                        {toPrettyJson(responsePayload)}
                      </Text>
                    </details>
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
