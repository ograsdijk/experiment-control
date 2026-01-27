import {
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
import type { ChangeEvent, ReactNode, RefObject } from "react";
import {
  processStateColor,
  sequencerRuntimeStateColor,
} from "../features/runtime/helpers";
import { formatDurationCompact } from "../features/sequencer/utils";
import type {
  SequencerDiagnostic,
  SequencerProgress,
} from "../features/sequencer/types";
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
  onRunAction: (
    action: "start" | "pause" | "resume" | "stop"
  ) => Promise<unknown> | void;
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
  onRunAction,
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
  colorScheme,
  diagnostics,
  onJumpToDiagnostic,
}: Props) {
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Sequencer"
      size="xl"
      centered
      zIndex={440}
    >
      <Stack gap="sm">
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
            <SegmentedControl
              size="xs"
              value={yamlViewMode}
              onChange={(value) => onYamlViewModeChange(value as "edit" | "preview")}
              data={[
                { value: "preview", label: "Preview" },
                { value: "edit", label: "Edit" },
              ]}
            />
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

        {yamlViewMode === "edit" ? (
          <Textarea
            ref={editorRef}
            label="Sequence YAML"
            placeholder="Paste or upload sequence YAML"
            autosize
            minRows={14}
            maxRows={26}
            value={yamlText}
            onChange={(event) => onYamlTextChange(event.currentTarget.value)}
          />
        ) : (
          <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
            <Stack gap={6}>
              <Group justify="space-between">
                <Text size="sm" fw={600}>
                  Sequence YAML
                </Text>
                <Text size="xs" c="dimmed">
                  Read-only preview with syntax highlighting
                </Text>
              </Group>
              <YamlPreview text={yamlText} colorScheme={colorScheme} />
            </Stack>
          </Card>
        )}

        <Stack gap={6}>
          <Group justify="space-between">
            <Text size="sm" fw={600}>
              Diagnostics
            </Text>
            <Text size="xs" c="dimmed">
              {diagnostics.length} issue{diagnostics.length === 1 ? "" : "s"}
            </Text>
          </Group>
          {diagnostics.length === 0 && (
            <Text size="xs" c="dimmed">
              No diagnostics yet. Click Validate to check the YAML.
            </Text>
          )}
          {diagnostics.length > 0 && (
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
                        <Text size="sm" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                          {diag.message}
                        </Text>
                      </Stack>
                    </Card>
                  );
                })}
              </Stack>
            </ScrollArea>
          )}
        </Stack>
      </Stack>
    </Modal>
  );
}
