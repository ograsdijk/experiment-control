import {
  AppShell,
  Badge,
  Button,
  Group,
  SegmentedControl,
  Text,
} from "@mantine/core";
import {
  IconCpu,
  IconFileText,
  IconPencil,
  IconRefresh,
  IconSettings,
  IconShieldCheck,
  IconTerminal2,
} from "@tabler/icons-react";
import type { CSSProperties, ReactNode } from "react";

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
}: Props) {
  return (
    <AppShell.Header className="app-header">
      <Group h="100%" px="lg" justify="space-between">
        <Group gap="sm">
          <div className="pulse" />
          <Text className="brand" size="lg">
            {instanceLabel}
          </Text>
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
              HDF {hdfWriterState} | {hdfWriterFileLabel}
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
