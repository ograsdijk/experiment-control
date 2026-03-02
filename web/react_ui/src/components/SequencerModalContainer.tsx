import type { ChangeEvent, ReactNode, RefObject } from "react";
import type {
  SequencerAdaptiveStudyStatus,
  SequencerDiagnostic,
  SequencerProgress,
} from "../features/sequencer/types";
import type { CapabilityMember } from "../types";
import { SequencerModal } from "./SequencerModal";

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
  sequencerProcessId: string | null;
  onShowLoadedYaml: (
    processId: string,
    opts?: { applyToEditor?: boolean; silent?: boolean }
  ) => Promise<unknown> | void;
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
