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
  Tabs,
  Text,
  TextInput,
  UnstyledButton,
} from "@mantine/core";
import { IconChevronDown, IconChevronRight, IconTrash } from "@tabler/icons-react";
import {
  Suspense,
  lazy,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type ChangeEvent,
  type ReactNode,
  type RefObject,
} from "react";
import {
  processStateColor,
  sequencerRuntimeStateColor,
} from "../features/runtime/helpers";
import {
  formatDurationCompact,
  formatSequencerEta,
} from "../features/sequencer/utils";
import type { StreamAnalysisWorkspaceConfig } from "../features/stream/types";
import { SequencerMetadataPanel } from "../features/sequencer/components/SequencerMetadataPanel";
import {
  buildSequencerOutlineMetadata,
  buildSequencerStepOutline,
} from "../features/sequencer/outline";
import { stepPathAtLine } from "../features/sequencer/diagnostic_locations";
import type {
  SequencerAdaptiveStudyStatus,
  SequencerDiagnostic,
  SequencerErrorDetail,
  SequencerOutlineMetadata,
  SequencerProgress,
  SequencerStepDetail,
  SequencerYamlEditorHandle,
} from "../features/sequencer/types";
import type { CapabilityMember } from "../types";
import type { StreamCatalogEntry } from "../types";
import type { TelemetrySignal } from "../types";
import { SequencerOutlinePane } from "./SequencerOutlinePane";

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
  /** What the editor text came from (file name or loaded source). */
  editorLabel: string | null;
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
  editorLabel,
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
  const [activeTab, setActiveTab] = useState<"steps" | "vars" | "yaml">("steps");
  const [overridesOpen, setOverridesOpen] = useState(false);

  useEffect(() => {
    if (opened) {
      setOverridesOpen(overrideRows.length > 0);
    }
    // Only when the modal opens; later toggles are the operator's.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened]);

  const metadata = useMemo<SequencerOutlineMetadata>(() => {
    try {
      return buildSequencerOutlineMetadata(yamlText);
    } catch {
      return { version: null, vars: [], contextColumns: [] };
    }
  }, [yamlText]);

  const libraryOptions = libraryEntries.map((entry) => ({
    value: entry.id,
    label: entry.label ? `${entry.label} (${entry.id})` : entry.id,
  }));
  const selectedLibraryEntry = selectedSequenceId
    ? libraryEntries.find((entry) => entry.id === selectedSequenceId) ?? null
    : null;
  const errorCount = diagnostics.filter((diag) => diag.severity === "error").length;
  const warningCount = diagnostics.filter((diag) => diag.severity === "warning").length;
  const anyStale = diagnostics.some((diag) => diag.stale);
  const [stepFocus, setStepFocus] = useState<{ line: number; nonce: number } | null>(null);
  const outline = useMemo(() => {
    try {
      return buildSequencerStepOutline(yamlText);
    } catch {
      return [];
    }
  }, [yamlText]);

  const jumpToDiagnostic = (diag: SequencerDiagnostic) => {
    if (diag.line == null) {
      return;
    }
    // Show it on its step when it points at one in the current text;
    // otherwise (stale, or outside any step) at its line in the YAML.
    if (!diag.stale && stepPathAtLine(outline, diag.line).length > 0) {
      setActiveTab("steps");
      setStepFocus({ line: diag.line, nonce: Date.now() });
      return;
    }
    setActiveTab("yaml");
    if (yamlViewMode !== "edit") {
      onYamlViewModeChange("edit");
    }
    window.setTimeout(() => {
      void onJumpToDiagnostic(diag.line ?? null, diag.column ?? null);
    }, 0);
  };

  const progressText = !progress
    ? null
    : progress.phase === "cleanup"
      ? `Cleanup ${progress.cleanupCompletedSteps ?? 0}/${progress.cleanupTotalSteps ?? "?"}`
      : totalSteps !== null
        ? `${progress.scope === "loop" ? "Loop " : ""}${completedSteps ?? 0}/${
            progress.approximate ? "~" : ""
          }${totalSteps} steps`
        : `${completedSteps ?? 0} steps`;
  const etaText = progress?.etaS != null ? formatSequencerEta(progress) : null;

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Sequencer"
      size="calc(100vw - 3rem)"
      centered
      zIndex={440}
      styles={{
        // The modal fills the window; its body takes what the header leaves.
        content: { height: "calc(100dvh - 3rem)", display: "flex", flexDirection: "column" },
        body: { flex: 1, minHeight: 0, display: "flex", flexDirection: "column", paddingTop: 0 },
      }}
    >
      <Stack gap="sm" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
        {/* Status + run: one row. */}
        <Group justify="space-between" align="center" wrap="nowrap" gap="md" style={{ flexShrink: 0 }}>
          <Group gap="xs" wrap="wrap" style={{ minWidth: 0 }}>
            <Badge variant="light" color={processStateColor(processState)}>
              Process {processState}
            </Badge>
            <Badge variant="light" color={sequencerRuntimeStateColor(runtimeState, processState)}>
              {runtimeState}
            </Badge>
            <Badge variant="outline" color={loaded ? "teal" : "gray"}>
              {loaded ? "Loaded" : "Not loaded"}
            </Badge>
            {yamlDirty && (
              <Badge variant="light" color="yellow">
                Editor changes not loaded
              </Badge>
            )}
            {errorCount > 0 && (
              <Badge variant="filled" color="red">
                {errorCount} error{errorCount === 1 ? "" : "s"}
              </Badge>
            )}
            {warningCount > 0 && (
              <Badge variant="light" color="yellow">
                {warningCount} warning{warningCount === 1 ? "" : "s"}
              </Badge>
            )}
            {cleanupActive && (
              <Badge variant="light" color="yellow">
                cleanup/finally
              </Badge>
            )}
          </Group>
          {progress && (
            <Stack gap={2} style={{ flex: 1, minWidth: "12rem", maxWidth: "36rem" }}>
              {progressPercent !== null && (
                <Progress value={progressPercent} size="sm" radius="xl" />
              )}
              <Text size="xs" c="dimmed" truncate>
                {[
                  progressText,
                  progressPercent !== null
                    ? `${progressPercent.toFixed(1)}%${progress.timePercent !== null ? " of time" : ""}`
                    : null,
                  `elapsed ${formatDurationCompact(progress.elapsedS)}`,
                  etaText ? `${progress.phase === "cleanup" ? "cleanup " : ""}ETA ${etaText}` : null,
                  progress.loopsTarget !== null
                    ? `loop ${Math.min((progress.loopsCompleted ?? 0) + 1, progress.loopsTarget)}/${progress.loopsTarget}`
                    : progress.loopMode === "continuous"
                      ? `${progress.loopsCompleted ?? 0} loops done`
                      : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </Text>
            </Stack>
          )}
          <Group gap="xs" wrap="nowrap" style={{ flexShrink: 0 }}>
            <SegmentedControl
              size="xs"
              value={runMode}
              onChange={(value) => onRunModeChange(value as "once" | "repeat" | "continuous")}
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
                aria-label="Repeat count"
                onChange={(value) =>
                  onRepeatCountChange(
                    typeof value === "number" && Number.isFinite(value)
                      ? Math.max(1, Math.trunc(value))
                      : 1
                  )
                }
                w={80}
              />
            )}
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

        {/* Current step and messages, only when there is something to say. */}
        {(currentStep || progress?.approximate || autoloadError || statusError || modalError) && (
          <Stack gap={2} style={{ flexShrink: 0 }}>
            {currentStep && (
              <Text size="xs" c="dimmed" truncate>
                Current step: {currentStepDetail?.summary ?? currentStep}
                {currentStepDetail
                  ? ` (${[
                      currentStepDetail.line !== null ? `line ${currentStepDetail.line}` : null,
                      currentStepDetail.path,
                      currentStepDetail.branch,
                    ]
                      .filter(Boolean)
                      .join(" | ")})`
                  : ""}
              </Text>
            )}
            {progress?.approximate && progress.estimateReason && (
              <Text size="xs" c="dimmed">
                Estimate: {progress.estimateReason}
              </Text>
            )}
            {autoloadError && (
              <Text size="xs" c="red">
                Autoload failed: {autoloadError}
              </Text>
            )}
            {statusError && (
              <Text size="xs" c="red">
                {errorDetail?.formatted ?? statusError}
                {errorDetail?.step
                  ? ` (${[
                      errorDetail.step.line !== null ? `line ${errorDetail.step.line}` : null,
                      errorDetail.step.path,
                      errorDetail.step.branch,
                    ]
                      .filter(Boolean)
                      .join(" | ")})`
                  : ""}
              </Text>
            )}
            {modalError && (
              <Text size="xs" c="red">
                {modalError}
              </Text>
            )}
          </Stack>
        )}

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(15rem, 18rem) minmax(0, 1fr)",
            gap: 12,
            flex: 1,
            minHeight: 0,
          }}
        >
          {/* Sidebar: source, load/check, overrides, adaptive, diagnostics. */}
          <ScrollArea style={{ minHeight: 0 }} type="auto" offsetScrollbars>
            <Stack gap="sm">
              <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
                <Stack gap={6}>
                  <Text size="sm" fw={600}>
                    Sequence
                  </Text>
                  {libraryConfigured || libraryOptions.length > 0 ? (
                    <>
                      <Select
                        size="xs"
                        placeholder={`Library (${libraryEntries.length})`}
                        data={libraryOptions}
                        value={selectedSequenceId}
                        onChange={(value) => onSelectedSequenceIdChange(value)}
                        searchable
                        clearable
                        comboboxProps={{ zIndex: 500 }}
                        disabled={libraryLoading}
                      />
                      {selectedLibraryEntry?.description && (
                        <Text size="xs" c="dimmed" lineClamp={3}>
                          {selectedLibraryEntry.description}
                        </Text>
                      )}
                      {libraryError && (
                        <Text size="xs" c="red">
                          {libraryError}
                        </Text>
                      )}
                      <Group gap="xs" grow>
                        <Button
                          size="xs"
                          variant="light"
                          loading={loadBusy}
                          disabled={!selectedSequenceId || libraryLoading}
                          onClick={() => {
                            void onLoadSelectedLibrary();
                          }}
                        >
                          Load selected
                        </Button>
                        <Button
                          size="xs"
                          variant="subtle"
                          color="gray"
                          loading={libraryLoading}
                          onClick={() => {
                            void onReloadLibrary();
                          }}
                        >
                          Reload list
                        </Button>
                      </Group>
                    </>
                  ) : (
                    <Text size="xs" c="dimmed">
                      No sequence library configured for this sequencer
                      (`sequence_library_path`).
                    </Text>
                  )}
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".yaml,.yml,text/yaml,application/x-yaml"
                    style={{ display: "none" }}
                    onChange={(event) => {
                      void onFileInputChange(event);
                    }}
                  />
                  <Group gap="xs" grow>
                    <Button size="xs" variant="light" onClick={() => fileInputRef.current?.click()}>
                      Upload YAML
                    </Button>
                    <Button
                      size="xs"
                      variant="light"
                      loading={loadedYamlBusy}
                      disabled={!hasSequencerProcess}
                      onClick={() => {
                        void onShowLoadedYaml();
                      }}
                    >
                      Show loaded
                    </Button>
                  </Group>
                  <Stack gap={2}>
                    <Text size="xs" style={{ wordBreak: "break-all" }}>
                      <Text span size="xs" c="dimmed">
                        In editor:{" "}
                      </Text>
                      {editorLabel ?? (yamlText.trim() ? "unsaved text" : "empty")}
                      {yamlDirty ? (
                        <Text span size="xs" c="yellow">
                          {" "}
                          (not loaded)
                        </Text>
                      ) : null}
                    </Text>
                    <Text size="xs" style={{ wordBreak: "break-all" }}>
                      <Text span size="xs" c="dimmed">
                        In sequencer:{" "}
                      </Text>
                      {loaded ? loadedSource ?? "loaded (source unknown)" : "nothing loaded"}
                    </Text>
                  </Stack>
                </Stack>
              </Card>

              <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
                <Stack gap={6}>
                  <Group justify="space-between">
                    <Text size="sm" fw={600}>
                      Editor
                    </Text>
                    {yamlDirty && (
                      <Badge size="xs" variant="light" color="yellow">
                        not loaded
                      </Badge>
                    )}
                  </Group>
                  <Button
                    size="xs"
                    loading={loadBusy}
                    onClick={() => {
                      void onLoad();
                    }}
                  >
                    Load editor YAML
                  </Button>
                  <Stack gap={6}>
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
                      variant="light"
                      loading={reloadSourceBusy}
                      disabled={!canReloadSource || loadBusy || actionBusy}
                      onClick={() => {
                        void onReloadLoadedSource();
                      }}
                    >
                      {reloadSourceLabel}
                    </Button>
                  </Stack>
                  <Text size="xs" c="dimmed">
                    Edits apply to the runtime only after Load editor YAML.
                    Reload discards them and rereads the source.
                  </Text>
                </Stack>
              </Card>

              <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
                <Stack gap={6}>
                  <Group justify="space-between" align="center">
                    <UnstyledButton onClick={() => setOverridesOpen((prev) => !prev)}>
                      <Group gap={4}>
                        {overridesOpen ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
                        <Text size="sm" fw={600}>
                          Run overrides
                        </Text>
                        {overrideRows.length > 0 && (
                          <Badge size="xs" variant="light" color={overridesValid ? "blue" : "red"}>
                            {overrideRows.length}
                            {overridesValid ? "" : " invalid"}
                          </Badge>
                        )}
                      </Group>
                    </UnstyledButton>
                    <Group gap={4}>
                      <Button
                        size="compact-xs"
                        variant="subtle"
                        color="gray"
                        onClick={() => {
                          onAddOverrideRow();
                          setOverridesOpen(true);
                        }}
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
                  <Collapse in={overridesOpen}>
                    <Stack gap={6}>
                      <Text size="xs" c="dimmed">
                        Applied to the next Start only (`vars_override`), not saved to YAML.
                      </Text>
                      {overrideRows.map((row) => (
                        <Stack key={row.id} gap={4}>
                          <Group gap={4} wrap="nowrap" align="flex-end">
                            {overrideVarOptions.length > 0 ? (
                              <Select
                                size="xs"
                                aria-label="Variable"
                                placeholder="variable"
                                data={overrideVarOptions.map((item) => ({ value: item, label: item }))}
                                value={row.name || null}
                                onChange={(value) => onUpdateOverrideRow(row.id, { name: value ?? "" })}
                                searchable
                                comboboxProps={{ zIndex: 500 }}
                                style={{ flex: 1, minWidth: 0 }}
                              />
                            ) : (
                              <TextInput
                                size="xs"
                                aria-label="Variable"
                                placeholder="variable"
                                value={row.name}
                                onChange={(event) =>
                                  onUpdateOverrideRow(row.id, { name: event.currentTarget.value })
                                }
                                style={{ flex: 1, minWidth: 0 }}
                              />
                            )}
                            <Select
                              size="xs"
                              aria-label="Type"
                              data={["number", "bool", "string", "json", "null"]}
                              value={row.valueType}
                              onChange={(value) =>
                                onUpdateOverrideRow(row.id, {
                                  valueType:
                                    (value as SequencerOverrideRow["valueType"] | null) ?? "string",
                                })
                              }
                              comboboxProps={{ zIndex: 500 }}
                              w={84}
                            />
                            <ActionIcon
                              size="md"
                              variant="subtle"
                              color="red"
                              aria-label="Remove override"
                              onClick={() => onRemoveOverrideRow(row.id)}
                            >
                              <IconTrash size={14} />
                            </ActionIcon>
                          </Group>
                          {row.valueType === "bool" ? (
                            <SegmentedControl
                              size="xs"
                              value={row.valueText === "false" ? "false" : "true"}
                              onChange={(value) => onUpdateOverrideRow(row.id, { valueText: value })}
                              data={["true", "false"]}
                            />
                          ) : row.valueType !== "null" ? (
                            <TextInput
                              size="xs"
                              aria-label="Value"
                              value={row.valueText}
                              onChange={(event) =>
                                onUpdateOverrideRow(row.id, { valueText: event.currentTarget.value })
                              }
                              placeholder={
                                row.valueType === "json" ? '{"key": 1}' : row.valueType === "number" ? "1.23" : "text"
                              }
                              styles={{
                                input:
                                  row.valueType === "json"
                                    ? { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }
                                    : undefined,
                              }}
                            />
                          ) : null}
                          {overrideErrors[row.id] && (
                            <Text size="xs" c="red">
                              {overrideErrors[row.id]}
                            </Text>
                          )}
                        </Stack>
                      ))}
                      {overrideVarOptions.length === 0 && overrideRows.length > 0 && (
                        <Text size="xs" c="yellow">
                          Variable list unavailable. Enter names manually.
                        </Text>
                      )}
                      {overrideRows.length > 0 && (
                        <Text
                          size="xs"
                          c="dimmed"
                          style={{
                            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                            whiteSpace: "pre-wrap",
                            wordBreak: "break-all",
                          }}
                        >
                          {overridePreview || "{}"}
                        </Text>
                      )}
                    </Stack>
                  </Collapse>
                </Stack>
              </Card>

              {loadedAdaptiveIds.length > 0 && (
                <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
                  <Stack gap={6}>
                    <Text size="sm" fw={600}>
                      Adaptive reuse
                    </Text>
                    <Text size="xs" c="dimmed">
                      How each adaptive study starts when you press Start.
                    </Text>
                    {loadedAdaptiveIds.map((studyId) => {
                      const status = adaptiveStudies[studyId];
                      const trialCount = status?.trialCount ?? 0;
                      const hasSaved = trialCount > 0;
                      return (
                        <Stack key={studyId} gap={4}>
                          <Group justify="space-between" wrap="nowrap">
                            <Group gap={4} wrap="wrap" style={{ minWidth: 0 }}>
                              <Text size="xs" fw={600} truncate>
                                {studyId}
                              </Text>
                              <Badge size="xs" variant="light" color={hasSaved ? "teal" : "gray"}>
                                {hasSaved ? `${trialCount} trial${trialCount === 1 ? "" : "s"}` : "no data"}
                              </Badge>
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
                              Clear
                            </Button>
                          </Group>
                          <SegmentedControl
                            size="xs"
                            fullWidth
                            value={adaptiveModes[studyId] ?? "reset"}
                            onChange={(value) =>
                              onAdaptiveModeChange(studyId, value as "reset" | "resume" | "warm_start")
                            }
                            data={[
                              { value: "reset", label: "Reset" },
                              { value: "resume", label: "Resume" },
                              { value: "warm_start", label: "Warm" },
                            ]}
                          />
                        </Stack>
                      );
                    })}
                  </Stack>
                </Card>
              )}

              <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
                <Stack gap={6}>
                  <Group gap="xs">
                    <Text size="sm" fw={600}>
                      Diagnostics
                    </Text>
                    {diagnostics.length > 0 && (
                      <Badge size="xs" variant="light" color={errorCount > 0 ? "red" : "yellow"}>
                        {diagnostics.length}
                      </Badge>
                    )}
                  </Group>
                  {anyStale && (
                    <Text size="xs" c="yellow">
                      The YAML changed since the last check; dimmed entries
                      may point at the wrong line. Validate again.
                    </Text>
                  )}
                  {diagnostics.length === 0 ? (
                    <Text size="xs" c="dimmed">
                      None yet. Validate + Preflight checks the YAML.
                    </Text>
                  ) : (
                    diagnostics.map((diag, idx) => (
                      <Stack
                        key={`${diag.source ?? "diag"}:${idx}`}
                        gap={2}
                        style={{ opacity: diag.stale ? 0.5 : 1 }}
                      >
                        <Group gap={4} justify="space-between" wrap="nowrap">
                          <Group gap={4} wrap="nowrap">
                            <Badge
                              size="xs"
                              variant="light"
                              color={diag.severity === "error" ? "red" : diag.severity === "warning" ? "yellow" : "gray"}
                            >
                              {diag.severity}
                            </Badge>
                            <Text size="xs" c="dimmed" truncate>
                              {diag.source ?? "sequencer"}
                            </Text>
                          </Group>
                          <Button
                            size="compact-xs"
                            variant="subtle"
                            color="gray"
                            disabled={diag.line == null}
                            onClick={() => jumpToDiagnostic(diag)}
                          >
                            {diag.line != null
                              ? `L${diag.line}${diag.column != null ? `:${diag.column}` : ""}`
                              : "no line"}
                          </Button>
                        </Group>
                        <Text size="xs" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                          {diag.message}
                        </Text>
                      </Stack>
                    ))
                  )}
                </Stack>
              </Card>
            </Stack>
          </ScrollArea>

          {/* Main: steps / variables / YAML, each using the full height. */}
          <Tabs
            value={activeTab}
            onChange={(value) => setActiveTab((value as typeof activeTab) ?? "steps")}
            keepMounted
            style={{ display: "flex", flexDirection: "column", minHeight: 0 }}
          >
            <Tabs.List>
              <Tabs.Tab value="steps">Steps</Tabs.Tab>
              <Tabs.Tab
                value="vars"
                rightSection={
                  <Badge size="xs" variant="light" color="gray">
                    {metadata.vars.length}
                  </Badge>
                }
              >
                Variables
              </Tabs.Tab>
              <Tabs.Tab value="yaml">YAML</Tabs.Tab>
            </Tabs.List>
            <Tabs.Panel value="steps" pt="sm" style={tabPanelStyle(activeTab === "steps")}>
              <SequencerOutlinePane
                yamlText={yamlText}
                onYamlTextChange={onYamlTextChange}
                streamCatalog={streamCatalog}
                capabilitiesByDevice={capabilitiesByDevice}
                streamWorkspaces={streamWorkspaces}
                latestSignalsByDevice={latestSignalsByDevice}
                colorScheme={colorScheme}
                diagnostics={diagnostics}
                focusRequest={stepFocus}
              />
            </Tabs.Panel>
            <Tabs.Panel value="vars" pt="sm" style={tabPanelStyle(activeTab === "vars")}>
              <SequencerMetadataPanel
                metadata={metadata}
                metadataCollapsed={false}
                onToggleCollapsed={() => undefined}
                yamlText={yamlText}
                onYamlTextChange={onYamlTextChange}
                fill
              />
            </Tabs.Panel>
            <Tabs.Panel value="yaml" pt="sm" style={tabPanelStyle(activeTab === "yaml")}>
              <Stack gap={6} style={{ flex: 1, minHeight: 0 }}>
                <Group justify="space-between">
                  <Text size="xs" c="dimmed">
                    {yamlViewMode === "edit" ? "Raw editable YAML" : "Read-only preview"}
                  </Text>
                  <SegmentedControl
                    size="xs"
                    value={yamlViewMode}
                    onChange={(value) => onYamlViewModeChange(value as "edit" | "preview")}
                    data={[
                      { value: "preview", label: "Preview" },
                      { value: "edit", label: "Edit" },
                    ]}
                  />
                </Group>
                {/* A flex column, so the editor gets a bounded height and scrolls. */}
                <div
                  style={{
                    flex: 1,
                    minHeight: 0,
                    overflow: "hidden",
                    display: "flex",
                    flexDirection: "column",
                  }}
                >
                  {/* Preview is the same editor, read-only, so it shows the
                      diagnostic marks too. */}
                  <Suspense
                    fallback={
                      <Text size="xs" c="dimmed">
                        Loading YAML editor...
                      </Text>
                    }
                  >
                    <LazySequencerYamlCodeEditor
                      ref={editorRef}
                      value={yamlText}
                      onChange={onYamlTextChange}
                      colorScheme={colorScheme}
                      diagnostics={diagnostics}
                      readOnly={yamlViewMode !== "edit"}
                    />
                  </Suspense>
                </div>
              </Stack>
            </Tabs.Panel>
          </Tabs>
        </div>
      </Stack>
    </Modal>
  );
}

function tabPanelStyle(active: boolean): CSSProperties {
  return active
    ? { flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }
    : { display: "none" };
}
