import {
  ActionIcon,
  AppShell,
  Badge,
  Button,
  Divider,
  Group,
  Menu,
  Popover,
  Stack,
  Text,
  Tooltip,
  UnstyledButton,
} from "@mantine/core";
import {
  IconChevronDown,
  IconCpu,
  IconDatabase,
  IconDots,
  IconFileText,
  IconExternalLink,
  IconLayoutGrid,
  IconPencil,
  IconPlayerPause,
  IconPlayerPlay,
  IconRefresh,
  IconSettings,
  IconShieldCheck,
  IconSitemap,
  IconTerminal2,
} from "@tabler/icons-react";
import type { ExtraUiInfo } from "../api";
import { useMemo, useState, type ReactNode } from "react";
import { useHeaderCompact } from "../features/layout/useHeaderCompact";
import { useSequencerHeader } from "../features/sequencer/SequencerContext";
import { useAdaptivePolling } from "../features/polling/useAdaptivePolling";

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
  onOpenProcesses: () => Promise<unknown> | void;
  onOpenDeviceOverview: () => void;
  deviceIssueCount: number;
  deviceFailedCount: number;
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
  extraUis: ExtraUiInfo[];
  onOpenSettings: () => void;
  onRefreshStatus: () => Promise<unknown> | void;
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

function trailingCount(label: string): string | null {
  const match = /\((\d+)\)\s*$/.exec(label);
  return match ? match[1] : null;
}

/**
 * Header navigation button. Below the compact breakpoint (see styles.css
 * `.header-nav-label`) the text label hides and only the icon plus the
 * optional count remain; the tooltip always carries the full label.
 */
function HeaderNavButton({
  icon,
  label,
  compactLabel = null,
  color,
  tooltip,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  compactLabel?: string | null;
  color: string;
  tooltip?: string;
  onClick: () => void;
}) {
  return (
    <Tooltip label={tooltip ?? label} withArrow openDelay={250}>
      <Button
        className="header-nav-btn"
        size="xs"
        variant="light"
        color={color}
        leftSection={icon}
        onClick={onClick}
        aria-label={label}
      >
        <span className="header-nav-label">{label}</span>
        {compactLabel ? <span className="header-nav-compact">{compactLabel}</span> : null}
      </Button>
    </Tooltip>
  );
}

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
  onOpenProcesses,
  onOpenDeviceOverview,
  deviceIssueCount,
  deviceFailedCount,
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
  extraUis,
  onOpenSettings,
  onRefreshStatus,
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
  useAdaptivePolling({
    enabled: instancePopoverOpen && !instanceRuntimeLoading && !instanceCleanupBusy,
    intervalMs: 3000,
    poll: async () => onRefreshInstanceRuntimeStatus(),
    onValue: () => undefined,
    pollImmediately: false,
    endpoint: "/api/runtime/status",
  });

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

  const sequencer = useSequencerHeader();
  const liveTone = telemetryBadgeColor === "red" ? "red" : telemetryBadgeColor;
  const stateMachineCount = trailingCount(stateMachineButtonSummary.label);
  const interlockCount = trailingCount(interlockButtonSummary.label);
  const header = useHeaderCompact(
    [
      instanceLabel,
      showHdfWriter && hdfWriterFileLabel,
      showInfluxWriter && influxWriterLabel,
      showHdfNoteChiplet && hdfMeasurementNotesRows,
      showSequencer && `${sequencer.runtimeState}${sequencer.chipSuffix}${sequencer.primaryLabel}`,
      deviceIssueCount,
      stateMachineButtonSummary.label,
      interlockButtonSummary.label,
      showDaqUi && daqWorkspaceCount,
      telemetryBadgeLabel,
    ].join("|")
  );

  return (
    <AppShell.Header className="app-header">
      <div
        ref={header.rowRef}
        className={`app-header-row${header.compact ? " app-header-row-compact" : ""}`}
      >
        <div ref={header.identityRef} className="app-header-identity">
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
                <Text className="brand" size="lg" truncate>
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
          {liveTone === "red" ? (
            <Badge size="sm" variant="light" color="red">
              {telemetryBadgeLabel}
            </Badge>
          ) : (
            <Tooltip label={telemetryBadgeLabel} withArrow>
              <span
                className="app-header-live-dot"
                style={{ background: `var(--mantine-color-${liveTone}-6)` }}
                aria-label={telemetryBadgeLabel}
                role="status"
              />
            </Tooltip>
          )}
        </div>
        <div ref={header.runRef} className="app-header-run">
          {showHdfWriter && (
            <Button
              className="app-header-run-chip app-header-run-chip-hdf"
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
              className="app-header-run-chip-fixed"
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
              className="app-header-run-chip-fixed"
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
            <Button.Group className="app-header-run-group">
              <Button
                className="app-header-run-chip"
                size="xs"
                variant="light"
                color={sequencer.chipColor}
                loading={sequencer.statusLoading}
                onClick={() => {
                  void sequencer.openSequencer();
                }}
                title={sequencer.statusError ?? sequencer.chipTooltip}
                style={sequencer.chipProgressStyle}
              >
                Sequencer {sequencer.runtimeState}
                {sequencer.chipSuffix}
              </Button>
              <Button
                className="app-header-run-chip-fixed"
                size="xs"
                variant="light"
                color={sequencer.primaryAction === "start" ? "teal" : "yellow"}
                leftSection={
                  sequencer.primaryAction === "pause" ? (
                    <IconPlayerPause size={14} />
                  ) : (
                    <IconPlayerPlay size={14} />
                  )
                }
                disabled={sequencer.primaryDisabled}
                loading={sequencer.actionBusy}
                onClick={() => {
                  void sequencer.runPrimaryAction();
                }}
                title={
                  sequencer.primaryAction === "start" && !sequencer.loaded
                    ? "Load a sequence before starting"
                    : undefined
                }
              >
                {sequencer.primaryLabel}
              </Button>
            </Button.Group>
          )}
        </div>
        <div ref={header.navRef} className="app-header-nav">
          <HeaderNavButton
            icon={<IconLayoutGrid size={14} />}
            label={deviceIssueCount > 0 ? `Devices (${deviceIssueCount})` : "Devices"}
            compactLabel={deviceIssueCount > 0 ? String(deviceIssueCount) : null}
            color={deviceFailedCount > 0 ? "red" : deviceIssueCount > 0 ? "orange" : "gray"}
            tooltip={
              deviceIssueCount > 0
                ? `Device overview (o): ${deviceIssueCount} device${
                    deviceIssueCount === 1 ? "" : "s"
                  } need attention`
                : "Device overview (o)"
            }
            onClick={onOpenDeviceOverview}
          />
          <HeaderNavButton
            icon={<IconCpu size={14} />}
            label="Processes"
            color="gray"
            onClick={() => {
              void onOpenProcesses();
            }}
          />
          <HeaderNavButton
            icon={<IconSitemap size={14} />}
            label={stateMachineButtonSummary.label}
            compactLabel={stateMachineCount}
            color={stateMachineButtonSummary.color}
            tooltip={`State machines: ${stateMachineButtonSummary.tooltip}`}
            onClick={onOpenStateMachines}
          />
          <HeaderNavButton
            icon={<IconShieldCheck size={14} />}
            label={interlockButtonSummary.label}
            compactLabel={interlockCount}
            color={interlockButtonSummary.color}
            tooltip={interlockButtonSummary.tooltip}
            onClick={onOpenInterlocks}
          />
          {showDaqUi ? (
            <HeaderNavButton
              icon={<IconPencil size={14} />}
              label={`DAG (${daqWorkspaceCount})`}
              compactLabel={String(daqWorkspaceCount)}
              color="cyan"
              tooltip="Open shared stream-analysis workspaces"
              onClick={() => {
                void onOpenDaq();
              }}
            />
          ) : null}
          <Tooltip label={`Command history (${commandHistoryCount})`} withArrow>
            <ActionIcon
              size={30}
              variant="light"
              color={commandUnreadError ? "red" : "gray"}
              onClick={onOpenCommandHistory}
              aria-label="Command history"
            >
              <IconTerminal2 size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label={logsUnreadError ? "Logs (new errors)" : "Logs"} withArrow>
            <ActionIcon
              size={30}
              variant="light"
              color={logsUnreadError ? "red" : "gray"}
              onClick={onOpenLogs}
              aria-label="Logs"
            >
              <IconFileText size={16} />
            </ActionIcon>
          </Tooltip>
          <Menu shadow="md" width={240} position="bottom-end" withArrow>
            <Menu.Target>
              <ActionIcon size={30} variant="light" color="gray" aria-label="More">
                <IconDots size={16} />
              </ActionIcon>
            </Menu.Target>
            <Menu.Dropdown>
              {extraUis.length > 0 ? (
                <>
                  <Menu.Label>Instance UIs</Menu.Label>
                  {extraUis.map((item) => (
                    <Menu.Item
                      key={item.slug}
                      component="a"
                      href={item.href}
                      target="_blank"
                      rel="noreferrer"
                      leftSection={<IconExternalLink size={14} />}
                    >
                      {item.label}
                    </Menu.Item>
                  ))}
                  <Menu.Divider />
                </>
              ) : null}
              <Menu.Item
                leftSection={<IconRefresh size={14} />}
                onClick={() => {
                  void onRefreshStatus();
                }}
              >
                Refresh status
              </Menu.Item>
              <Menu.Item leftSection={<IconSettings size={14} />} onClick={onOpenSettings}>
                Settings
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </div>
      </div>
    </AppShell.Header>
  );
}
