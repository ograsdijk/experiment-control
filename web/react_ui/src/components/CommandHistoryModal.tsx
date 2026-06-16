import {
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Modal,
  NumberInput,
  ScrollArea,
  SegmentedControl,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
} from "@mantine/core";
import { useState, type MutableRefObject } from "react";
import type { useCommandHistoryController } from "../features/commands/useCommandHistoryController";
import type { CommandHistoryEntry } from "../features/commands/types";
import { formatWallTimeSeconds } from "../features/logs/utils";
import type { DeviceStatus } from "../types";
import { DeviceNameInline } from "./DeviceNameInline";
import { JsonPreview } from "./JsonPreview";

type CommandHistoryControllerState = ReturnType<typeof useCommandHistoryController>;

type Props = {
  opened: boolean;
  onClose: () => void;
  controller: CommandHistoryControllerState;
  devices: DeviceStatus[];
  colorScheme: "light" | "dark";
  viewportRef: MutableRefObject<HTMLDivElement | null>;
  onCopyJson: (label: string, payload: unknown) => void;
};

function rowErrorMessage(error: Record<string, unknown> | null): string | null {
  if (!error || typeof error !== "object") {
    return null;
  }
  const message = String(error.message ?? "").trim();
  if (message) {
    return message;
  }
  const code = String(error.code ?? "").trim();
  return code || null;
}

const PARAM_PREVIEW_LIMIT = 5;

function formatParamPreviewValue(value: unknown): string {
  if (value == null) {
    return "null";
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? String(value) : "nan";
  }
  if (typeof value === "string") {
    return value.length > 28 ? `${value.slice(0, 28)}...` : value;
  }
  if (Array.isArray(value)) {
    return `[${value.length}]`;
  }
  if (typeof value === "object") {
    return "{...}";
  }
  return String(value);
}

function formatParamsDetailText(
  params: Record<string, unknown> | null,
  paramsJson: string
): string {
  if (params && typeof params === "object" && !Array.isArray(params)) {
    try {
      return JSON.stringify(params, null, 2);
    } catch {
      // fall back to raw JSON below
    }
  }
  const raw = String(paramsJson ?? "").trim();
  return raw || "{}";
}

function formatJsonDetailText(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return String(value ?? "{}");
  }
}

function buildLiveRequestCopyPayload(row: CommandHistoryEntry): Record<string, unknown> {
  return {
    id: row.id,
    ts_wall_s: row.ts_wall_s,
    target_kind: row.target_kind,
    target_id: row.target_id,
    action: row.action,
    params: row.params,
    source: row.source,
  };
}

export function CommandHistoryModal({
  opened,
  onClose,
  controller,
  devices,
  colorScheme,
  viewportRef,
  onCopyJson,
}: Props) {
  const [expandedJournalParamsById, setExpandedJournalParamsById] = useState<
    Record<number, boolean>
  >({});
  const [expandedRestoreParamsById, setExpandedRestoreParamsById] = useState<
    Record<number, boolean>
  >({});
  const [expandedLiveDetailsById, setExpandedLiveDetailsById] = useState<
    Record<string, boolean>
  >({});
  const deviceById = new Map(devices.map((device) => [device.device_id, device]));
  const mode = controller.commandHistoryMode;
  const restoreRunnableCount = controller.commandRestorePreviewRows.filter(
    (row) => row.include
  ).length;

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
          <SegmentedControl
            value={mode}
            onChange={(value) =>
              controller.setCommandHistoryMode(
                value === "journal" || value === "restore" ? value : "live"
              )
            }
            data={[
              { label: "Live", value: "live" },
              { label: "Journal", value: "journal" },
              { label: "Restore", value: "restore" },
            ]}
          />
          <Group gap="xs">
            <Switch
              size="sm"
              checked={controller.commandHistorySortNewestFirst}
              onChange={(event) =>
                controller.setCommandHistorySortNewestFirst(
                  event.currentTarget.checked
                )
              }
              label="Newest first"
            />
            {(mode === "journal" || mode === "restore") && (
              <Button
                size="xs"
                variant="light"
                color="gray"
                loading={controller.commandJournalLoading}
                onClick={() => {
                  void controller.refreshCommandJournal();
                }}
              >
                Refresh journal
              </Button>
            )}
          </Group>
        </Group>

        {(mode === "journal" || mode === "restore") && (
          <Card p="xs" radius="sm" style={{ border: "1px solid var(--card-border)" }}>
            <Group justify="space-between">
              <Stack gap={2}>
                <Group gap="xs">
                  <Badge
                    size="xs"
                    variant="light"
                    color={controller.commandJournalStatus?.enabled ? "teal" : "gray"}
                  >
                    journal {controller.commandJournalStatus?.enabled ? "enabled" : "disabled"}
                  </Badge>
                  <Badge
                    size="xs"
                    variant="outline"
                    color={controller.commandJournalStatusError ? "red" : "gray"}
                  >
                    {controller.commandJournalStatusError ? "status error" : "status ok"}
                  </Badge>
                  <Badge
                    size="xs"
                    variant="outline"
                    color={controller.commandJournalError ? "red" : "gray"}
                  >
                    rows {controller.commandJournalRows.length}
                  </Badge>
                </Group>
                <Text size="xs" c="dimmed" style={{ wordBreak: "break-word" }}>
                  {controller.commandJournalStatus?.path ?? "No journal path available"}
                </Text>
                {controller.commandJournalStatusError && (
                  <Text size="xs" c="red">
                    {controller.commandJournalStatusError}
                  </Text>
                )}
                {controller.commandJournalError && (
                  <Text size="xs" c="red">
                    {controller.commandJournalError}
                  </Text>
                )}
              </Stack>
              <Stack gap={2} align="flex-end">
                <Text size="xs" c="dimmed">
                  written: {controller.commandJournalStatus?.written ?? 0} | dropped:{" "}
                  {controller.commandJournalStatus?.dropped ?? 0}
                </Text>
                <Text size="xs" c="dimmed">
                  queue: {controller.commandJournalStatus?.queue_depth ?? 0}/
                  {controller.commandJournalStatus?.queue_max ?? 0}
                </Text>
              </Stack>
            </Group>
          </Card>
        )}

        {mode === "live" && (
          <>
            <Group justify="space-between">
              <Text size="xs" c="dimmed">
                {controller.filteredCommandHistoryRows.length} shown /{" "}
                {controller.commandHistoryRows.length} stored
              </Text>
              <Group gap="xs" align="flex-end">
                <NumberInput
                  size="xs"
                  label="Persist N"
                  min={20}
                  max={2000}
                  step={10}
                  value={controller.commandHistoryLimit}
                  onChange={(value) => {
                    const numeric = typeof value === "number" ? value : Number(value);
                    if (Number.isFinite(numeric)) {
                      controller.setCommandHistoryLimit(numeric);
                    }
                  }}
                  styles={{ input: { width: 120 } }}
                />
                <Switch
                  size="sm"
                  checked={controller.commandHistoryAutoScroll}
                  onChange={(event) =>
                    controller.setCommandHistoryAutoScroll(event.currentTarget.checked)
                  }
                  label="Auto-scroll"
                />
                <Button
                  size="xs"
                  variant="light"
                  color="red"
                  onClick={() => controller.setCommandHistoryRows([])}
                >
                  Clear
                </Button>
              </Group>
            </Group>
            <Group grow align="flex-end">
              <Select
                label="Target"
                comboboxProps={{ zIndex: 500 }}
                value={controller.commandHistoryTargetFilter}
                onChange={(value) => controller.setCommandHistoryTargetFilter(value ?? "all")}
                data={[
                  { value: "all", label: "All targets" },
                  { value: "device", label: "Device" },
                  { value: "process", label: "Process" },
                ]}
              />
              <Select
                label="Status"
                comboboxProps={{ zIndex: 500 }}
                value={controller.commandHistoryStatusFilter}
                onChange={(value) => controller.setCommandHistoryStatusFilter(value ?? "all")}
                data={[
                  { value: "all", label: "All statuses" },
                  { value: "ok", label: "OK" },
                  { value: "error", label: "Error" },
                ]}
              />
              <Select
                label="Source"
                comboboxProps={{ zIndex: 500 }}
                value={controller.commandHistorySourceFilter}
                onChange={(value) => controller.setCommandHistorySourceFilter(value ?? "all")}
                data={[
                  { value: "all", label: "All sources" },
                  ...controller.commandHistorySourceOptions.map((source) => ({
                    value: source,
                    label: source,
                  })),
                ]}
              />
            </Group>
            <TextInput
              label="Text Search"
              placeholder="Search target, action, params, and replies"
              value={controller.commandHistoryTextFilter}
              onChange={(event) =>
                controller.setCommandHistoryTextFilter(event.currentTarget.value)
              }
            />
            <ScrollArea h="55vh" viewportRef={viewportRef}>
              <Stack gap={6}>
                {controller.filteredCommandHistoryRows.length === 0 && (
                  <Text size="sm" c="dimmed">
                    No command entries match the current filters.
                  </Text>
                )}
                {controller.filteredCommandHistoryRows.map((row) => {
                  const ok = row.response.ok === true;
                  const errorMessage =
                    row.response.error?.message ?? row.response.error?.code;
                  const expanded = expandedLiveDetailsById[row.id] === true;
                  const requestPayload = buildLiveRequestCopyPayload(row);
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
                              <Badge
                                size="xs"
                                variant="outline"
                                color={row.target_kind === "device" ? "orange" : "violet"}
                              >
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
                              onClick={() =>
                                setExpandedLiveDetailsById((prev) => ({
                                  ...prev,
                                  [row.id]: !expanded,
                                }))
                              }
                            >
                              {expanded ? "Hide details" : "Show details"}
                            </Button>
                            <Button
                              size="compact-xs"
                              variant="subtle"
                              color="gray"
                              onClick={() =>
                                onCopyJson("Request JSON", requestPayload)
                              }
                            >
                              Copy request
                            </Button>
                            <Button
                              size="compact-xs"
                              variant="subtle"
                              color="gray"
                              onClick={() => onCopyJson("Reply JSON", row.response)}
                            >
                              Copy reply
                            </Button>
                          </Group>
                        </Group>
                        {expanded && (
                          <Stack gap={4}>
                            <Text size="xs" c="dimmed">
                              Request
                            </Text>
                            <JsonPreview
                              text={formatJsonDetailText(requestPayload)}
                              colorScheme={colorScheme}
                            />
                            <Text size="xs" c="dimmed">
                              Reply
                            </Text>
                            <JsonPreview
                              text={formatJsonDetailText(row.response)}
                              colorScheme={colorScheme}
                            />
                          </Stack>
                        )}
                      </Stack>
                    </Card>
                  );
                })}
              </Stack>
            </ScrollArea>
          </>
        )}

        {mode === "journal" && (
          <>
            <Group justify="space-between">
              <Text size="xs" c="dimmed">
                {controller.filteredCommandJournalRows.length} shown /{" "}
                {controller.commandJournalTotalMatched} matched
              </Text>
              <Group gap="xs" align="flex-end">
                <NumberInput
                  size="xs"
                  label="Fetch N"
                  min={20}
                  max={5000}
                  step={20}
                  value={controller.commandJournalLimit}
                  onChange={(value) => {
                    const numeric = typeof value === "number" ? value : Number(value);
                    if (Number.isFinite(numeric)) {
                      controller.setCommandJournalLimit(numeric);
                    }
                  }}
                  styles={{ input: { width: 120 } }}
                />
                <Switch
                  size="sm"
                  checked={controller.commandJournalLastPerDeviceOnly}
                  onChange={(event) =>
                    controller.setCommandJournalLastPerDeviceOnly(
                      event.currentTarget.checked
                    )
                  }
                  label="Last per target/action"
                />
                <Button size="xs" variant="light" onClick={controller.selectAllFilteredCommandJournal}>
                  Select shown
                </Button>
                <Button size="xs" variant="light" onClick={controller.clearCommandJournalSelection}>
                  Clear selection
                </Button>
              </Group>
            </Group>
            <Group grow align="flex-end">
              <Select
                label="Target"
                comboboxProps={{ zIndex: 500 }}
                value={controller.commandJournalTargetFilter}
                onChange={(value) => controller.setCommandJournalTargetFilter(value ?? "all")}
                data={[
                  { value: "all", label: "All targets" },
                  { value: "device", label: "Device" },
                  { value: "process", label: "Process" },
                ]}
              />
              <Select
                label="Status"
                comboboxProps={{ zIndex: 500 }}
                value={controller.commandJournalStatusFilter}
                onChange={(value) => controller.setCommandJournalStatusFilter(value ?? "all")}
                data={[
                  { value: "all", label: "All statuses" },
                  { value: "ok", label: "OK" },
                  { value: "error", label: "Error" },
                ]}
              />
              <Select
                label="Source"
                comboboxProps={{ zIndex: 500 }}
                value={controller.commandJournalSourceFilter}
                onChange={(value) => controller.setCommandJournalSourceFilter(value ?? "all")}
                data={[
                  { value: "all", label: "All sources" },
                  ...controller.commandJournalSourceOptions.map((source) => ({
                    value: source,
                    label: source,
                  })),
                ]}
              />
            </Group>
            <TextInput
              label="Text Search"
              placeholder="Search target, action, params JSON, and result JSON"
              value={controller.commandJournalTextFilter}
              onChange={(event) =>
                controller.setCommandJournalTextFilter(event.currentTarget.value)
              }
            />
            <Text size="xs" c="dimmed">
              Selected for restore: {controller.selectedCommandJournalRows.length}
            </Text>
            <ScrollArea h="50vh" viewportRef={viewportRef}>
              <Stack gap={6}>
                {controller.filteredCommandJournalRows.length === 0 && (
                  <Text size="sm" c="dimmed">
                    No command journal entries match the current filters.
                  </Text>
                )}
                {controller.filteredCommandJournalRows.map((row) => (
                  <Card
                    key={row.id}
                    p="xs"
                    radius="sm"
                    style={{ border: "1px solid var(--card-border)" }}
                  >
                    <Group justify="space-between" align="flex-start">
                      <Group gap="xs" align="flex-start">
                        <Checkbox
                          checked={controller.selectedCommandJournalIds[row.id] === true}
                          onChange={(event) =>
                            controller.toggleCommandJournalSelection(
                              row.id,
                              event.currentTarget.checked
                            )
                          }
                        />
                        <Stack gap={2}>
                          {(() => {
                            const paramsEntries = row.params
                              ? Object.entries(row.params)
                              : [];
                            const previewEntries = paramsEntries.slice(
                              0,
                              PARAM_PREVIEW_LIMIT
                            );
                            const hasMore =
                              paramsEntries.length > previewEntries.length;
                            const hasRawParams =
                              String(row.params_json ?? "").trim().length > 0;
                            const expanded =
                              expandedJournalParamsById[row.id] === true;
                            return (
                              <>
                          <Group gap="xs" wrap="wrap">
                            <Text size="xs" c="dimmed">
                              {formatWallTimeSeconds(row.ts_wall_s)}
                            </Text>
                            <Badge size="xs" variant="light" color={row.ok ? "teal" : "red"}>
                              {row.ok ? "ok" : "error"}
                            </Badge>
                            <Badge
                              size="xs"
                              variant="outline"
                              color={row.target_kind === "device" ? "orange" : "violet"}
                            >
                              {row.target_kind}
                            </Badge>
                            <Badge size="xs" variant="outline" color="gray">
                              {row.source}
                            </Badge>
                            <Badge size="xs" variant="outline" color="gray">
                              id {row.id}
                            </Badge>
                          </Group>
                          <Text size="sm" fw={600}>
                            {row.target_id}.{row.action}
                          </Text>
                          <Group gap={4} wrap="wrap">
                            <Text size="xs" c="dimmed">
                              params
                            </Text>
                            {previewEntries.length > 0 ? (
                              previewEntries.map(([key, value]) => (
                                <Badge
                                  key={`${row.id}-${key}`}
                                  size="xs"
                                  variant="outline"
                                  color="gray"
                                >
                                  {key}={formatParamPreviewValue(value)}
                                </Badge>
                              ))
                            ) : (
                              <Badge size="xs" variant="outline" color="gray">
                                none
                              </Badge>
                            )}
                            {hasMore && (
                              <Badge size="xs" variant="outline" color="gray">
                                +{paramsEntries.length - previewEntries.length} more
                              </Badge>
                            )}
                            {hasRawParams && (
                              <Button
                                size="compact-xs"
                                variant="subtle"
                                color="gray"
                                onClick={() =>
                                  setExpandedJournalParamsById((prev) => ({
                                    ...prev,
                                    [row.id]: !expanded,
                                  }))
                                }
                              >
                                {expanded ? "Hide params" : "Show params"}
                              </Button>
                            )}
                          </Group>
                          {hasRawParams && expanded && (
                            <JsonPreview
                              text={formatParamsDetailText(row.params, row.params_json)}
                              colorScheme={colorScheme}
                            />
                          )}
                          {rowErrorMessage(row.error) && (
                            <Text size="xs" c="red">
                              {rowErrorMessage(row.error)}
                            </Text>
                          )}
                          {row.params_parse_error && (
                            <Text size="xs" c="yellow">
                              params_json parse error: {row.params_parse_error}
                            </Text>
                          )}
                              </>
                            );
                          })()}
                        </Stack>
                      </Group>
                      <Group gap="xs">
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          color="gray"
                          onClick={() => onCopyJson("Journal row JSON", row)}
                        >
                          Copy row
                        </Button>
                      </Group>
                    </Group>
                  </Card>
                ))}
              </Stack>
            </ScrollArea>
          </>
        )}

        {mode === "restore" && (
          <>
            <Group justify="space-between" align="flex-end">
              <Stack gap={2}>
                <Text size="xs" c="dimmed">
                  Selected journal rows: {controller.selectedCommandJournalRows.length}
                </Text>
                <Text size="xs" c="dimmed">
                  Runnable after safety filters: {restoreRunnableCount}
                </Text>
              </Stack>
              <Button
                size="xs"
                color="teal"
                loading={controller.commandRestoreBusy}
                disabled={restoreRunnableCount <= 0}
                onClick={() => {
                  void controller.executeCommandRestore();
                }}
              >
                Restore selected
              </Button>
            </Group>
            <Group gap="md">
              <Switch
                size="sm"
                checked={controller.commandRestoreIncludeFailed}
                onChange={(event) =>
                  controller.setCommandRestoreIncludeFailed(event.currentTarget.checked)
                }
                label="Include failed rows"
              />
              <Switch
                size="sm"
                checked={controller.commandRestoreIncludeRemote}
                onChange={(event) =>
                  controller.setCommandRestoreIncludeRemote(event.currentTarget.checked)
                }
                label="Include remote targets"
              />
              <Switch
                size="sm"
                checked={controller.commandRestoreIncludeProcessControl}
                onChange={(event) =>
                  controller.setCommandRestoreIncludeProcessControl(
                    event.currentTarget.checked
                  )
                }
                label="Include process start/stop/restart"
              />
            </Group>
            {controller.commandRestoreLastReport && (
              <Text size="xs" c="dimmed">
                Last restore report: attempted {controller.commandRestoreLastReport.attempted},
                executed {controller.commandRestoreLastReport.executed}, ok{" "}
                {controller.commandRestoreLastReport.ok}, error{" "}
                {controller.commandRestoreLastReport.error}, skipped{" "}
                {controller.commandRestoreLastReport.skipped}
              </Text>
            )}
            <ScrollArea h="55vh" viewportRef={viewportRef}>
              <Stack gap={6}>
                {controller.commandRestorePreviewRows.length === 0 && (
                  <Text size="sm" c="dimmed">
                    Select rows in the Journal tab to build a restore plan.
                  </Text>
                )}
                {controller.commandRestorePreviewRows.map((row) => (
                  <Card
                    key={row.id}
                    p="xs"
                    radius="sm"
                    style={{ border: "1px solid var(--card-border)" }}
                  >
                    <Group justify="space-between" align="flex-start">
                      <Stack gap={2}>
                        {(() => {
                          const paramsEntries = row.params
                            ? Object.entries(row.params)
                            : [];
                          const previewEntries = paramsEntries.slice(
                            0,
                            PARAM_PREVIEW_LIMIT
                          );
                          const hasMore =
                            paramsEntries.length > previewEntries.length;
                          const hasRawParams =
                            String(row.params_json ?? "").trim().length > 0;
                          const expanded =
                            expandedRestoreParamsById[row.id] === true;
                          return (
                            <>
                        <Group gap="xs" wrap="wrap">
                          <Text size="xs" c="dimmed">
                            {formatWallTimeSeconds(row.ts_wall_s)}
                          </Text>
                          <Badge
                            size="xs"
                            variant="light"
                            color={row.include ? "teal" : "yellow"}
                          >
                            {row.include ? "will run" : "skipped"}
                          </Badge>
                          <Badge
                            size="xs"
                            variant="outline"
                            color={row.target_kind === "device" ? "orange" : "violet"}
                          >
                            {row.target_kind}
                          </Badge>
                        </Group>
                        <Text size="sm" fw={600}>
                          {row.target_id}.{row.action}
                        </Text>
                        <Group gap={4} wrap="wrap">
                          <Text size="xs" c="dimmed">
                            params
                          </Text>
                          {previewEntries.length > 0 ? (
                            previewEntries.map(([key, value]) => (
                              <Badge
                                key={`restore-${row.id}-${key}`}
                                size="xs"
                                variant="outline"
                                color="gray"
                              >
                                {key}={formatParamPreviewValue(value)}
                              </Badge>
                            ))
                          ) : (
                            <Badge size="xs" variant="outline" color="gray">
                              none
                            </Badge>
                          )}
                          {hasMore && (
                            <Badge size="xs" variant="outline" color="gray">
                              +{paramsEntries.length - previewEntries.length} more
                            </Badge>
                          )}
                          {hasRawParams && (
                            <Button
                              size="compact-xs"
                              variant="subtle"
                              color="gray"
                              onClick={() =>
                                setExpandedRestoreParamsById((prev) => ({
                                  ...prev,
                                  [row.id]: !expanded,
                                }))
                              }
                            >
                              {expanded ? "Hide params" : "Show params"}
                            </Button>
                          )}
                        </Group>
                        {hasRawParams && expanded && (
                          <JsonPreview
                            text={formatParamsDetailText(row.params, row.params_json)}
                            colorScheme={colorScheme}
                          />
                        )}
                        {row.skip_reason && (
                          <Text size="xs" c="yellow">
                            {row.skip_reason}
                          </Text>
                        )}
                            </>
                          );
                        })()}
                      </Stack>
                      <Button
                        size="compact-xs"
                        variant="subtle"
                        color="gray"
                        onClick={() => onCopyJson("Restore row JSON", row)}
                      >
                        Copy row
                      </Button>
                    </Group>
                  </Card>
                ))}
              </Stack>
            </ScrollArea>
          </>
        )}
      </Stack>
    </Modal>
  );
}
