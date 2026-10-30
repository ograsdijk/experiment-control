// @vitest-environment jsdom

import { MantineProvider } from "@mantine/core";
import { act, createElement, createRef, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { parseRunForm } from "../features/sequencer/run_form";
import { SequencerModal } from "./SequencerModal";

vi.mock("../api", () => ({
  callDevice: vi.fn(async () => ({ ok: true, result: null })),
  fetchCapabilities: vi.fn(async () => []),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const FORM = parseRunForm(`
version: 1
vars:
  passes: 3
form:
  title: Quick scan
  sections:
    - fields:
        - {type: integer, var: passes, label: Passes}
steps:
  - sleep: 0
`)!;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (window as unknown as { matchMedia: unknown }).matchMedia ??= () => ({
    matches: false,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
  });
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

type ModalProps = ComponentProps<typeof SequencerModal>;

function props(overrides: Partial<ModalProps>): ModalProps {
  const noop = () => undefined;
  return {
    opened: true,
    onClose: noop,
    page: true,
    processState: "RUNNING",
    runtimeState: "IDLE",
    loaded: false,
    currentStep: null,
    currentStepDetail: null,
    errorDetail: null,
    cleanupActive: null,
    progress: null,
    progressPercent: null,
    totalSteps: null,
    completedSteps: null,
    loadedSource: null,
    editorLabel: null,
    pauseInfo: null,
    runEvents: [],
    plan: null,
    planError: null,
    planOverridesInvalid: false,
    onTimelineVisibleChange: noop,
    loops: [],
    autoloadError: null,
    statusError: null,
    modalError: null,
    primaryIcon: null,
    primaryAction: "start",
    primaryLabel: "Start",
    primaryDisabled: true,
    actionBusy: false,
    runMode: "once",
    repeatCount: 1,
    onRunModeChange: noop,
    onRepeatCountChange: noop,
    libraryConfigured: true,
    libraryEntries: [],
    libraryLoading: false,
    libraryError: null,
    selectedSequenceId: null,
    onSelectedSequenceIdChange: noop,
    onReloadLibrary: noop,
    overrideRows: [],
    overrideVarOptions: [],
    overrideErrors: {},
    overridePreview: "",
    overridesValid: true,
    onAddOverrideRow: noop,
    onRemoveOverrideRow: noop,
    onUpdateOverrideRow: noop,
    onClearOverrides: noop,
    adaptiveModes: {},
    adaptiveStudies: {},
    loadedAdaptiveIds: [],
    adaptiveClearBusyStudyId: null,
    onRunAction: noop,
    onFetchLibraryForm: async () => FORM,
    onAdaptiveModeChange: noop,
    onClearAdaptiveStudy: noop,
    fileInputRef: createRef(),
    onFileInputChange: noop,
    yamlViewMode: "edit",
    onYamlViewModeChange: noop,
    loadedYamlBusy: false,
    hasSequencerProcess: true,
    onShowLoadedYaml: noop,
    validateBusy: false,
    onValidate: noop,
    loadBusy: false,
    onLoad: noop,
    onLoadSelectedLibrary: noop,
    yamlDirty: false,
    editorStale: false,
    otherSequenceLoaded: false,
    reloadSourceBusy: false,
    canReloadSource: false,
    reloadSourceLabel: "",
    onReloadLoadedSource: noop,
    editorRef: createRef(),
    yamlText: "",
    onYamlTextChange: noop,
    streamCatalog: [],
    capabilitiesByDevice: {},
    streamWorkspaces: {},
    latestSignalsByDevice: {},
    colorScheme: "dark",
    diagnostics: [],
    valueContext: null,
    onJumpToDiagnostic: noop,
    ...overrides,
  } as ModalProps;
}

describe("SequencerModal run form", () => {
  it("opens the form of a library sequence while nothing else changes", async () => {
    const element = createElement(
      MantineProvider,
      { env: "test" },
      createElement(
        SequencerModal,
        props({
          libraryEntries: [
            { id: "quick_scan", label: "Quick scan", hasForm: true },
          ] as ModalProps["libraryEntries"],
        })
      )
    );
    await act(async () => root.render(element));
    const menuButton = [...document.querySelectorAll("button")].find((el) =>
      el.textContent?.includes("Run with form")
    );
    expect(menuButton).toBeTruthy();
    await act(async () => menuButton!.click());
    const item = [...document.querySelectorAll("[role=menuitem]")].find((el) =>
      el.textContent?.includes("Quick scan")
    ) as HTMLElement | undefined;
    expect(item).toBeTruthy();
    // No prop changes after the click: the dialog must still appear.
    await act(async () => item!.click());
    expect(document.body.textContent).toContain("Passes");
  });
});
