import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Collapse,
  Group,
  ScrollArea,
  Select,
  SegmentedControl,
  Stack,
  Text,
  TextInput,
  UnstyledButton,
} from "@mantine/core";
import { IconChevronDown, IconChevronRight, IconTrash } from "@tabler/icons-react";
import {
  memo,
  useMemo,
  type ChangeEvent,
  type Dispatch,
  type RefObject,
  type SetStateAction,
} from "react";
import type {
  SequencerAdaptiveStudyStatus,
  SequencerDiagnostic,
  SequencerRunEvent,
} from "../features/sequencer/types";
import { formatDurationCompact, runEventToDiagnostic } from "../features/sequencer/utils";
import { perfCount } from "../features/performance/perfInstrumentation";

export type SequencerLibraryEntry = {
  id: string;
  label: string | null;
  description: string | null;
  path: string | null;
  source: string | null;
  vars: string[];
};

export type SequencerOverrideRow = {
  id: string;
  name: string;
  valueType: "number" | "bool" | "string" | "json" | "null";
  valueText: string;
};

type Props = {
  libraryConfigured: boolean;
  libraryEntries: SequencerLibraryEntry[];
  libraryLoading: boolean;
  libraryError: string | null;
  selectedSequenceId: string | null;
  onSelectedSequenceIdChange: (sequenceId: string | null) => void;
  onReloadLibrary: () => Promise<unknown> | void;
  onLoadSelectedLibrary: () => Promise<unknown> | void;
  loadBusy: boolean;
  fileInputRef: RefObject<HTMLInputElement>;
  onFileInputChange: (event: ChangeEvent<HTMLInputElement>) => Promise<unknown> | void;
  loadedYamlBusy: boolean;
  hasSequencerProcess: boolean;
  onShowLoadedYaml: () => Promise<unknown> | void;
  editorLabel: string | null;
  /** The editor holds no text (a boolean, so typing does not re-render this). */
  editorEmpty: boolean;
  yamlDirty: boolean;
  loaded: boolean;
  loadedSource: string | null;
  onLoad: () => Promise<unknown> | void;
  validateBusy: boolean;
  onValidate: () => Promise<unknown> | void;
  reloadSourceBusy: boolean;
  canReloadSource: boolean;
  actionBusy: boolean;
  reloadSourceLabel: string;
  onReloadLoadedSource: () => Promise<unknown> | void;
  overridesOpen: boolean;
  setOverridesOpen: Dispatch<SetStateAction<boolean>>;
  overrideRows: SequencerOverrideRow[];
  overrideVarOptions: string[];
  overrideErrors: Record<string, string | null>;
  overridePreview: string;
  overridesValid: boolean;
  onAddOverrideRow: () => void;
  onRemoveOverrideRow: (rowId: string) => void;
  onUpdateOverrideRow: (
    rowId: string,
    patch: Partial<Pick<SequencerOverrideRow, "name" | "valueType" | "valueText">>
  ) => void;
  onClearOverrides: () => void;
  loadedAdaptiveIds: readonly string[];
  adaptiveStudies: Record<string, SequencerAdaptiveStudyStatus>;
  adaptiveModes: Record<string, "reset" | "resume" | "warm_start">;
  adaptiveClearBusyStudyId: string | null;
  onAdaptiveModeChange: (studyId: string, mode: "reset" | "resume" | "warm_start") => void;
  onClearAdaptiveStudy: (studyId: string) => Promise<unknown> | void;
  diagnostics: ReadonlyArray<SequencerDiagnostic>;
  runEvents: ReadonlyArray<SequencerRunEvent>;
  /** The editor holds text other than the loaded sequence (run lines may not match). */
  textNotLoaded: boolean;
  onJumpToDiagnostic: (diag: SequencerDiagnostic) => void;
};

/**
 * The Sequencer modal's sidebar: source, load/check, overrides, adaptive
 * reuse, diagnostics and run events. Memoized and given no editor text, so
 * typing in the YAML editor does not re-render it.
 */
export const SequencerSidebar = memo(function SequencerSidebar({
  libraryConfigured,
  libraryEntries,
  libraryLoading,
  libraryError,
  selectedSequenceId,
  onSelectedSequenceIdChange,
  onReloadLibrary,
  onLoadSelectedLibrary,
  loadBusy,
  fileInputRef,
  onFileInputChange,
  loadedYamlBusy,
  hasSequencerProcess,
  onShowLoadedYaml,
  editorLabel,
  editorEmpty,
  yamlDirty,
  loaded,
  loadedSource,
  onLoad,
  validateBusy,
  onValidate,
  reloadSourceBusy,
  canReloadSource,
  actionBusy,
  reloadSourceLabel,
  onReloadLoadedSource,
  overridesOpen,
  setOverridesOpen,
  overrideRows,
  overrideVarOptions,
  overrideErrors,
  overridePreview,
  overridesValid,
  onAddOverrideRow,
  onRemoveOverrideRow,
  onUpdateOverrideRow,
  onClearOverrides,
  loadedAdaptiveIds,
  adaptiveStudies,
  adaptiveModes,
  adaptiveClearBusyStudyId,
  onAdaptiveModeChange,
  onClearAdaptiveStudy,
  diagnostics,
  runEvents,
  textNotLoaded,
  onJumpToDiagnostic: jumpToDiagnostic,
}: Props) {
  perfCount("react.SequencerSidebar.renders");
  const libraryOptions = useMemo(
    () =>
      libraryEntries.map((entry) => ({
        value: entry.id,
        label: entry.label ? `${entry.label} (${entry.id})` : entry.id,
      })),
    [libraryEntries]
  );
  const selectedLibraryEntry = selectedSequenceId
    ? libraryEntries.find((entry) => entry.id === selectedSequenceId) ?? null
    : null;
  const errorCount = diagnostics.filter((diag) => diag.severity === "error").length;
  const anyStale = diagnostics.some((diag) => diag.stale);
  const runErrorCount = runEvents.filter((event) => event.severity === "error").length;
  const runWarningCount = runEvents.filter((event) => event.severity === "warning").length;

  return (
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
                {editorLabel ?? (editorEmpty ? "empty" : "unsaved text")}
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

        <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
          <Stack gap={6}>
            <Group gap="xs">
              <Text size="sm" fw={600}>
                This run
              </Text>
              {runErrorCount > 0 && (
                <Badge size="xs" variant="filled" color="red">
                  {runErrorCount} error{runErrorCount === 1 ? "" : "s"}
                </Badge>
              )}
              {runWarningCount > 0 && (
                <Badge size="xs" variant="light" color="yellow">
                  {runWarningCount} warning{runWarningCount === 1 ? "" : "s"}
                </Badge>
              )}
            </Group>
            {runEvents.length === 0 ? (
              <Text size="xs" c="dimmed">
                Pauses, failures and warnings of the current run appear here.
              </Text>
            ) : (
              [...runEvents].reverse().map((event, idx) => {
                const asDiag = runEventToDiagnostic(event, textNotLoaded);
                return (
                  <Stack
                    key={`run:${runEvents.length - idx}`}
                    gap={2}
                    style={{ opacity: textNotLoaded ? 0.6 : 1 }}
                  >
                    <Group gap={4} justify="space-between" wrap="nowrap">
                      <Group gap={4} wrap="nowrap" style={{ minWidth: 0 }}>
                        <Badge
                          size="xs"
                          variant="light"
                          color={
                            event.severity === "error"
                              ? "red"
                              : event.severity === "warning"
                                ? "yellow"
                                : "gray"
                          }
                        >
                          {event.kind.replace("_", " ")}
                        </Badge>
                        <Text size="xs" c="dimmed" truncate>
                          {formatDurationCompact(event.elapsedS)}
                          {event.count > 1 ? ` · x${event.count}` : ""}
                          {event.source ? ` · ${event.source}` : ""}
                        </Text>
                      </Group>
                      {event.step?.line != null && (
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          color="gray"
                          onClick={() => jumpToDiagnostic(asDiag)}
                        >
                          L{event.step.line}
                        </Button>
                      )}
                    </Group>
                    <Text size="xs" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                      {event.message}
                    </Text>
                    {event.step?.summary && (
                      <Text size="xs" c="dimmed" truncate>
                        at {event.step.summary}
                      </Text>
                    )}
                  </Stack>
                );
              })
            )}
          </Stack>
        </Card>
      </Stack>
    </ScrollArea>
  );
});
