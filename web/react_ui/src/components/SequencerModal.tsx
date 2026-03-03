import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Group,
  Modal,
  Progress,
  ScrollArea,
  SegmentedControl,
  Stack,
  Text,
  Textarea,
} from "@mantine/core";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
import { useEffect, useState, type ChangeEvent, type ReactNode, type RefObject } from "react";
import {
  processStateColor,
  sequencerRuntimeStateColor,
} from "../features/runtime/helpers";
import { formatDurationCompact } from "../features/sequencer/utils";
import type {
  SequencerAdaptiveStudyStatus,
  SequencerDiagnostic,
  SequencerProgress,
} from "../features/sequencer/types";
import type { CapabilityMember } from "../types";
import { SequencerOutlinePane } from "./SequencerOutlinePane";
import { YamlPreview } from "./YamlPreview";

type Props = {
  opened: boolean;
  onClose: () => void;
  processState: string;
  runtimeState: string;
  loaded: boolean;
  currentStep: string | null;
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
  editorRef: RefObject<HTMLTextAreaElement>;
  yamlText: string;
  onYamlTextChange: (value: string) => void;
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
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
  editorRef,
  yamlText,
  onYamlTextChange,
  capabilitiesByDevice,
  colorScheme,
  diagnostics,
  onJumpToDiagnostic,
}: Props) {
  const [showFullYaml, setShowFullYaml] = useState(false);
  const [diagnosticsCollapsed, setDiagnosticsCollapsed] = useState(false);

  useEffect(() => {
    if (opened) {
      setShowFullYaml(false);
      setDiagnosticsCollapsed(false);
    }
  }, [opened]);

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Sequencer"
      size="clamp(56rem, 92vw, 96rem)"
      centered
      zIndex={440}
    >
      <Stack gap="sm" style={{ height: "clamp(42rem, 88vh, 78rem)", minHeight: 0 }}>
        <Group justify="space-between" align="flex-start">
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
              <Text size="xs" c="dimmed">
                Current step: {currentStep}
              </Text>
            )}
            {progress && (
              <Stack gap={4}>
                {progressPercent !== null && (
                  <Progress value={progressPercent} size="sm" radius="xl" />
                )}
                <Text size="xs" c="dimmed">
                  {totalSteps !== null
                    ? `Progress: ${completedSteps ?? 0}/${totalSteps} (${(progressPercent ?? 0).toFixed(1)}%)`
                    : `Completed steps: ${completedSteps ?? 0}`}
                </Text>
                <Text size="xs" c="dimmed">
                  Elapsed: {formatDurationCompact(progress.elapsedS)}
                  {progress.etaS !== null
                    ? `  ETA: ${formatDurationCompact(progress.etaS)}`
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
              <Text size="xs" c="red">
                {statusError}
              </Text>
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

        <Group justify="space-between">
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
              Upload validates on demand and does not auto-run.
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
              Validate
            </Button>
            <Button
              size="xs"
              loading={loadBusy}
              onClick={() => {
                void onLoad();
              }}
            >
              Load
            </Button>
          </Group>
        </Group>

        <SequencerOutlinePane
          yamlText={yamlText}
          onYamlTextChange={onYamlTextChange}
          capabilitiesByDevice={capabilitiesByDevice}
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
                <Textarea
                  ref={editorRef}
                  label="Sequence YAML"
                  placeholder="Paste or upload sequence YAML"
                  rows={12}
                  value={yamlText}
                  onChange={(event) => onYamlTextChange(event.currentTarget.value)}
                  style={{ flex: 1, minHeight: 0 }}
                  styles={{
                    root: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0 },
                    input: {
                      height: "100%",
                      minHeight: "100%",
                      overflowY: "auto",
                    },
                  }}
                />
              ) : (
                <YamlPreview
                  text={yamlText}
                  colorScheme={colorScheme}
                  height="100%"
                />
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
                  No diagnostics yet. Click Validate to check the YAML.
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
                                  void onJumpToDiagnostic(
                                    diag.line ?? null,
                                    diag.column ?? null
                                  );
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
