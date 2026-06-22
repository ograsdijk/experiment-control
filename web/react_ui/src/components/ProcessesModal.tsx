import {
  Badge,
  Button,
  Card,
  Group,
  Modal,
  Select,
  Stack,
  Text,
  Tooltip,
  useComputedColorScheme,
} from "@mantine/core";
import { IconRefresh, IconTopologyStar3 } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import {
  livenessColor,
  processStateColor,
} from "../features/runtime/helpers";
import type { CapabilityMember, ProcessStatus } from "../types";

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

function formatMemoryLabel(bytes: number | null | undefined): string {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes <= 0) {
    return "n/a";
  }
  const mb = bytes / (1024 * 1024);
  if (mb >= 1024) {
    return `${(mb / 1024).toFixed(2)} GB`;
  }
  return `${mb.toFixed(1)} MB`;
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
  const [selectedActionByProcess, setSelectedActionByProcess] = useState<
    Record<string, string>
  >({});
  const computedColorScheme = useComputedColorScheme("light");
  const remoteIconColor =
    computedColorScheme === "dark"
      ? "var(--mantine-color-blue-4)"
      : "var(--mantine-color-blue-6)";

  useEffect(() => {
    setSelectedActionByProcess((prev) => {
      let changed = false;
      const next: Record<string, string> = {};
      for (const process of processes) {
        const processId = process.process_id;
        const sorted = [...(capabilitiesByProcess[processId] ?? [])].sort(
          (a, b) => a.name.localeCompare(b.name)
        );
        const commands = sorted.map((m) => m.name);
        if (commands.length === 0) {
          if (Object.prototype.hasOwnProperty.call(prev, processId)) {
            changed = true;
          }
          continue;
        }
        // Default to the first ALLOWED action so the Command button isn't
        // pre-armed on a denied (greyed) one for federated processes.
        const firstAllowed =
          sorted.find((m) => actionAllowed(m))?.name ?? commands[0];
        const previous = prev[processId];
        const previousMember = sorted.find((m) => m.name === previous);
        const chosen =
          previous && commands.includes(previous) && actionAllowed(previousMember)
            ? previous
            : firstAllowed;
        next[processId] = chosen;
        if (previous !== chosen) {
          changed = true;
        }
      }
      if (!changed && Object.keys(prev).length !== Object.keys(next).length) {
        changed = true;
      }
      return changed ? next : prev;
    });
  }, [processes, capabilitiesByProcess]);

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Processes"
      size="clamp(56rem, 92vw, 96rem)"
      centered
      zIndex={400}
    >
      <Stack gap="md">
        <Group justify="space-between">
          <Text size="sm" c="dimmed">
            {processes.length} attached process{processes.length === 1 ? "" : "es"}
          </Text>
          <Button
            size="xs"
            variant="light"
            leftSection={<IconRefresh size={14} />}
            onClick={() => {
              void onRefresh();
            }}
          >
            Refresh
          </Button>
        </Group>
        {processes.length === 0 && (
          <Text size="sm" c="dimmed">
            No processes attached.
          </Text>
        )}
        {processes.map((process) => {
          const processId = process.process_id;
          const sortedMembers = [...(capabilitiesByProcess[processId] ?? [])].sort(
            (a, b) => a.name.localeCompare(b.name)
          );
          const commands = sortedMembers.map((m) => m.name);
          const allowedByName: Record<string, boolean> = {};
          for (const m of sortedMembers) {
            allowedByName[m.name] = actionAllowed(m);
          }
          const selectedAction =
            selectedActionByProcess[processId] ??
            sortedMembers.find((m) => actionAllowed(m))?.name ??
            commands[0] ??
            "";
          const selectedAllowed = selectedAction
            ? allowedByName[selectedAction] !== false
            : false;
          const busy = Boolean(busyByProcess[processId]);
          const error = errorByProcess[processId];
          const isRemote =
            Boolean(process.is_remote) || process.source_kind === "federated";
          const remotePeerId = String(process.owner_peer_id ?? "").trim();
          const remoteTooltip = remotePeerId
            ? `Remote process (peer: ${remotePeerId})`
            : "Remote process";
          return (
            <Card
              key={processId}
              radius="md"
              p="sm"
              style={{ border: "1px solid var(--card-border)" }}
            >
              <Stack gap="xs">
                <Group justify="space-between" align="flex-start">
                  <Stack gap={2}>
                    <Group gap="xs">
                      {isRemote ? (
                        <Tooltip label={remoteTooltip} withArrow>
                          <span
                            style={{
                              display: "inline-flex",
                              verticalAlign: "text-bottom",
                              lineHeight: 0,
                              color: remoteIconColor,
                            }}
                          >
                            <IconTopologyStar3 size={14} stroke={1.8} />
                          </span>
                        </Tooltip>
                      ) : null}
                      <Text fw={600}>{processId}</Text>
                      <Badge variant="light" color={processStateColor(process.state)}>
                        {process.state}
                      </Badge>
                      {isRemote ? (
                        <Badge
                          variant="light"
                          color={livenessColor(process.liveness)}
                        >
                          {process.liveness ?? "UNKNOWN"}
                        </Badge>
                      ) : null}
                    </Group>
                    <Text size="xs" c="dimmed">
                      pid {process.pid ?? "n/a"} | mem{" "}
                      {formatMemoryLabel(process.rss_bytes ?? null)} | hb age{" "}
                      {process.hb_age_s != null
                        ? `${process.hb_age_s.toFixed(2)} s`
                        : "n/a"}
                    </Text>
                  </Stack>
                  {/* Lifecycle is owner-only: the local manager doesn't supervise
                      a mirrored process, so hide start/stop/restart for remotes. */}
                  {!isRemote ? (
                    <Group gap="xs">
                      <Button
                        size="xs"
                        variant="light"
                        onClick={() => onProcessAction(processId, "start")}
                        disabled={busy}
                      >
                        Start
                      </Button>
                      <Button
                        size="xs"
                        variant="light"
                        color="red"
                        onClick={() => onProcessAction(processId, "stop")}
                        disabled={busy}
                      >
                        Stop
                      </Button>
                      <Button
                        size="xs"
                        variant="light"
                        color="red"
                        leftSection={<IconRefresh size={14} />}
                        onClick={() => onProcessAction(processId, "restart")}
                        disabled={busy}
                      >
                        Restart
                      </Button>
                    </Group>
                  ) : null}
                </Group>
                {commands.length > 0 ? (
                  <Group align="flex-end" wrap="nowrap">
                    <Select
                      size="xs"
                      label="Action"
                      value={selectedAction || null}
                      data={sortedMembers.map((m) => ({
                        value: m.name,
                        label: allowedByName[m.name]
                          ? m.name
                          : `${m.name} (not federated-allowed)`,
                        disabled: !allowedByName[m.name],
                      }))}
                      searchable
                      allowDeselect={false}
                      flex={1}
                      onChange={(value) =>
                        setSelectedActionByProcess((prev) => ({
                          ...prev,
                          [processId]: value ?? "",
                        }))
                      }
                    />
                    <Button
                      size="xs"
                      variant="light"
                      onClick={() => onOpenCommand(processId, selectedAction)}
                      disabled={busy || !selectedAction || !selectedAllowed}
                    >
                      Command
                    </Button>
                  </Group>
                ) : (
                  <Text size="xs" c="dimmed">
                    {error ?? "No process commands available yet."}
                  </Text>
                )}
                {process.last_error && (
                  <Text size="xs" c="red">
                    {process.last_error}
                  </Text>
                )}
              </Stack>
            </Card>
          );
        })}
      </Stack>
    </Modal>
  );
}
