import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Collapse,
  Group,
  Modal,
  NumberInput,
  Progress,
  ScrollArea,
  Select,
  SegmentedControl,
  Stack,
  Text,
  Textarea,
  TextInput,
} from "@mantine/core";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
import {
  Suspense,
  lazy,
  useEffect,
  useState,
  type ChangeEvent,
  type ReactNode,
  type RefObject,
} from "react";
import {
  processStateColor,
  sequencerRuntimeStateColor,
} from "../features/runtime/helpers";
import { formatDurationCompact } from "../features/sequencer/utils";
import type { StreamAnalysisWorkspaceConfig } from "../features/stream/types";
import type {
  SequencerAdaptiveStudyStatus,
  SequencerDiagnostic,
  SequencerErrorDetail,
  SequencerProgress,
  SequencerStepDetail,
  SequencerYamlEditorHandle,
} from "../features/sequencer/types";
import type { CapabilityMember } from "../types";
import type { StreamCatalogEntry } from "../types";
import type { TelemetrySignal } from "../types";
import { SequencerOutlinePane } from "./SequencerOutlinePane";
import { YamlPreview } from "./YamlPreview";

const LazySequencerYamlCodeEditor = lazy(
  () => import("../features/sequencer/components/SequencerYamlCodeEditor")
);

type SequencerLibraryEntry = {
  id: string;
  label: string | null;
  description: string | null;
  path: string | null;
  source: string | null;
  vars: string[];
};

type SequencerOverrideRow = {
  id: string;
  name: string;
  valueType: "number" | "bool" | "string" | "json" | "null";
  valueText: string;
};

type Props = {
  opened: boolean;
  onClose: () => void;
  processState: string;
  runtimeState: string;
  loaded: boolean;
  currentStep: string | null;
  currentStepDetail: SequencerStepDetail | null;
  errorDetail: SequencerErrorDetail | null;
  cleanupActive: boolean | null;
  progress: SequencerProgress | null;
  progressPercent: number | null;
  totalSteps: number | null;
  completedSteps: number | null;
  loadedSource: string | null;
  autoloadError: string | null;
  statusError: string | null;
  modalError: string | null;
  primaryIcon: ReactNode;
  primaryAction: "start" | "pause" | "resume";
  primaryLabel: string;
  primaryDisabled: boolean;
  actionBusy: boolean;
  runMode: "once" | "repeat" | "continuous";
  repeatCount: number;
  onRunModeChange: (mode: "once" | "repeat" | "continuous") => void;
  onRepeatCountChange: (value: number) => void;
  libraryConfigured: boolean;
  libraryEntries: SequencerLibraryEntry[];
  libraryLoading: boolean;
  libraryError: string | null;
  selectedSequenceId: string | null;
  onSelectedSequenceIdChange: (sequenceId: string | null) => void;
  onReloadLibrary: () => Promise<unknown> | void;
  overrideRows: SequencerOverrideRow[];
  overrideVarOptions: string[];
  overrideErrors: Record<string, string | null>;
  overridePreview: string;
  overridesValid: boolean;
  onAddOverrideRow: () => void;
  onRemoveOverrideRow: (rowId: string) => void;
  onUpdateOverrideRow: (
    rowId: string,
    patch: Partial<
      Pick<SequencerOverrideRow, "name" | "valueType" | "valueText">
    >
  ) => void;
  onClearOverrides: () => void;
  adaptiveModes: Record<string, "reset" | "resume" | "warm_start">;
  adaptiveStudies: Record<string, SequencerAdaptiveStudyStatus>;
  loadedAdaptiveIds: readonly string[];
  adaptiveClearBusyStudyId: string | null;
  onRunAction: (
    action: "start" | "pause" | "resume" | "stop"
  ) => Promise<unknown> | void;
  onAdaptiveModeChange: (
    studyId: string,
    mode: "reset" | "resume" | "warm_start"
  ) => void;
  onClearAdaptiveStudy: (studyId: string) => Promise<unknown> | void;
  fileInputRef: RefObject<HTMLInputElement>;
  onFileInputChange: (event: ChangeEvent<HTMLInputElement>) => Promise<unknown> | void;
  yamlViewMode: "edit" | "preview";
  onYamlViewModeChange: (mode: "edit" | "preview") => void;
  loadedYamlBusy: boolean;
  hasSequencerProcess: boolean;
  onShowLoadedYaml: () => Promise<unknown> | void;
  validateBusy: boolean;
  onValidate: () => Promise<unknown> | void;
  loadBusy: boolean;
  onLoad: () => Promise<unknown> | void;
  onLoadSelectedLibrary: () => Promise<unknown> | void;
  yamlDirty: boolean;
  reloadSourceBusy: boolean;
  canReloadSource: boolean;
  reloadSourceLabel: string;
  onReloadLoadedSource: () => Promise<unknown> | void;
  editorRef: RefObject<SequencerYamlEditorHandle>;
  yamlText: string;
  onYamlTextChange: (value: string) => void;
  streamCatalog: StreamCatalogEntry[];
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
  streamWorkspaces: Record<string, StreamAnalysisWorkspaceConfig>;
  latestSignalsByDevice: Record<string, Record<string, TelemetrySignal>>;
  colorScheme: "light" | "dark";
  diagnostics: ReadonlyArray<SequencerDiagnostic>;
  onJumpToDiagnostic: (
    line: number | null,
    column: number | null
  ) => Promise<unknown> | void;
};

export function SequencerModal({
  opened,
  onClose,
  processState,
  runtimeState,
  loaded,
  currentStep,
  currentStepDetail,
  errorDetail,
  cleanupActive,
  progress,
  progressPercent,
  totalSteps,
  completedSteps,
  loadedSource,
  autoloadError,
  statusError,
  modalError,
  primaryIcon,
  primaryAction,
  primaryLabel,
  primaryDisabled,
  actionBusy,
  runMode,
  repeatCount,
  onRunModeChange,
  onRepeatCountChange,
  libraryConfigured,
  libraryEntries,
  libraryLoading,
  libraryError,
  selectedSequenceId,
  onSelectedSequenceIdChange,
  onReloadLibrary,
  overrideRows,
  overrideVarOptions,
  overrideErrors,
  overridePreview,
  overridesValid,
  onAddOverrideRow,
  onRemoveOverrideRow,
  onUpdateOverrideRow,
  onClearOverrides,
  adaptiveModes,
  adaptiveStudies,
  loadedAdaptiveIds,
  adaptiveClearBusyStudyId,
  onRunAction,
  onAdaptiveModeChange,
  onClearAdaptiveStudy,
  fileInputRef,
  onFileInputChange,
  yamlViewMode,
  onYamlViewModeChange,
  loadedYamlBusy,
  hasSequencerProcess,
  onShowLoadedYaml,
  validateBusy,
  onValidate,
  loadBusy,
  onLoad,
  onLoadSelectedLibrary,
  yamlDirty,
  reloadSourceBusy,
  canReloadSource,
  reloadSourceLabel,
  onReloadLoadedSource,
  editorRef,
  yamlText,
  onYamlTextChange,
  streamCatalog,
  capabilitiesByDevice,
  streamWorkspaces,
  latestSignalsByDevice,
  colorScheme,
  diagnostics,
  onJumpToDiagnostic,
}: Props) {
  const [showFullYaml, setShowFullYaml] = useState(false);
  const [diagnosticsCollapsed, setDiagnosticsCollapsed] = useState(false);
  const [controlsCollapsed, setControlsCollapsed] = useState(false);

  useEffect(() => {
    if (opened) {
      setShowFullYaml(false);
      setDiagnosticsCollapsed(false);
      setControlsCollapsed(false);
    }
  }, [opened]);

  const libraryOptions = libraryEntries.map((entry) => ({
    value: entry.id,
    label: entry.label ? `${entry.label} (${entry.id})` : entry.id,
  }));
  const selectedLibraryEntry = selectedSequenceId
    ? libraryEntries.find((entry) => entry.id === selectedSequenceId) ?? null
    : null;

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Sequencer"
      size="clamp(56rem, 92vw, 96rem)"
      centered
      zIndex={440}
    >
      <Stack
        gap="sm"
        style={{ height: "clamp(42rem, 88vh, 78rem)", minHeight: 0, overflow: "hidden" }}
      >
        <Group justify="space-between" align="flex-start" style={{ flexShrink: 0 }}>
          <Stack gap={4}>
            <Group gap="xs" wrap="wrap">
              <Badge variant="light" color={processStateColor(processState)}>
                Process {processState}
              </Badge>
              <Badge
                variant="light"
                color={sequencerRuntimeStateColor(runtimeState, processState)}
              >
                State {runtimeState}
              </Badge>
              <Badge variant="outline" color={loaded ? "teal" : "gray"}>
                {loaded ? "Loaded" : "Not loaded"}
              </Badge>
            </Group>
            {currentStep && (
              <Stack gap={2}>
                <Group gap="xs" wrap="wrap">
                  <Text size="xs" c="dimmed">
                    Current step: {currentStepDetail?.summary ?? currentStep}
                  </Text>
                  {cleanupActive && (
                    <Badge size="xs" variant="light" color="yellow">
                      cleanup/finally
                    </Badge>
                  )}
                </Group>
                {currentStepDetail && (
                  <Text size="xs" c="dimmed">
                    {[
                      currentStepDetail.line !== null
                        ? `line ${currentStepDetail.line}`
                        : null,
                      currentStepDetail.path,
                      currentStepDetail.branch,
                    ]
                      .filter(Boolean)
                      .join(" | ")}
                  </Text>
                )}
              </Stack>
            )}
            {progress && (
              <Stack gap={4}>
                {progressPercent !== null && (
                  <Progress value={progressPercent} size="sm" radius="xl" />
                )}
                <Text size="xs" c="dimmed">
                  {totalSteps !== null
                    ? `Progress: ${completedSteps ?? 0}/${totalSteps} (${(progressPercent ?? 0).toFixed(1)}%)`
                    : `Completed steps: ${completedSteps ?? 0}${
                        progress.estimateReason
                          ? ` | Total unknown: ${progress.estimateReason}`
                          : ""
                      }`}
                </Text>
                <Text size="xs" c="dimmed">
                  Elapsed: {formatDurationCompact(progress.elapsedS)}
                  {progress.etaS !== null
                    ? `  ETA: ${formatDurationCompact(progress.etaS)}`
                    : ""}
                </Text>
                <Text size="xs" c="dimmed">
                  Run mode: {progress.loopMode ?? "once"}
                  {progress.loopsTarget !== null
                    ? ` (${progress.loopsCompleted ?? 0}/${progress.loopsTarget})`
                    : progress.loopsCompleted !== null
                      ? ` (${progress.loopsCompleted} completed)`
                      : ""}
                </Text>
              </Stack>
            )}
            {loadedSource && (
              <Text size="xs" c="dimmed">
                Loaded from: {loadedSource}
              </Text>
            )}
            {autoloadError && (
              <Text size="xs" c="red">
                Autoload failed: {autoloadError}
              </Text>
            )}
            {loaded && !loadedSource && (
              <Text size="xs" c="dimmed">
                Loaded sequence source is unavailable.
              </Text>
            )}
            {statusError && (
              <Stack gap={2}>
                <Text size="xs" c="red">
                  {errorDetail?.formatted ?? statusError}
                </Text>
                {errorDetail?.step && (
                  <Text size="xs" c="dimmed">
                    {[
                      errorDetail.step.line !== null
                        ? `line ${errorDetail.step.line}`
                        : null,
                      errorDetail.step.path,
                      errorDetail.step.branch,
                    ]
                      .filter(Boolean)
                      .join(" | ")}
                  </Text>
                )}
              </Stack>
            )}
            {modalError && (
              <Text size="xs" c="red">
                {modalError}
              </Text>
            )}
          </Stack>
          <Group gap="xs">
            <Button
              size="xs"
              variant="light"
              leftSection={primaryIcon}
              color={primaryAction === "start" ? "teal" : "yellow"}
              disabled={primaryDisabled}
              loading={actionBusy}
              onClick={() => {
                void onRunAction(primaryAction);
              }}
            >
              {primaryLabel}
            </Button>
            <Button
              size="xs"
              variant="light"
              color="red"
              disabled={actionBusy}
              loading={actionBusy}
              onClick={() => {
                void onRunAction("stop");
              }}
            >
              Stop
            </Button>
          </Group>
        </Group>

        <Card
          radius="md"
          p="sm"
          style={{ border: "1px solid var(--card-border)", flexShrink: 0 }}
        >
          <Stack gap={8}>
            <Group justify="space-between" align="center" wrap="wrap">
                <Text size="sm" fw={600}>
                Run controls
              </Text>
              <Group gap="xs" align="center" wrap="wrap">
                <Badge size="xs" variant="light">
                  {runMode}
                </Badge>
                {selectedSequenceId && (
                  <Badge size="xs" variant="outline" color="gray">
                    {selectedSequenceId}
                  </Badge>
                )}
                {overrideRows.length > 0 && (
                  <Badge size="xs" variant="light" color="blue">
                    {overrideRows.length} override{overrideRows.length === 1 ? "" : "s"}
                  </Badge>
                )}
                {yamlDirty && (
                  <Badge size="xs" variant="light" color="yellow">
                    Editor changes not loaded
                  </Badge>
                )}
                <ActionIcon
                  size="sm"
                  variant="subtle"
                  color="gray"
                  aria-label={
                    controlsCollapsed ? "Expand run controls" : "Collapse run controls"
                  }
                  onClick={() => setControlsCollapsed((prev) => !prev)}
                >
                  {controlsCollapsed ? (
                    <IconChevronRight size={16} />
                  ) : (
                    <IconChevronDown size={16} />
                  )}
                </ActionIcon>
              </Group>
            </Group>
            <Collapse in={!controlsCollapsed}>
              <Stack gap={8}>
                {yamlDirty && (
                  <Text size="xs" c="yellow">
                    Editor changes are not loaded into the runtime until you press
                    Load editor YAML.
                  </Text>
                )}
                <Group justify="space-between" align="center" wrap="wrap">
                  <Text size="sm" fw={600}>
                    Start mode
                  </Text>
                  <Group gap="xs" align="center">
                <SegmentedControl
                  size="xs"
                  value={runMode}
                  onChange={(value) =>
                    onRunModeChange(value as "once" | "repeat" | "continuous")
                  }
                  data={[
                    { value: "once", label: "Once" },
                    { value: "repeat", label: "N times" },
                    { value: "continuous", label: "Continuous" },
                  ]}
                />
                {runMode === "repeat" && (
                  <NumberInput
                    size="xs"
                    min={1}
                    max={1000000}
                    step={1}
                    value={repeatCount}
                    onChange={(value) =>
                      onRepeatCountChange(
                        typeof value === "number" && Number.isFinite(value)
                          ? Math.max(1, Math.trunc(value))
                          : 1
                      )
                    }
                    w={96}
                  />
                )}
              </Group>
                </Group>

            {(libraryConfigured || libraryOptions.length > 0) && (
              <Stack gap={6}>
                <Group justify="space-between" align="center">
                  <Text size="sm" fw={600}>
                    Sequence library ({libraryEntries.length})
                  </Text>
                  <Group gap="xs">
                    <Button
                      size="compact-xs"
                      variant="subtle"
                      color="gray"
                      loading={loadBusy}
                      disabled={!selectedSequenceId || libraryLoading}
                      onClick={() => {
                        void onLoadSelectedLibrary();
                      }}
                    >
                      Load selected library
                    </Button>
                    <Button
                      size="compact-xs"
                      variant="subtle"
                      color="gray"
                      loading={libraryLoading}
                      onClick={() => {
                        void onReloadLibrary();
                      }}
                    >
                      Reload
                    </Button>
                  </Group>
                </Group>
                <Select
                  size="xs"
                  placeholder="Select sequence id"
                  data={libraryOptions}
                  value={selectedSequenceId}
                  onChange={(value) => onSelectedSequenceIdChange(value)}
                  searchable
                  clearable
                  comboboxProps={{ zIndex: 500 }}
                  disabled={libraryLoading}
                />
                <Text size="xs" c="dimmed">
                  Selected: {selectedSequenceId ?? "none"}
                </Text>
                {selectedLibraryEntry?.description && (
                  <Text size="xs" c="dimmed">
                    {selectedLibraryEntry.description}
                  </Text>
                )}
                {libraryError && (
                  <Text size="xs" c="red">
                    {libraryError}
                  </Text>
                )}
              </Stack>
            )}

            <Stack gap={6}>
              <Group justify="space-between" align="center">
                <Group gap="xs">
                  <Text size="sm" fw={600}>
                    Run overrides
                  </Text>
                  <Badge
                    size="xs"
                    variant="light"
                    color={overridesValid ? "teal" : "red"}
                  >
                    {overridesValid ? "valid" : "invalid"}
                  </Badge>
                </Group>
                <Group gap="xs">
                  <Button
                    size="compact-xs"
                    variant="subtle"
                    color="gray"
                    onClick={onAddOverrideRow}
                  >
                    Add
                  </Button>
                  <Button
                    size="compact-xs"
                    variant="subtle"
                    color="gray"
                    onClick={onClearOverrides}
                    disabled={overrideRows.length === 0}
                  >
                    Clear
                  </Button>
                </Group>
              </Group>
              <Text size="xs" c="dimmed">
                Applied only to the next Start call (`vars_override`), not saved to YAML.
              </Text>
              {overrideVarOptions.length === 0 && (
                <Text size="xs" c="yellow">
                  Variable list unavailable. Enter names manually.
                </Text>
              )}
              {overrideRows.length === 0 ? (
                <Text size="xs" c="dimmed">
                  No overrides configured.
                </Text>
              ) : (
                <Stack gap={6}>
                  {overrideRows.map((row) => (
                    <Card
                      key={row.id}
                      p="xs"
                      radius="sm"
                      style={{ border: "1px solid var(--card-border)" }}
                    >
                      <Stack gap={6}>
                        <Group gap="xs" align="end" wrap="wrap">
                          {overrideVarOptions.length > 0 ? (
                            <Select
                              size="xs"
                              label="Variable"
                              data={overrideVarOptions.map((item) => ({
                                value: item,
                                label: item,
                              }))}
                              value={row.name || null}
                              onChange={(value) =>
                                onUpdateOverrideRow(row.id, { name: value ?? "" })
                              }
                              searchable
                              w={220}
                            />
                          ) : (
                            <TextInput
                              size="xs"
                              label="Variable"
                              value={row.name}
                              onChange={(event) =>
                                onUpdateOverrideRow(row.id, {
                                  name: event.currentTarget.value,
                                })
                              }
                              w={220}
                            />
                          )}
                          <Select
                            size="xs"
                            label="Type"
                            data={[
                              { value: "number", label: "number" },
                              { value: "bool", label: "bool" },
                              { value: "string", label: "string" },
                              { value: "json", label: "json" },
                              { value: "null", label: "null" },
                            ]}
                            value={row.valueType}
                            onChange={(value) =>
                              onUpdateOverrideRow(row.id, {
                                valueType:
                                  (value as SequencerOverrideRow["valueType"] | null) ??
                                  "string",
                              })
                            }
                            w={140}
                          />
                          {row.valueType === "bool" ? (
                            <Select
                              size="xs"
                              label="Value"
                              data={[
                                { value: "true", label: "true" },
                                { value: "false", label: "false" },
                              ]}
                              value={
                                row.valueText === "true" || row.valueText === "false"
                                  ? row.valueText
                                  : "true"
                              }
                              onChange={(value) =>
                                onUpdateOverrideRow(row.id, {
                                  valueText: value ?? "true",
                                })
                              }
                              w={140}
                            />
                          ) : row.valueType === "null" ? (
                            <Text size="xs" c="dimmed" mt={22}>
                              Value fixed to null
                            </Text>
                          ) : (
                            <TextInput
                              size="xs"
                              label="Value"
                              value={row.valueText}
                              onChange={(event) =>
                                onUpdateOverrideRow(row.id, {
                                  valueText: event.currentTarget.value,
                                })
                              }
                              placeholder={
                                row.valueType === "json"
                                  ? '{"key": 1}'
                                  : row.valueType === "number"
                                    ? "1.23"
                                    : "text"
                              }
                              styles={{
                                input:
                                  row.valueType === "json"
                                    ? { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }
                                    : undefined,
                              }}
                              w={row.valueType === "json" ? 280 : 200}
                            />
                          )}
                          <Button
                            size="compact-xs"
                            variant="subtle"
                            color="red"
                            onClick={() => onRemoveOverrideRow(row.id)}
                          >
                            Remove
                          </Button>
                        </Group>
                        {overrideErrors[row.id] && (
                          <Text size="xs" c="red">
                            {overrideErrors[row.id]}
                          </Text>
                        )}
                      </Stack>
                    </Card>
                  ))}
                </Stack>
              )}
              <Textarea
                size="xs"
                label="vars_override preview"
                value={overridePreview || "{}"}
                readOnly
                autosize
                minRows={2}
                maxRows={6}
                styles={{
                  input: {
                    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                  },
                }}
              />
            </Stack>
              </Stack>
            </Collapse>
          </Stack>
        </Card>

        {loadedAdaptiveIds.length > 0 && (
          <Stack gap={6}>
            <Group justify="space-between" align="center">
              <Text size="sm" fw={600}>
                Adaptive reuse
              </Text>
              <Text size="xs" c="dimmed">
                Choose how each adaptive study starts when you press Start.
              </Text>
            </Group>
            {loadedAdaptiveIds.map((studyId) => {
              const status = adaptiveStudies[studyId];
              const trialCount = status?.trialCount ?? 0;
              const hasSaved = trialCount > 0;
              return (
                <Card
                  key={studyId}
                  p="xs"
                  radius="sm"
                  style={{ border: "1px solid var(--card-border)" }}
                >
                  <Stack gap={6}>
                    <Group justify="space-between" align="center" wrap="wrap">
                      <Group gap="xs" wrap="wrap">
                        <Text size="sm" fw={600}>
                          {studyId}
                        </Text>
                        <Badge
                          size="xs"
                          variant="light"
                          color={hasSaved ? "teal" : "gray"}
                        >
                          {hasSaved ? `${trialCount} saved trial${trialCount === 1 ? "" : "s"}` : "No saved data"}
                        </Badge>
                        {status?.controllerKind && (
                          <Badge size="xs" variant="outline" color="gray">
                            {status.controllerKind}
                          </Badge>
                        )}
                      </Group>
                      <Button
                        size="compact-xs"
                        variant="subtle"
                        color="red"
                        disabled={!hasSaved}
                        loading={adaptiveClearBusyStudyId === studyId}
                        onClick={() => {
                          void onClearAdaptiveStudy(studyId);
                        }}
                      >
                        Clear saved
                      </Button>
                    </Group>
                    <SegmentedControl
                      size="xs"
                      fullWidth
                      value={adaptiveModes[studyId] ?? "reset"}
                      onChange={(value) =>
                        onAdaptiveModeChange(
                          studyId,
                          value as "reset" | "resume" | "warm_start"
                        )
                      }
                      data={[
                        { value: "reset", label: "Reset" },
                        { value: "resume", label: "Resume" },
                        { value: "warm_start", label: "Warm start" },
                      ]}
                    />
                  </Stack>
                </Card>
              );
            })}
          </Stack>
        )}

        <Group justify="space-between" style={{ flexShrink: 0 }}>
          <Group gap="xs">
            <input
              ref={fileInputRef}
              type="file"
              accept=".yaml,.yml,text/yaml,application/x-yaml"
              style={{ display: "none" }}
              onChange={(event) => {
                void onFileInputChange(event);
              }}
            />
            <Button
              size="xs"
              variant="light"
              onClick={() => fileInputRef.current?.click()}
            >
              Upload YAML
            </Button>
            <Text size="xs" c="dimmed">
              Upload runs checks on demand and does not auto-run.
            </Text>
          </Group>
          <Group gap="xs">
            <Button
              size="xs"
              variant="light"
              loading={loadedYamlBusy}
              disabled={!hasSequencerProcess}
              onClick={() => {
                void onShowLoadedYaml();
              }}
            >
              Show loaded YAML
            </Button>
            <Button
              size="xs"
              variant="light"
              loading={validateBusy}
              onClick={() => {
                void onValidate();
              }}
            >
              Validate + Preflight
            </Button>
            <Button
              size="xs"
              loading={loadBusy}
              onClick={() => {
                void onLoad();
              }}
            >
              Load editor YAML
            </Button>
            <Button
              size="xs"
              variant="light"
              loading={reloadSourceBusy}
              disabled={!canReloadSource || loadBusy || actionBusy}
              onClick={() => {
                void onReloadLoadedSource();
              }}
            >
              {reloadSourceLabel}
            </Button>
          </Group>
        </Group>
        <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
          Load editor YAML sends the current editor contents. Reload from source
          discards editor edits and rereads the selected library/file source.
        </Text>

        <SequencerOutlinePane
          yamlText={yamlText}
          onYamlTextChange={onYamlTextChange}
          streamCatalog={streamCatalog}
          capabilitiesByDevice={capabilitiesByDevice}
          streamWorkspaces={streamWorkspaces}
          latestSignalsByDevice={latestSignalsByDevice}
          colorScheme={colorScheme}
        />

        <Card
          radius="md"
          p="sm"
          style={{
            border: "1px solid var(--card-border)",
            flex: showFullYaml
              ? "1 1 clamp(12rem, 24vh, 18rem)"
              : "0 0 auto",
            minHeight: showFullYaml ? "clamp(12rem, 24vh, 18rem)" : 0,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Stack gap={6} style={{ flex: 1, minHeight: 0 }}>
            <Group justify="space-between" align="center">
              <Stack gap={2}>
                <Text size="sm" fw={600}>
                  Full sequence YAML
                </Text>
                <Text size="xs" c="dimmed">
                  {showFullYaml
                    ? yamlViewMode === "edit"
                      ? "Raw editable YAML"
                      : "Read-only preview with syntax highlighting"
                    : "Collapsed by default to keep the visual outline readable"}
                </Text>
              </Stack>
              <Group gap="xs" align="center">
                <SegmentedControl
                  size="xs"
                  value={yamlViewMode}
                  onChange={(value) => onYamlViewModeChange(value as "edit" | "preview")}
                  data={[
                    { value: "preview", label: "Preview" },
                    { value: "edit", label: "Edit" },
                  ]}
                />
                <ActionIcon
                  size="sm"
                  variant="subtle"
                  color="gray"
                  aria-label={showFullYaml ? "Hide full YAML" : "Show full YAML"}
                  onClick={() => setShowFullYaml((prev) => !prev)}
                >
                  {showFullYaml ? (
                    <IconChevronDown size={16} />
                  ) : (
                    <IconChevronRight size={16} />
                  )}
                </ActionIcon>
              </Group>
            </Group>

            {showFullYaml &&
              (yamlViewMode === "edit" ? (
                <Stack gap={4} style={{ flex: 1, minHeight: 0 }}>
                  <Text size="xs" c="dimmed">
                    Sequence YAML
                  </Text>
                  <div style={{ flex: 1, minHeight: 0 }}>
                    <Suspense
                      fallback={
                        <Card
                          radius="sm"
                          p="xs"
                          style={{ border: "1px solid var(--card-border)" }}
                        >
                          <Text size="xs" c="dimmed">
                            Loading YAML editor...
                          </Text>
                        </Card>
                      }
                    >
                      <LazySequencerYamlCodeEditor
                        ref={editorRef}
                        value={yamlText}
                        onChange={onYamlTextChange}
                        colorScheme={colorScheme}
                      />
                    </Suspense>
                  </div>
                </Stack>
              ) : (
                <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
                  <YamlPreview
                    text={yamlText}
                    colorScheme={colorScheme}
                    height="100%"
                  />
                </div>
              ))}
          </Stack>
        </Card>

        <Card
          radius="md"
          p="sm"
          style={{ border: "1px solid var(--card-border)", flexShrink: 0 }}
        >
          <Stack gap={6}>
            <Group justify="space-between" align="center">
              <Group gap="xs" wrap="wrap">
                <Text size="sm" fw={600}>
                  Diagnostics
                </Text>
                <Text size="xs" c="dimmed">
                  {diagnostics.length} issue{diagnostics.length === 1 ? "" : "s"}
                </Text>
              </Group>
              <ActionIcon
                size="sm"
                variant="subtle"
                color="gray"
                aria-label={
                  diagnosticsCollapsed ? "Expand diagnostics" : "Collapse diagnostics"
                }
                onClick={() => setDiagnosticsCollapsed((prev) => !prev)}
              >
                {diagnosticsCollapsed ? (
                  <IconChevronRight size={16} />
                ) : (
                  <IconChevronDown size={16} />
                )}
              </ActionIcon>
            </Group>
            {!diagnosticsCollapsed &&
              (diagnostics.length === 0 ? (
                <Text size="xs" c="dimmed">
                  No diagnostics yet. Click Validate + Preflight to check the YAML.
                </Text>
              ) : (
                <ScrollArea h={180}>
                  <Stack gap={6}>
                    {diagnostics.map((diag, idx) => {
                      const badgeColor =
                        diag.severity === "error"
                          ? "red"
                          : diag.severity === "warning"
                            ? "yellow"
                            : "gray";
                      const location =
                        diag.line != null
                          ? `L${diag.line}${diag.column != null ? `:C${diag.column}` : ""}`
                          : "No line";
                      return (
                        <Card
                          key={`${diag.source ?? "diag"}:${idx}`}
                          p="xs"
                          radius="sm"
                          style={{ border: "1px solid var(--card-border)" }}
                        >
                          <Stack gap={4}>
                            <Group justify="space-between" align="flex-start">
                              <Group gap="xs">
                                <Badge size="xs" variant="light" color={badgeColor}>
                                  {diag.severity}
                                </Badge>
                                <Text size="xs" c="dimmed">
                                  {diag.source ?? "sequencer"}
                                </Text>
                              </Group>
                              <Button
                                size="compact-xs"
                                variant="subtle"
                                color="gray"
                                disabled={diag.line == null}
                                onClick={() => {
                                  if (diag.line == null) {
                                    return;
                                  }
                                  if (!showFullYaml) {
                                    setShowFullYaml(true);
                                  }
                                  if (yamlViewMode !== "edit") {
                                    onYamlViewModeChange("edit");
                                  }
                                  window.setTimeout(() => {
                                    void onJumpToDiagnostic(
                                      diag.line ?? null,
                                      diag.column ?? null
                                    );
                                  }, 0);
                                }}
                              >
                                {location}
                              </Button>
                            </Group>
                            <Text
                              size="sm"
                              style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
                            >
                              {diag.message}
                            </Text>
                          </Stack>
                        </Card>
                      );
                    })}
                  </Stack>
                </ScrollArea>
              ))}
          </Stack>
        </Card>
      </Stack>
    </Modal>
  );
}
