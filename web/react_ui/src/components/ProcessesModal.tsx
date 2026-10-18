import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Card,
  Group,
  Modal,
  ScrollArea,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Tooltip,
  UnstyledButton,
  useComputedColorScheme,
} from "@mantine/core";
import {
  IconPlayerPlay,
  IconPlayerStop,
  IconRefresh,
  IconTerminal2,
  IconTopologyStar3,
  IconX,
} from "@tabler/icons-react";
import { useMemo, useState, type ReactNode } from "react";

import {
  formatAge,
  formatBytes,
  isRemoteProcess,
  processAdapter,
  processHealth,
  resourceHealthRank,
  stateName,
} from "../features/devices/health";
import { useResourceListView } from "../features/devices/useDeviceListView";
import { livenessColor, processStateColor } from "../features/runtime/helpers";
import type { CapabilityMember, ProcessStatus } from "../types";
import { HealthMark } from "./HealthMark";
import { RefreshIconButton } from "./RefreshIconButton";
import { ResourceListToolbar } from "./ResourceListToolbar";

// A mirrored (federated) process action is allowed unless the server annotated it
// `federation_allowed === false`. Local processes leave it undefined → allowed.
function actionAllowed(member: CapabilityMember | undefined): boolean {
  return member?.federation_allowed !== false;
}

type Props = {
  opened: boolean;
  onClose: () => void;
  processes: ReadonlyArray<ProcessStatus>;
  capabilitiesByProcess: Record<string, CapabilityMember[]>;
  busyByProcess: Record<string, boolean>;
  errorByProcess: Record<string, string>;
  onRefresh: () => Promise<unknown> | void;
  onProcessAction: (
    processId: string,
    action: "start" | "stop" | "restart"
  ) => void;
  onOpenCommand: (processId: string, action?: string) => void;
};

type TableColumn = "process" | "health" | "state" | "pid" | "mem" | "hb" | "restarts";

const TABLE_COLUMNS: { key: TableColumn; label: string; detail?: boolean }[] = [
  { key: "process", label: "Process" },
  { key: "health", label: "Health" },
  { key: "state", label: "State" },
  // Hidden while a process is selected: the detail panel shows them instead.
  { key: "pid", label: "PID", detail: true },
  { key: "mem", label: "Memory", detail: true },
  { key: "hb", label: "HB age", detail: true },
  { key: "restarts", label: "Restarts", detail: true },
];

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function tableSortValue(process: ProcessStatus, column: TableColumn): string | number | null {
  switch (column) {
    case "process":
      return process.process_id.toLowerCase();
    case "health":
      return resourceHealthRank(process, processAdapter);
    case "state":
      return stateName(process.state);
    case "pid":
      return numberOrNull(process.pid);
    case "mem":
      return numberOrNull(process.rss_bytes);
    case "hb":
      return numberOrNull(process.hb_age_s);
    case "restarts":
      return numberOrNull(process.restart_count);
  }
}

function compareValues(a: string | number | null, b: string | number | null): number {
  if (typeof a === "string" && typeof b === "string") {
    return a.localeCompare(b);
  }
  const av = typeof a === "number" ? a : Number.POSITIVE_INFINITY;
  const bv = typeof b === "number" ? b : Number.POSITIVE_INFINITY;
  return av - bv;
}

/** Lifecycle availability from the supervisor state, like the device card's connect logic. */
function lifecycleAvailability(state: string) {
  const s = stateName(state);
  return {
    canStart: !(s === "RUNNING" || s === "STARTING" || s === "STOPPING"),
    canStop: !(s === "STOPPED" || s === "EXITED" || s === "STOPPING"),
  };
}

function ProcessName({
  process,
  remoteIconColor,
}: {
  process: ProcessStatus;
  remoteIconColor: string;
}) {
  const remote = isRemoteProcess(process);
  const peer = String(process.owner_peer_id ?? "").trim();
  return (
    <Group gap={4} wrap="nowrap">
      {remote ? (
        <Tooltip label={peer ? `Remote process (peer: ${peer})` : "Remote process"} withArrow>
          <span style={{ display: "inline-flex", lineHeight: 0, color: remoteIconColor }}>
            <IconTopologyStar3 size={13} stroke={1.8} />
          </span>
        </Tooltip>
      ) : null}
      <Text span size="xs" fw={600}>
        {process.process_id}
      </Text>
    </Group>
  );
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Stack gap={0}>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text size="sm" style={{ wordBreak: "break-word" }}>
        {children}
      </Text>
    </Stack>
  );
}

function ProcessDetail({
  process,
  capabilities,
  busy,
  capabilityError,
  remoteIconColor,
  onClose,
  onProcessAction,
  onOpenCommand,
}: {
  process: ProcessStatus;
  capabilities: CapabilityMember[];
  busy: boolean;
  capabilityError: string | undefined;
  remoteIconColor: string;
  onClose: () => void;
  onProcessAction: Props["onProcessAction"];
  onOpenCommand: Props["onOpenCommand"];
}) {
  const processId = process.process_id;
  const remote = isRemoteProcess(process);
  // Deliberately no default: the operator picks the action, so the Command
  // button is never pre-armed on something like process.stop.
  const [selectedAction, setSelectedAction] = useState<string | null>(null);
  const members = useMemo(
    () => [...capabilities].sort((a, b) => a.name.localeCompare(b.name)),
    [capabilities]
  );
  const selectedMember = members.find((m) => m.name === selectedAction);
  const selectedValid = Boolean(selectedMember) && actionAllowed(selectedMember);
  const { canStart, canStop } = lifecycleAvailability(process.state);
  const exitCode = numberOrNull(process.last_exit_code);

  return (
    <Card withBorder radius="lg" p="md">
      <Stack gap="sm">
        <Group justify="space-between" wrap="nowrap" align="flex-start">
          <Stack gap={4} style={{ minWidth: 0 }}>
            <Text fw={600} truncate>
              {processId}
            </Text>
            <Group gap={6}>
              <Badge size="sm" variant="light" color={processStateColor(process.state)}>
                {process.state}
              </Badge>
              {remote ? (
                <Badge size="sm" variant="light" color={livenessColor(process.liveness)}>
                  {process.liveness ?? "UNKNOWN"}
                </Badge>
              ) : null}
            </Group>
          </Stack>
          <ActionIcon
            size="sm"
            variant="subtle"
            color="gray"
            aria-label="Close process details"
            onClick={onClose}
          >
            <IconX size={14} />
          </ActionIcon>
        </Group>

        {/* Lifecycle is owner-only: the local manager doesn't supervise a
            mirrored process, so start/stop/restart are hidden for remotes. */}
        {remote ? (
          <Text size="xs" c="dimmed">
            Mirrored from {process.owner_peer_id || "a federation peer"}; lifecycle is
            managed by its owner.
          </Text>
        ) : (
          <Group gap="xs">
            <Button
              size="xs"
              variant="light"
              color="teal"
              leftSection={<IconPlayerPlay size={14} />}
              disabled={busy || !canStart}
              onClick={() => onProcessAction(processId, "start")}
            >
              Start
            </Button>
            <Button
              size="xs"
              variant="light"
              color="red"
              leftSection={<IconPlayerStop size={14} />}
              disabled={busy || !canStop}
              onClick={() => onProcessAction(processId, "stop")}
            >
              Stop
            </Button>
            <Button
              size="xs"
              variant="light"
              color="orange"
              leftSection={<IconRefresh size={14} />}
              disabled={busy}
              onClick={() => onProcessAction(processId, "restart")}
            >
              Restart
            </Button>
          </Group>
        )}

        <SimpleGrid cols={2} spacing="xs" verticalSpacing="xs">
          <DetailRow label="PID">{process.pid ?? "—"}</DetailRow>
          <DetailRow label="Memory">{formatBytes(process.rss_bytes)}</DetailRow>
          <DetailRow label="Heartbeat age">{formatAge(process.hb_age_s)}</DetailRow>
          <DetailRow label="Restart policy">{process.restart_policy || "—"}</DetailRow>
          <DetailRow label="Restarts">{numberOrNull(process.restart_count) ?? "—"}</DetailRow>
          <DetailRow label="Last exit code">{exitCode ?? "—"}</DetailRow>
        </SimpleGrid>
        {process.rpc_endpoint ? (
          <DetailRow label="RPC endpoint">{process.rpc_endpoint}</DetailRow>
        ) : null}
        {process.last_error ? (
          <Text size="xs" c="red" style={{ wordBreak: "break-word" }}>
            {process.last_error}
          </Text>
        ) : null}

        <Stack gap={6}>
          <Text size="xs" c="dimmed">
            Command
          </Text>
          {members.length > 0 ? (
            <Group gap="xs" wrap="nowrap" align="flex-end">
              <Select
                size="xs"
                placeholder="Choose an action…"
                aria-label="Process action"
                value={selectedAction}
                data={members.map((m) => ({
                  value: m.name,
                  label: actionAllowed(m) ? m.name : `${m.name} (not federated-allowed)`,
                  disabled: !actionAllowed(m),
                }))}
                searchable
                clearable
                flex={1}
                onChange={setSelectedAction}
              />
              <Button
                size="xs"
                variant="light"
                leftSection={<IconTerminal2 size={14} />}
                disabled={busy || !selectedValid}
                onClick={() => {
                  if (selectedAction) {
                    onOpenCommand(processId, selectedAction);
                  }
                }}
              >
                Command
              </Button>
            </Group>
          ) : (
            <Text size="xs" c="dimmed">
              {capabilityError ?? "No process commands available yet."}
            </Text>
          )}
        </Stack>
      </Stack>
    </Card>
  );
}

export function ProcessesModal({
  opened,
  onClose,
  processes,
  capabilitiesByProcess,
  busyByProcess,
  errorByProcess,
  onRefresh,
  onProcessAction,
  onOpenCommand,
}: Props) {
  const view = useResourceListView(processes, processAdapter, {
    persistKey: "ecui.processesModal",
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tableSort, setTableSort] = useState<{ column: TableColumn; reverse: boolean } | null>(
    null
  );
  const computedColorScheme = useComputedColorScheme("light");
  const remoteIconColor =
    computedColorScheme === "dark"
      ? "var(--mantine-color-blue-4)"
      : "var(--mantine-color-blue-6)";

  const selectedProcess = useMemo(
    () => processes.find((process) => process.process_id === selectedId) ?? null,
    [processes, selectedId]
  );
  const compactTable = selectedProcess !== null;
  const visibleColumns = compactTable
    ? TABLE_COLUMNS.filter((column) => !column.detail)
    : TABLE_COLUMNS;

  const rows = useMemo(() => {
    if (!tableSort) {
      return view.visible;
    }
    const { column, reverse } = tableSort;
    const sorted = [...view.visible].sort(
      (a, b) =>
        compareValues(tableSortValue(a, column), tableSortValue(b, column)) ||
        a.process_id.localeCompare(b.process_id)
    );
    return reverse ? sorted.reverse() : sorted;
  }, [tableSort, view.visible]);

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Processes"
      size="clamp(56rem, 92vw, 96rem)"
      centered
    >
      <Group gap="sm" align="flex-start" wrap="nowrap" mb="sm">
        <Box style={{ flex: 1, maxWidth: 640 }}>
          <ResourceListToolbar
            view={view}
            noun="processes"
            healthyLabel="running"
            customSortLabel="Default order"
          />
        </Box>
        <RefreshIconButton
          label="Refresh processes"
          onClick={() => {
            void onRefresh();
          }}
        />
      </Group>
      <Group gap="md" align="flex-start" wrap="nowrap">
        <ScrollArea.Autosize
          style={{ flex: 1, minWidth: 0 }}
          mah="min(70vh, 48rem)"
          type="auto"
          offsetScrollbars
        >
          {rows.length === 0 ? (
            <Text size="sm" c="dimmed" ta="center" py="xl">
              {processes.length === 0
                ? "No processes attached."
                : "No processes match the current filter."}
            </Text>
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
                {rows.map((process) => {
                  const health = processHealth(process);
                  const error = process.last_error ?? "";
                  return (
                    <Table.Tr
                      key={process.process_id}
                      className={`resource-overview-row${
                        process.process_id === selectedId
                          ? " resource-overview-row-selected"
                          : ""
                      }`}
                      onClick={() =>
                        setSelectedId((prev) =>
                          prev === process.process_id ? null : process.process_id
                        )
                      }
                    >
                      <Table.Td>
                        <ProcessName process={process} remoteIconColor={remoteIconColor} />
                      </Table.Td>
                      <Table.Td>
                        <HealthMark health={health} /> {health}
                      </Table.Td>
                      <Table.Td>
                        <Group gap={4} wrap="nowrap">
                          <Badge
                            size="xs"
                            variant="outline"
                            color={processStateColor(process.state)}
                          >
                            {process.state}
                          </Badge>
                          {isRemoteProcess(process) ? (
                            <Badge
                              size="xs"
                              variant="light"
                              color={livenessColor(process.liveness)}
                            >
                              {process.liveness ?? "?"}
                            </Badge>
                          ) : null}
                        </Group>
                      </Table.Td>
                      {compactTable ? null : (
                        <>
                          <Table.Td>{process.pid ?? "—"}</Table.Td>
                          <Table.Td>{formatBytes(process.rss_bytes)}</Table.Td>
                          <Table.Td>{formatAge(process.hb_age_s)}</Table.Td>
                          <Table.Td>{numberOrNull(process.restart_count) ?? "—"}</Table.Td>
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
          )}
        </ScrollArea.Autosize>
        {selectedProcess ? (
          <Box className="resource-overview-detail">
            <ScrollArea.Autosize mah="min(70vh, 48rem)" type="auto">
              <ProcessDetail
                key={selectedProcess.process_id}
                process={selectedProcess}
                capabilities={capabilitiesByProcess[selectedProcess.process_id] ?? []}
                busy={Boolean(busyByProcess[selectedProcess.process_id])}
                capabilityError={errorByProcess[selectedProcess.process_id]}
                remoteIconColor={remoteIconColor}
                onClose={() => setSelectedId(null)}
                onProcessAction={onProcessAction}
                onOpenCommand={onOpenCommand}
              />
            </ScrollArea.Autosize>
          </Box>
        ) : null}
      </Group>
    </Modal>
  );
}
