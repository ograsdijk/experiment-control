import {
  ActionIcon,
  AppShell,
  Badge,
  Button,
  Divider,
  Group,
  Popover,
  SegmentedControl,
  Stack,
  Text,
  UnstyledButton,
} from "@mantine/core";
import {
  IconChevronDown,
  IconCpu,
  IconDatabase,
  IconFileText,
  IconPencil,
  IconRefresh,
  IconSettings,
  IconShieldCheck,
  IconTerminal2,
} from "@tabler/icons-react";
import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";

type InstanceRuntimeStatus = {
  instance_id: string;
  started_ts?: {
    t_wall?: number;
    t_mono?: number;
  } | null;
  manager_pid?: number | null;
  manager_reachable?: boolean;
  lock_effective_status?: string;
  lock_effective_help?: string;
  lock_status?: Record<string, unknown> | null;
  last_orphan_cleanup?: Record<string, unknown> | null;
};

function normalizeLockEffectiveStatus(value: unknown): string {
  if (typeof value !== "string") {
    return "unknown";
  }
  const status = value.trim().toLowerCase();
  if (status === "active") {
    return "active";
  }
  if (status === "running_unlocked") {
    return "running_unlocked";
  }
  if (status === "stale") {
    return "stale";
  }
  if (status === "missing" || status === "invalid") {
    return "missing";
  }
  return "unknown";
}

function lockStatusHelpText(status: string): string {
  if (status === "active") {
    return "Lock is held by the running manager process.";
  }
  if (status === "running_unlocked") {
    return "Manager is reachable, but no active instance lock is held.";
  }
  if (status === "stale") {
    return "Lock file exists, but its owner process is not alive.";
  }
  if (status === "missing") {
    return "No lock file exists for this instance.";
  }
  return "Lock status is unknown.";
}

type CleanupSummary = {
  matched: number;
  terminated: number;
  failed: number;
  dryRun: boolean;
  candidates: number[];
};

function parseCleanupSummary(raw: Record<string, unknown> | null): CleanupSummary | null {
  if (!raw) {
    return null;
  }
  const matchedRaw = raw.matched;
  const matched =
    typeof matchedRaw === "number" && Number.isFinite(matchedRaw)
      ? Math.trunc(matchedRaw)
      : 0;
  const terminated = Array.isArray(raw.terminated) ? raw.terminated.length : 0;
  const failed = Array.isArray(raw.failed) ? raw.failed.length : 0;
  const dryRun = raw.dry_run === true;
  const candidatesRaw = Array.isArray(raw.candidates) ? raw.candidates : [];
  const candidates = candidatesRaw
    .map((value) => (typeof value === "number" && Number.isFinite(value) ? Math.trunc(value) : NaN))
    .filter((value) => Number.isFinite(value));
  return { matched, terminated, failed, dryRun, candidates };
}

function formatWallTime24h(epochSeconds: number | null): string {
  if (epochSeconds === null) {
    return "n/a";
  }
  const value = Number(epochSeconds);
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  try {
    const dt = new Date(value * 1000);
    return dt.toLocaleString(undefined, {
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

type Props = {
  instanceLabel: string;
  showHdfWriter: boolean;
  hdfWriterChipColor: string;
  hdfWriterLoading: boolean;
  onOpenHdfWriter: () => Promise<unknown> | void;
  hdfWriterTitle: string;
  hdfWriterState: string;
  hdfWriterFileLabel: string;
  showHdfNoteChiplet: boolean;
  hdfMeasurementSchemaLoading: boolean;
  hdfCommandsBlocked: boolean;
  hdfMeasurementNoteBusy: boolean;
  onOpenHdfMeasurementNote: () => Promise<unknown> | void;
  hdfMeasurementSchemaDisplayError: string | null;
  hdfMeasurementNotesRows: number;
  showInfluxWriter: boolean;
  influxWriterChipColor: string;
  influxWriterLoading: boolean;
  onOpenInfluxWriter: () => Promise<unknown> | void;
  influxWriterTitle: string;
  influxWriterLabel: string;
  showSequencer: boolean;
  sequencerChipColor: string;
  sequencerStatusLoading: boolean;
  onOpenSequencer: () => Promise<unknown> | void;
  sequencerStatusError: string | null;
  sequencerChipTooltip: string;
  sequencerRuntimeState: string;
  sequencerChipSuffix: string;
  sequencerChipProgressStyle: CSSProperties | undefined;
  sequencerPrimaryAction: "start" | "pause" | "resume";
  sequencerPrimaryLabel: string;
  sequencerPrimaryDisabled: boolean;
  sequencerActionBusy: boolean;
  sequencerPrimaryIcon: ReactNode;
  onRunSequencerPrimaryAction: () => Promise<unknown> | void;
  sequencerLoaded: boolean;
  onOpenProcesses: () => Promise<unknown> | void;
  stateMachineButtonSummary: {
    color: string;
    tooltip: string;
    label: string;
  };
  onOpenStateMachines: () => void;
  interlockButtonSummary: {
    color: string;
    tooltip: string;
    label: string;
  };
  onOpenInterlocks: () => void;
  showDaqUi: boolean;
  onOpenDaq: () => Promise<unknown> | void;
  daqWorkspaceCount: number;
  commandUnreadError: boolean;
  onOpenCommandHistory: () => void;
  commandHistoryCount: number;
  logsUnreadError: boolean;
  onOpenLogs: () => void;
  onOpenSettings: () => void;
  onRefreshStatus: () => Promise<unknown> | void;
  colorScheme: "light" | "dark" | "auto";
  onColorSchemeChange: (value: "light" | "dark" | "auto") => void;
  telemetryBadgeColor: string;
  telemetryBadgeLabel: string;
  instanceRuntimeStatus: InstanceRuntimeStatus | null;
  instanceRuntimeLoading: boolean;
  instanceRuntimeError: string | null;
  onRefreshInstanceRuntimeStatus: () => Promise<unknown> | void;
  instanceCleanupBusy: boolean;
  onRunInstanceCleanupDryRun: () => Promise<Record<string, unknown> | null> | null;
  onRunInstanceCleanupApply: () => Promise<Record<string, unknown> | null> | null;
};

export function DashboardHeaderBar({
  instanceLabel,
  showHdfWriter,
  hdfWriterChipColor,
  hdfWriterLoading,
  onOpenHdfWriter,
  hdfWriterTitle,
  hdfWriterState,
  hdfWriterFileLabel,
  showHdfNoteChiplet,
  hdfMeasurementSchemaLoading,
  hdfCommandsBlocked,
  hdfMeasurementNoteBusy,
  onOpenHdfMeasurementNote,
  hdfMeasurementSchemaDisplayError,
  hdfMeasurementNotesRows,
  showInfluxWriter,
  influxWriterChipColor,
  influxWriterLoading,
  onOpenInfluxWriter,
  influxWriterTitle,
  influxWriterLabel,
  showSequencer,
  sequencerChipColor,
  sequencerStatusLoading,
  onOpenSequencer,
  sequencerStatusError,
  sequencerChipTooltip,
  sequencerRuntimeState,
  sequencerChipSuffix,
  sequencerChipProgressStyle,
  sequencerPrimaryAction,
  sequencerPrimaryLabel,
  sequencerPrimaryDisabled,
  sequencerActionBusy,
  sequencerPrimaryIcon,
  onRunSequencerPrimaryAction,
  sequencerLoaded,
  onOpenProcesses,
  stateMachineButtonSummary,
  onOpenStateMachines,
  interlockButtonSummary,
  onOpenInterlocks,
  showDaqUi,
  onOpenDaq,
  daqWorkspaceCount,
  commandUnreadError,
  onOpenCommandHistory,
  commandHistoryCount,
  logsUnreadError,
  onOpenLogs,
  onOpenSettings,
  onRefreshStatus,
  colorScheme,
  onColorSchemeChange,
  telemetryBadgeColor,
  telemetryBadgeLabel,
  instanceRuntimeStatus,
  instanceRuntimeLoading,
  instanceRuntimeError,
  onRefreshInstanceRuntimeStatus,
  instanceCleanupBusy,
  onRunInstanceCleanupDryRun,
  onRunInstanceCleanupApply,
}: Props) {
  const [instancePopoverOpen, setInstancePopoverOpen] = useState(false);
  const [cleanupPreview, setCleanupPreview] = useState<CleanupSummary | null>(null);
  useEffect(() => {
    if (!instancePopoverOpen) {
      return;
    }
    const timer = window.setInterval(() => {
      if (instanceRuntimeLoading || instanceCleanupBusy) {
        return;
      }
      void onRefreshInstanceRuntimeStatus();
    }, 3000);
    return () => {
      window.clearInterval(timer);
    };
  }, [
    instancePopoverOpen,
    instanceRuntimeLoading,
    instanceCleanupBusy,
    onRefreshInstanceRuntimeStatus,
  ]);

  const lockStatus = useMemo(() => {
    const lock =
      instanceRuntimeStatus?.lock_status &&
      typeof instanceRuntimeStatus.lock_status === "object"
        ? instanceRuntimeStatus.lock_status
        : null;
    const effectiveRaw = instanceRuntimeStatus?.lock_effective_status;
    const statusRaw = lock?.status;
    const statusCandidate = normalizeLockEffectiveStatus(
      typeof effectiveRaw === "string" && effectiveRaw.trim().length > 0
        ? effectiveRaw
        : statusRaw
    );
    const status =
      statusCandidate === "unknown" && instanceRuntimeLoading && instanceRuntimeStatus === null
        ? "loading"
        : statusCandidate;
    const color =
      status === "active"
        ? "teal"
        : status === "running_unlocked"
          ? "blue"
          : status === "loading"
            ? "gray"
        : status === "stale"
          ? "yellow"
          : status === "missing"
            ? "gray"
            : "red";
    const ownerPidRaw = lock?.owner_pid;
    const ownerPid =
      typeof ownerPidRaw === "number" && Number.isFinite(ownerPidRaw)
        ? Math.trunc(ownerPidRaw)
        : null;
    const acquiredRaw = lock?.acquired_wall_s;
    const acquiredWallS =
      typeof acquiredRaw === "number" && Number.isFinite(acquiredRaw)
        ? acquiredRaw
        : null;
    const managerRpcRaw = lock?.manager_rpc;
    const managerRpc =
      typeof managerRpcRaw === "string" && managerRpcRaw.trim().length > 0
        ? managerRpcRaw.trim()
        : null;
    const helpFromApiRaw = instanceRuntimeStatus?.lock_effective_help;
    const helpFromApi =
      typeof helpFromApiRaw === "string" && helpFromApiRaw.trim().length > 0
        ? helpFromApiRaw.trim()
        : null;
    const help = helpFromApi ?? lockStatusHelpText(status);
    return { status, color, ownerPid, acquiredWallS, managerRpc, help };
  }, [instanceRuntimeStatus, instanceRuntimeLoading]);
  const lastCleanup = useMemo(() => {
    const raw = instanceRuntimeStatus?.last_orphan_cleanup;
    if (!raw || typeof raw !== "object") {
      return null;
    }
    const sourceRaw = raw.source;
    const source =
      typeof sourceRaw === "string" && sourceRaw.trim().length > 0
        ? sourceRaw.trim()
        : null;
    const tsRaw =
      raw.ts && typeof raw.ts === "object"
        ? (raw.ts as Record<string, unknown>)
        : null;
    const tWallRaw = tsRaw?.t_wall;
    const tWall =
      typeof tWallRaw === "number" && Number.isFinite(tWallRaw) ? tWallRaw : null;
    const resultRaw =
      raw.result && typeof raw.result === "object"
        ? (raw.result as Record<string, unknown>)
        : null;
    const summary = parseCleanupSummary(resultRaw);
    if (!summary) {
      return null;
    }
    return {
      source,
      tWall,
      matched: summary.matched,
      terminated: summary.terminated,
      failed: summary.failed,
      dryRun: summary.dryRun,
      candidates: summary.candidates,
    };
  }, [instanceRuntimeStatus]);
  const lockAcquiredLabel = useMemo(() => {
    return formatWallTime24h(lockStatus.acquiredWallS);
  }, [lockStatus.acquiredWallS]);
  const cleanupAtLabel = useMemo(() => {
    if (!lastCleanup) {
      return null;
    }
    const formatted = formatWallTime24h(lastCleanup.tWall);
    return formatted === "n/a" ? null : formatted;
  }, [lastCleanup]);
  const cleanupPreviewLabel = useMemo(() => {
    if (!cleanupPreview) {
      return null;
    }
    const candidatesLabel =
      cleanupPreview.candidates.length > 0
        ? cleanupPreview.candidates.slice(0, 8).join(", ") +
          (cleanupPreview.candidates.length > 8 ? ", ..." : "")
        : "none";
    return {
      summary: `matched=${cleanupPreview.matched} terminated=${cleanupPreview.terminated} failed=${cleanupPreview.failed}`,
      candidates: candidatesLabel,
    };
  }, [cleanupPreview]);

  const runCleanupDryRun = async () => {
    const result = await onRunInstanceCleanupDryRun();
    const summary = parseCleanupSummary(
      result && typeof result === "object" ? result : null
    );
    setCleanupPreview(summary);
    return summary;
  };

  const runCleanupApply = async () => {
    let summary = cleanupPreview;
    if (!summary) {
      summary = await runCleanupDryRun();
      if (!summary) {
        return;
      }
    }
    const candidatesLabel =
      summary.candidates.length > 0 ? summary.candidates.join(", ") : "none";
    const confirmed = window.confirm(
      `Execute orphan cleanup now?\nmatched=${summary.matched}\ncandidates=${candidatesLabel}`
    );
    if (!confirmed) {
      return;
    }
    await onRunInstanceCleanupApply();
    setCleanupPreview(null);
  };

  return (
    <AppShell.Header className="app-header">
      <Group h="100%" px="lg" justify="space-between">
        <Group gap="sm">
          <Popover
            opened={instancePopoverOpen}
            onChange={(open) => {
              setInstancePopoverOpen(open);
              if (open) {
                void onRefreshInstanceRuntimeStatus();
              }
            }}
            width={360}
            withArrow
            shadow="md"
            position="bottom-start"
          >
            <Popover.Target>
              <UnstyledButton
                type="button"
                className="instance-title-trigger"
                onClick={() => {
                  setInstancePopoverOpen((open) => !open);
                }}
                aria-expanded={instancePopoverOpen}
                title="Show instance runtime details"
              >
                <Group gap={6} wrap="nowrap">
                  <Text className="brand" size="lg">
                    {instanceLabel}
                  </Text>
                  <IconChevronDown size={14} />
                </Group>
              </UnstyledButton>
            </Popover.Target>
            <Popover.Dropdown>
              <Stack gap={6}>
                <Group justify="space-between" align="center">
                  <Text size="sm" fw={600}>
                    Instance runtime
                  </Text>
                  <ActionIcon
                    size="sm"
                    variant="subtle"
                    onClick={() => {
                      void onRefreshInstanceRuntimeStatus();
                    }}
                    aria-label="Refresh instance runtime details"
                  >
                    <IconRefresh size={14} />
                  </ActionIcon>
                </Group>
                {instanceRuntimeLoading ? (
                  <Text size="xs" c="dimmed">
                    Loading runtime status...
                  </Text>
                ) : null}
                {instanceRuntimeError ? (
                  <Text size="xs" c="red">
                    {instanceRuntimeError}
                  </Text>
                ) : null}
                <Divider />
                <Group gap={6} align="center">
                  <Text size="xs" c="dimmed">
                    Lock
                  </Text>
                  <Badge size="xs" variant="light" color={lockStatus.color}>
                    {lockStatus.status}
                  </Badge>
                </Group>
                <Text size="xs" c="dimmed">
                  {lockStatus.help}
                </Text>
                {typeof instanceRuntimeStatus?.manager_pid === "number" &&
                Number.isFinite(instanceRuntimeStatus.manager_pid) ? (
                  <Text size="xs">
                    Manager PID: {Math.trunc(instanceRuntimeStatus.manager_pid)}
                  </Text>
                ) : null}
                <Text size="xs">Owner PID: {lockStatus.ownerPid ?? "n/a"}</Text>
                <Text size="xs">
                  {lockStatus.status === "active" ? "Acquired" : "Last acquired"}:{" "}
                  {lockAcquiredLabel}
                </Text>
                {lockStatus.managerRpc ? (
                  <Text size="xs">Manager RPC: {lockStatus.managerRpc}</Text>
                ) : null}
                <Divider />
                <Group justify="space-between" align="center">
                  <Text size="xs" c="dimmed">
                    Orphan cleanup
                  </Text>
                  <Group gap={6}>
                    <Button
                      size="compact-xs"
                      variant="light"
                      color="blue"
                      loading={instanceCleanupBusy}
                      onClick={() => {
                        void runCleanupDryRun();
                      }}
                    >
                      Dry-run
                    </Button>
                    <Button
                      size="compact-xs"
                      variant="light"
                      color="orange"
                      loading={instanceCleanupBusy}
                      onClick={() => {
                        void runCleanupApply();
                      }}
                    >
                      Execute
                    </Button>
                  </Group>
                </Group>
                {cleanupPreviewLabel ? (
                  <>
                    <Text size="xs">{cleanupPreviewLabel.summary}</Text>
                    <Text size="xs" c="dimmed">
                      candidates: {cleanupPreviewLabel.candidates}
                    </Text>
                  </>
                ) : (
                  <Text size="xs" c="dimmed">
                    Run dry-run before execute to review candidate PIDs.
                  </Text>
                )}
                <Divider />
                <Text size="xs" c="dimmed">
                  Last orphan cleanup
                </Text>
                {lastCleanup ? (
                  <>
                    <Text size="xs">
                      matched={lastCleanup.matched} terminated={lastCleanup.terminated}{" "}
                      failed={lastCleanup.failed}
                      {lastCleanup.dryRun ? " (dry-run)" : ""}
                    </Text>
                    {lastCleanup.candidates.length > 0 ? (
                      <Text size="xs" c="dimmed">
                        candidates: {lastCleanup.candidates.slice(0, 8).join(", ")}
                        {lastCleanup.candidates.length > 8 ? ", ..." : ""}
                      </Text>
                    ) : null}
                    {lastCleanup.source ? (
                      <Text size="xs" c="dimmed">
                        source: {lastCleanup.source}
                      </Text>
                    ) : null}
                    {cleanupAtLabel ? (
                      <Text size="xs" c="dimmed">
                        at: {cleanupAtLabel}
                      </Text>
                    ) : null}
                  </>
                ) : (
                  <Text size="xs" c="dimmed">
                    No cleanup recorded yet.
                  </Text>
                )}
              </Stack>
            </Popover.Dropdown>
          </Popover>
          {showHdfWriter && (
            <Button
              size="xs"
              variant="light"
              color={hdfWriterChipColor}
              loading={hdfWriterLoading}
              onClick={() => {
                void onOpenHdfWriter();
              }}
              title={hdfWriterTitle}
            >
              {hdfWriterFileLabel !== "no file" && hdfWriterFileLabel !== "status unavailable"
                ? `HDF | ${hdfWriterFileLabel}`
                : "HDF"}
            </Button>
          )}
          {showInfluxWriter && (
            <Button
              size="xs"
              variant="light"
              color={influxWriterChipColor}
              leftSection={<IconDatabase size={14} />}
              loading={influxWriterLoading}
              onClick={() => {
                void onOpenInfluxWriter();
              }}
              title={influxWriterTitle}
            >
              {influxWriterLabel}
            </Button>
          )}
          {showHdfNoteChiplet && (
            <Button
              size="xs"
              variant="light"
              color="orange"
              leftSection={<IconFileText size={14} />}
              loading={hdfMeasurementSchemaLoading}
              disabled={hdfCommandsBlocked || hdfMeasurementNoteBusy}
              onClick={() => {
                void onOpenHdfMeasurementNote();
              }}
              title={
                hdfMeasurementSchemaDisplayError ??
                "Add a measurement note to the active HDF file"
              }
            >
              Note ({hdfMeasurementNotesRows})
            </Button>
          )}
          {showSequencer && (
            <Button.Group>
              <Button
                size="xs"
                variant="light"
                color={sequencerChipColor}
                loading={sequencerStatusLoading}
                onClick={() => {
                  void onOpenSequencer();
                }}
                title={sequencerStatusError ?? sequencerChipTooltip}
                style={sequencerChipProgressStyle}
              >
                Sequencer {sequencerRuntimeState}
                {sequencerChipSuffix}
              </Button>
              <Button
                size="xs"
                variant="light"
                color={sequencerPrimaryAction === "start" ? "teal" : "yellow"}
                leftSection={sequencerPrimaryIcon}
                disabled={sequencerPrimaryDisabled}
                loading={sequencerActionBusy}
                onClick={() => {
                  void onRunSequencerPrimaryAction();
                }}
                title={
                  sequencerPrimaryAction === "start" && !sequencerLoaded
                    ? "Load a sequence before starting"
                    : undefined
                }
              >
                {sequencerPrimaryLabel}
              </Button>
            </Button.Group>
          )}
        </Group>
        <Group gap="xs">
          <Button
            size="xs"
            variant="light"
            color="gray"
            leftSection={<IconCpu size={14} />}
            onClick={() => {
              void onOpenProcesses();
            }}
          >
            Processes
          </Button>
          <Button
            size="xs"
            variant="light"
            color={stateMachineButtonSummary.color}
            leftSection={<IconCpu size={14} />}
            onClick={onOpenStateMachines}
            title={stateMachineButtonSummary.tooltip}
          >
            {stateMachineButtonSummary.label}
          </Button>
          <Button
            size="xs"
            variant="light"
            color={interlockButtonSummary.color}
            leftSection={<IconShieldCheck size={14} />}
            onClick={onOpenInterlocks}
            title={interlockButtonSummary.tooltip}
          >
            {interlockButtonSummary.label}
          </Button>
          {showDaqUi ? (
            <Button
              size="xs"
              variant="light"
              color="cyan"
              leftSection={<IconPencil size={14} />}
              onClick={() => {
                void onOpenDaq();
              }}
              title="Open shared stream-analysis workspaces"
            >
              DAG ({daqWorkspaceCount})
            </Button>
          ) : null}
          <Button
            size="xs"
            variant="light"
            color={commandUnreadError ? "red" : "gray"}
            leftSection={<IconTerminal2 size={14} />}
            onClick={onOpenCommandHistory}
            title="Latest command requests and replies"
          >
            Commands ({commandHistoryCount})
          </Button>
          <Button
            size="xs"
            variant="light"
            color={logsUnreadError ? "red" : "gray"}
            leftSection={<IconFileText size={14} />}
            onClick={onOpenLogs}
          >
            Logs
          </Button>
          <Button
            size="xs"
            variant="light"
            color="gray"
            leftSection={<IconSettings size={14} />}
            onClick={onOpenSettings}
          >
            Settings
          </Button>
          <Button
            size="xs"
            variant="light"
            leftSection={<IconRefresh size={14} />}
            onClick={() => {
              void onRefreshStatus();
            }}
          >
            Refresh status
          </Button>
          <SegmentedControl
            size="xs"
            value={colorScheme}
            onChange={(value) =>
              onColorSchemeChange(value as "light" | "dark" | "auto")
            }
            data={[
              { label: "Light", value: "light" },
              { label: "Dark", value: "dark" },
              { label: "Auto", value: "auto" },
            ]}
          />
          <Badge variant="light" color={telemetryBadgeColor}>
            {telemetryBadgeLabel}
          </Badge>
        </Group>
      </Group>
    </AppShell.Header>
  );
}
