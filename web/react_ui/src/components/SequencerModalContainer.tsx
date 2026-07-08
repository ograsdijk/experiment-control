import type { ChangeEvent, ReactNode, RefObject } from "react";
import type {
  SequencerAdaptiveStudyStatus,
  SequencerDiagnostic,
  SequencerErrorDetail,
  SequencerProgress,
  SequencerStepDetail,
  SequencerYamlEditorHandle,
} from "../features/sequencer/types";
import type { StreamAnalysisWorkspaceConfig } from "../features/stream/types";
import type { CapabilityMember } from "../types";
import type { StreamCatalogEntry } from "../types";
import type { TelemetrySignal } from "../types";
import { SequencerModal } from "./SequencerModal";

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
  libraryEntries: {
    id: string;
    label: string | null;
    description: string | null;
    path: string | null;
    source: string | null;
    vars: string[];
  }[];
  libraryLoading: boolean;
  libraryError: string | null;
  selectedSequenceId: string | null;
  onSelectedSequenceIdChange: (sequenceId: string | null) => void;
  onReloadLibrary: () => Promise<unknown> | void;
  overrideRows: {
    id: string;
    name: string;
    valueType: "number" | "bool" | "string" | "json" | "null";
    valueText: string;
  }[];
  overrideVarOptions: string[];
  overrideErrors: Record<string, string | null>;
  overridePreview: string;
  overridesValid: boolean;
  onAddOverrideRow: () => void;
  onRemoveOverrideRow: (rowId: string) => void;
  onUpdateOverrideRow: (
    rowId: string,
    patch: Partial<{
      name: string;
      valueType: "number" | "bool" | "string" | "json" | "null";
      valueText: string;
    }>
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
  sequencerProcessId: string | null;
  onShowLoadedYaml: (
    processId: string,
    opts?: { applyToEditor?: boolean; silent?: boolean }
  ) => Promise<unknown> | void;
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

export function SequencerModalContainer({
  sequencerProcessId,
  onShowLoadedYaml,
  ...props
}: Props) {
  return (
    <SequencerModal
      {...props}
      hasSequencerProcess={Boolean(sequencerProcessId)}
      onShowLoadedYaml={async () => {
        if (!sequencerProcessId) {
          return;
        }
        await onShowLoadedYaml(sequencerProcessId, {
          applyToEditor: true,
        });
      }}
    />
  );
}
