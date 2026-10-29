import {
  ActionIcon,
  Badge,
  Button,
  Group,
  Modal,
  NumberInput,
  Progress,
  SegmentedControl,
  Stack,
  Switch,
  Tabs,
  Text,
  Title,
  Tooltip,
} from "@mantine/core";
import { IconExternalLink } from "@tabler/icons-react";
import {
  Suspense,
  lazy,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
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
  formatSequencerPause,
  runEventToDiagnostic,
} from "../features/sequencer/utils";
import type { StreamAnalysisWorkspaceConfig } from "../features/stream/types";
import { SequencerMetadataPanel } from "../features/sequencer/components/SequencerMetadataPanel";
import { mergeDiagnostics } from "../features/sequencer/diagnostics_merge";
import { useSequencerOutline } from "../features/sequencer/useSequencerOutline";
import { stepPathAtLine } from "../features/sequencer/diagnostic_locations";
import type {
  SequencerAdaptiveStudyStatus,
  SequencerDiagnostic,
  SequencerErrorDetail,
  SequencerPauseInfo,
  SequencerRunEvent,
  SequencerProgress,
  SequencerStepDetail,
  SequencerYamlEditorHandle,
} from "../features/sequencer/types";
import type { CapabilityMember } from "../types";
import type { StreamCatalogEntry } from "../types";
import type { TelemetrySignal } from "../types";
import { activeStepLine, resolveActiveStep } from "../features/sequencer/active_step";
import { SequencerOutlinePane } from "./SequencerOutlinePane";
import {
  SequencerSidebar,
  type SequencerLibraryEntry,
  type SequencerOverrideRow,
} from "./SequencerSidebar";
import { SequencerValuesContext } from "../features/sequencer/components/ValueChip";
import type { ValueContext } from "../features/sequencer/resolved_values";
import type { SequencerLoopStatus, SequencerPlan } from "../features/sequencer/plan";
import type { SequencerYamlCodeEditorProps } from "../features/sequencer/components/SequencerYamlCodeEditor";
import { SequencerTimelinePane } from "./SequencerTimelinePane";
import { perfCount } from "../features/performance/perfInstrumentation";
import { sequencerPageUrl } from "../features/sequencer/pageView";

const NOOP = () => undefined;

/**
 * A hidden tab keeps what it last rendered: its subtree, and the values
 * context it reads, hold still until the tab is shown again, instead of
 * re-rendering for status polls, diagnostics and value updates nobody sees.
 * Component state and effects inside keep working.
 */
function FrozenWhileHidden({ active, children }: { active: boolean; children: ReactNode }) {
  const values = useContext(SequencerValuesContext);
  const shown = useRef({ children, values });
  if (active) {
    shown.current = { children, values };
  }
  return (
    <SequencerValuesContext.Provider value={shown.current.values}>
      {shown.current.children}
    </SequencerValuesContext.Provider>
  );
}

const LazySequencerYamlCodeEditor = lazy(
  () => import("../features/sequencer/components/SequencerYamlCodeEditor")
);

/**
 * The editor text as a subscription. The tabs are memoized and do not take
 * the text, so a keystroke re-renders only the editor that reads it: a
 * re-render of the tabs would make React search every (frozen) step row
 * for readers of Mantine's tabs context.
 */
type TextSource = { get: () => string; subscribe: (listener: () => void) => () => void };

function useTextSource(text: string): TextSource {
  const [source] = useState(() => {
    let current = text;
    const listeners = new Set<() => void>();
    return {
      get: () => current,
      subscribe: (listener: () => void) => {
        listeners.add(listener);
        return () => {
          listeners.delete(listener);
        };
      },
      set: (next: string) => {
        if (next !== current) {
          current = next;
          listeners.forEach((listener) => listener());
        }
      },
    };
  });
  useLayoutEffect(() => {
    source.set(text);
  }, [source, text]);
  return source;
}

function YamlEditorWithText({
  textSource,
  editorRef,
  ...props
}: Omit<SequencerYamlCodeEditorProps, "value"> & {
  textSource: TextSource;
  editorRef: RefObject<SequencerYamlEditorHandle>;
}) {
  const value = useSyncExternalStore(textSource.subscribe, textSource.get);
  return <LazySequencerYamlCodeEditor ref={editorRef} {...props} value={value} />;
}

type Props = {
  opened: boolean;
  onClose: () => void;
  /** Fill the window as the `?view=sequencer` page instead of a modal. */
  page?: boolean;
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
  /** Who paused the run and why, while paused. */
  pauseInfo: SequencerPauseInfo | null;
  /** Events of the current/last run (pauses, failures, warnings). */
  runEvents: ReadonlyArray<SequencerRunEvent>;
  /** `sequencer.plan` of the loaded sequence, for the Timeline tab. */
  plan: SequencerPlan | null;
  planError: string | null;
  /** The run overrides have errors: the plan uses the last valid ones. */
  planOverridesInvalid: boolean;
  /** The Timeline tab is shown or hidden (the plan is fetched only while shown). */
  onTimelineVisibleChange: (visible: boolean) => void;
  /** Loops the run is inside (`sequencer.status.loops`). */
  loops: ReadonlyArray<SequencerLoopStatus>;
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
  /** The editor text is from an older load than the sequencer holds. */
  editorStale: boolean;
  /** Another load happened while the editor holds unloaded edits. */
  otherSequenceLoaded: boolean;
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
  /** Validator diagnostics; the local condition checks are added here. */
  diagnostics: ReadonlyArray<SequencerDiagnostic>;
  /** Resolved `${...}` values for the Steps and YAML tabs. */
  valueContext: ValueContext | null;
  onJumpToDiagnostic: (
    line: number | null,
    column: number | null
  ) => Promise<unknown> | void;
};

export function SequencerModal({
  opened,
  onClose,
  page = false,
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
  pauseInfo,
  runEvents,
  plan,
  planError,
  planOverridesInvalid,
  onTimelineVisibleChange,
  loops,
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
  editorStale,
  otherSequenceLoaded,
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
  diagnostics: validatorDiagnostics,
  valueContext,
  onJumpToDiagnostic,
}: Props) {
  perfCount("react.SequencerModal.renders");
  const [activeTab, setActiveTab] = useState<"steps" | "vars" | "yaml" | "timeline">("steps");
  useEffect(() => {
    onTimelineVisibleChange(activeTab === "timeline");
    return () => onTimelineVisibleChange(false);
  }, [activeTab, onTimelineVisibleChange]);
  const [overridesOpen, setOverridesOpen] = useState(false);

  useEffect(() => {
    if (opened) {
      setOverridesOpen(overrideRows.length > 0);
    }
    // Only when the modal opens; later toggles are the operator's.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened]);

  // One deferred parse feeds the outline, the vars tab and the local checks.
  const [parsedOutline, parseOutlineNow] = useSequencerOutline(yamlText);
  // An edit from the Steps or Variables tab: its outline is built at once.
  const onOutlineEdit = useCallback(
    (value: string) => {
      onYamlTextChange(value);
      parseOutlineNow(value);
    },
    [onYamlTextChange, parseOutlineNow]
  );
  // The memoized tabs read the text only to apply an edit.
  const yamlTextRef = useRef(yamlText);
  yamlTextRef.current = yamlText;
  const getYamlText = useCallback(() => yamlTextRef.current, []);
  const metadata = parsedOutline.metadata;
  const outline = parsedOutline.outline;
  const diagnostics = useMemo(
    () => mergeDiagnostics(validatorDiagnostics, parsedOutline.localDiagnostics),
    [validatorDiagnostics, parsedOutline.localDiagnostics]
  );

  const errorCount = diagnostics.filter((diag) => diag.severity === "error").length;
  const warningCount = diagnostics.filter((diag) => diag.severity === "warning").length;
  const [stepFocus, setStepFocus] = useState<{ line: number; nonce: number } | null>(null);
  // Run events point at the loaded sequence's lines: stale while the editor
  // holds other (not yet loaded) text.
  const textNotLoaded = yamlDirty || editorStale;
  const editorEmpty = !yamlText.trim();
  const runDiagnostics = useMemo(
    () => runEvents.map((event) => runEventToDiagnostic(event, textNotLoaded)),
    [runEvents, textNotLoaded]
  );
  const markerDiagnostics = useMemo(
    () => [...diagnostics, ...runDiagnostics],
    [diagnostics, runDiagnostics]
  );
  const showPause = runtimeState === "PAUSED" && pauseInfo !== null;

  // The running step, only while the editor still holds the loaded text.
  const activeLine = useMemo(
    () =>
      activeStepLine({
        runtimeState,
        detail: currentStepDetail,
        loadedSource,
        yamlDirty: textNotLoaded,
      }),
    [runtimeState, currentStepDetail, loadedSource, textNotLoaded]
  );
  const activeStep = useMemo(() => resolveActiveStep(outline, activeLine), [outline, activeLine]);
  const [follow, setFollowState] = useState(readFollowPreference);
  const setFollow = useCallback((value: boolean) => {
    setFollowState(value);
    try {
      window.localStorage.setItem(FOLLOW_STORAGE_KEY, value ? "1" : "0");
    } catch {
      // Storage can be blocked; the switch still works for this session.
    }
  }, []);
  const stopFollowing = useCallback(() => setFollow(false), [setFollow]);
  const [showValues, setShowValuesState] = useState(readShowValuesPreference);
  const setShowValues = useCallback((value: boolean) => {
    setShowValuesState(value);
    try {
      window.localStorage.setItem(SHOW_VALUES_STORAGE_KEY, value ? "1" : "0");
    } catch {
      // Storage can be blocked; the switch still works for this session.
    }
  }, []);
  const runtimeOrigins = parsedOutline.runtimeOrigins;
  const shownValues = useMemo(
    () => (showValues && valueContext ? { ...valueContext, runtimeOrigins } : null),
    [showValues, valueContext, runtimeOrigins]
  );

  const jumpToDiagnostic = useCallback((diag: SequencerDiagnostic) => {
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
  }, [outline, yamlViewMode, onYamlViewModeChange, onJumpToDiagnostic]);

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

  const textSource = useTextSource(yamlText);
  const tabs = useMemo(
    () => (
          <SequencerValuesContext.Provider value={shownValues}>
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
              <Tabs.Tab value="timeline">Timeline</Tabs.Tab>
              <Switch
                size="xs"
                label="Values"
                title="Show the resolved value of each ${...} next to it."
                checked={showValues}
                onChange={(event) => setShowValues(event.currentTarget.checked)}
                style={{ marginLeft: "auto", alignSelf: "center", marginRight: 12 }}
              />
              <Switch
                size="xs"
                label="Follow"
                title="Scroll to the running step as it changes. Scrolling yourself turns this off."
                checked={follow}
                onChange={(event) => setFollow(event.currentTarget.checked)}
                style={{ alignSelf: "center" }}
              />
            </Tabs.List>
            <Tabs.Panel value="steps" pt="sm" style={tabPanelStyle(activeTab === "steps")}>
              <FrozenWhileHidden active={activeTab === "steps"}>
              <SequencerOutlinePane
                getYamlText={getYamlText}
                parsedOutline={parsedOutline}
                onYamlTextChange={onOutlineEdit}
                streamCatalog={streamCatalog}
                capabilitiesByDevice={capabilitiesByDevice}
                streamWorkspaces={streamWorkspaces}
                latestSignalsByDevice={latestSignalsByDevice}
                colorScheme={colorScheme}
                diagnostics={markerDiagnostics}
                focusRequest={stepFocus}
                activeStep={activeStep}
                follow={follow && activeTab === "steps"}
                onUserScroll={stopFollowing}
              />
              </FrozenWhileHidden>
            </Tabs.Panel>
            <Tabs.Panel value="vars" pt="sm" style={tabPanelStyle(activeTab === "vars")}>
              <FrozenWhileHidden active={activeTab === "vars"}>
              <SequencerMetadataPanel
                metadata={metadata}
                metadataCollapsed={false}
                onToggleCollapsed={NOOP}
                getYamlText={getYamlText}
                onYamlTextChange={onOutlineEdit}
                fill
              />
              </FrozenWhileHidden>
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
                    <YamlEditorWithText
                      editorRef={editorRef}
                      textSource={textSource}
                      onChange={onYamlTextChange}
                      colorScheme={colorScheme}
                      diagnostics={markerDiagnostics}
                      readOnly={yamlViewMode !== "edit"}
                      activeStep={activeStep}
                      follow={follow && activeTab === "yaml"}
                      onUserScroll={stopFollowing}
                      values={shownValues}
                    />
                  </Suspense>
                </div>
              </Stack>
            </Tabs.Panel>
            <Tabs.Panel value="timeline" pt="sm" style={tabPanelStyle(activeTab === "timeline")}>
              {activeTab === "timeline" ? (
                <SequencerTimelinePane
                  outline={outline}
                  plan={loaded ? plan : null}
                  planError={planError}
                  overridesInvalid={planOverridesInvalid}
                  loops={loops}
                  runtimeState={runtimeState}
                  activeStep={activeStep}
                  textNotLoaded={textNotLoaded}
                  onOpenStep={(line) => {
                    setActiveTab("steps");
                    setStepFocus({ line, nonce: Date.now() });
                  }}
                />
              ) : null}
            </Tabs.Panel>
          </Tabs>
          </SequencerValuesContext.Provider>
    ),
    [
      activeTab,
      metadata,
      showValues,
      setShowValues,
      follow,
      setFollow,
      getYamlText,
      parsedOutline,
      onYamlTextChange,
      onOutlineEdit,
      streamCatalog,
      capabilitiesByDevice,
      streamWorkspaces,
      latestSignalsByDevice,
      colorScheme,
      markerDiagnostics,
      stepFocus,
      activeStep,
      stopFollowing,
      yamlViewMode,
      onYamlViewModeChange,
      editorRef,
      textSource,
      shownValues,
      outline,
      loaded,
      plan,
      planError,
      planOverridesInvalid,
      loops,
      runtimeState,
      textNotLoaded,
    ]
  );

  // Memoized like the tabs: a keystroke changes none of this.
  const body = useMemo(
    () => (
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
        {(currentStep ||
          otherSequenceLoaded ||
          showPause ||
          progress?.approximate ||
          autoloadError ||
          statusError ||
          modalError) && (
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
            {otherSequenceLoaded && (
              <Group gap="xs" wrap="nowrap">
                <Text size="xs" c="yellow">
                  A different sequence was loaded (by another client); your edits are not
                  the loaded sequence.
                </Text>
                <Button
                  size="compact-xs"
                  variant="light"
                  color="yellow"
                  loading={loadedYamlBusy}
                  onClick={() => {
                    void onShowLoadedYaml();
                  }}
                >
                  Discard edits and load current
                </Button>
              </Group>
            )}
            {showPause && pauseInfo && (
              <Text size="sm" c="yellow" fw={500}>
                {formatSequencerPause(pauseInfo)}
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
                {/* `formatted` already ends in the location; with a step we
                    append our own, so use the bare message. */}
                {errorDetail?.step ? errorDetail.message : (errorDetail?.formatted ?? statusError)}
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
          <SequencerSidebar
            libraryConfigured={libraryConfigured}
            libraryEntries={libraryEntries}
            libraryLoading={libraryLoading}
            libraryError={libraryError}
            selectedSequenceId={selectedSequenceId}
            onSelectedSequenceIdChange={onSelectedSequenceIdChange}
            onReloadLibrary={onReloadLibrary}
            onLoadSelectedLibrary={onLoadSelectedLibrary}
            loadBusy={loadBusy}
            fileInputRef={fileInputRef}
            onFileInputChange={onFileInputChange}
            loadedYamlBusy={loadedYamlBusy}
            hasSequencerProcess={hasSequencerProcess}
            onShowLoadedYaml={onShowLoadedYaml}
            editorLabel={editorLabel}
            editorEmpty={editorEmpty}
            yamlDirty={yamlDirty}
            loaded={loaded}
            loadedSource={loadedSource}
            onLoad={onLoad}
            validateBusy={validateBusy}
            onValidate={onValidate}
            reloadSourceBusy={reloadSourceBusy}
            canReloadSource={canReloadSource}
            actionBusy={actionBusy}
            reloadSourceLabel={reloadSourceLabel}
            onReloadLoadedSource={onReloadLoadedSource}
            overridesOpen={overridesOpen}
            setOverridesOpen={setOverridesOpen}
            overrideRows={overrideRows}
            overrideVarOptions={overrideVarOptions}
            overrideErrors={overrideErrors}
            overridePreview={overridePreview}
            overridesValid={overridesValid}
            onAddOverrideRow={onAddOverrideRow}
            onRemoveOverrideRow={onRemoveOverrideRow}
            onUpdateOverrideRow={onUpdateOverrideRow}
            onClearOverrides={onClearOverrides}
            loadedAdaptiveIds={loadedAdaptiveIds}
            adaptiveStudies={adaptiveStudies}
            adaptiveModes={adaptiveModes}
            adaptiveClearBusyStudyId={adaptiveClearBusyStudyId}
            onAdaptiveModeChange={onAdaptiveModeChange}
            onClearAdaptiveStudy={onClearAdaptiveStudy}
            diagnostics={diagnostics}
            runEvents={runEvents}
            textNotLoaded={textNotLoaded}
            onJumpToDiagnostic={jumpToDiagnostic}
          />

          {/* Main: steps / variables / YAML, each using the full height. */}
          {tabs}
        </div>
      </Stack>
    ),
    [
      actionBusy,
      adaptiveClearBusyStudyId,
      adaptiveModes,
      adaptiveStudies,
      autoloadError,
      canReloadSource,
      cleanupActive,
      currentStep,
      currentStepDetail,
      diagnostics,
      editorEmpty,
      editorLabel,
      errorCount,
      errorDetail,
      etaText,
      fileInputRef,
      hasSequencerProcess,
      jumpToDiagnostic,
      libraryConfigured,
      libraryEntries,
      libraryError,
      libraryLoading,
      loadBusy,
      loaded,
      loadedAdaptiveIds,
      loadedSource,
      loadedYamlBusy,
      loops,
      modalError,
      onAdaptiveModeChange,
      onAddOverrideRow,
      onClearAdaptiveStudy,
      onClearOverrides,
      onFileInputChange,
      onJumpToDiagnostic,
      onLoad,
      onLoadSelectedLibrary,
      onReloadLibrary,
      onReloadLoadedSource,
      onRemoveOverrideRow,
      onRepeatCountChange,
      onRunAction,
      onRunModeChange,
      onSelectedSequenceIdChange,
      onShowLoadedYaml,
      onUpdateOverrideRow,
      onValidate,
      otherSequenceLoaded,
      outline,
      overrideErrors,
      overridePreview,
      overrideRows,
      overrideVarOptions,
      overridesOpen,
      overridesValid,
      pauseInfo,
      primaryAction,
      primaryDisabled,
      primaryIcon,
      primaryLabel,
      processState,
      progress,
      progressPercent,
      progressText,
      reloadSourceBusy,
      reloadSourceLabel,
      repeatCount,
      runEvents,
      runMode,
      runtimeState,
      selectedSequenceId,
      showPause,
      statusError,
      tabs,
      textNotLoaded,
      validateBusy,
      warningCount,
      yamlDirty,
    ]
  );

  if (page) {
    return (
      <div className="sequencer-page">
        <Title order={4} style={{ flexShrink: 0 }}>
          Sequencer
        </Title>
        {body}
      </div>
    );
  }

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={
        <Group gap={6} wrap="nowrap">
          Sequencer
          <Tooltip label="Open in a new tab" withinPortal zIndex={600}>
            <ActionIcon
              component="a"
              href={sequencerPageUrl()}
              target="_blank"
              rel="noopener"
              variant="subtle"
              color="gray"
              size="sm"
              aria-label="Open the Sequencer in a new tab"
            >
              <IconExternalLink size={14} />
            </ActionIcon>
          </Tooltip>
        </Group>
      }
      size="calc(100vw - 3rem)"
      centered
      styles={{
        // The modal fills the window; its body takes what the header leaves.
        content: { height: "calc(100dvh - 3rem)", display: "flex", flexDirection: "column" },
        body: { flex: 1, minHeight: 0, display: "flex", flexDirection: "column", paddingTop: 0 },
      }}
    >
      {body}
    </Modal>
  );
}

const FOLLOW_STORAGE_KEY = "experiment-control.sequencer.followActiveStep";
const SHOW_VALUES_STORAGE_KEY = "experiment-control.sequencer.showValues";

/** Values are shown unless turned off in this browser. */
function readShowValuesPreference(): boolean {
  try {
    return window.localStorage.getItem(SHOW_VALUES_STORAGE_KEY) !== "0";
  } catch {
    return true;
  }
}

function readFollowPreference(): boolean {
  try {
    return window.localStorage.getItem(FOLLOW_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function tabPanelStyle(active: boolean): CSSProperties {
  return active
    ? { flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }
    : { display: "none" };
}
