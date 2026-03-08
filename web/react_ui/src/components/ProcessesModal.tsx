import {
  Badge,
  Button,
  Card,
  Group,
  Modal,
  Select,
  Stack,
  Text,
} from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { processStateColor } from "../features/runtime/helpers";
import type { CapabilityMember, ProcessStatus } from "../types";

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

  useEffect(() => {
    setSelectedActionByProcess((prev) => {
      let changed = false;
      const next: Record<string, string> = {};
      for (const process of processes) {
        const processId = process.process_id;
        const commands = (capabilitiesByProcess[processId] ?? [])
          .map((capability) => capability.name)
          .sort((a, b) => a.localeCompare(b));
        if (commands.length === 0) {
          if (Object.prototype.hasOwnProperty.call(prev, processId)) {
            changed = true;
          }
          continue;
        }
        const previous = prev[processId];
        const chosen =
          previous && commands.includes(previous) ? previous : commands[0];
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
          const commands = (capabilitiesByProcess[processId] ?? [])
            .map((capability) => capability.name)
            .sort((a, b) => a.localeCompare(b));
          const selectedAction =
            selectedActionByProcess[processId] ?? commands[0] ?? "";
          const busy = Boolean(busyByProcess[processId]);
          const error = errorByProcess[processId];
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
                      <Text fw={600}>{processId}</Text>
                      <Badge variant="light" color={processStateColor(process.state)}>
                        {process.state}
                      </Badge>
                    </Group>
                    <Text size="xs" c="dimmed">
                      pid {process.pid ?? "n/a"} | hb age{" "}
                      {process.hb_age_s != null
                        ? `${process.hb_age_s.toFixed(2)} s`
                        : "n/a"}
                    </Text>
                  </Stack>
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
                </Group>
                {commands.length > 0 ? (
                  <Group align="flex-end" wrap="nowrap">
                    <Select
                      size="xs"
                      label="Action"
                      value={selectedAction || null}
                      data={commands.map((name) => ({ value: name, label: name }))}
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
                      disabled={busy || !selectedAction}
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
