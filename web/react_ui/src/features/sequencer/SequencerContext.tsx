import {
  createContext,
  useContext,
  useMemo,
  type CSSProperties,
  type MutableRefObject,
  type ReactNode,
} from "react";
import { sequencerRuntimeStateColor } from "../runtime/helpers";
import { colorWithAlpha } from "../../utils/traceColors";
import { useSequencerController } from "./useSequencerController";

export type SequencerController = ReturnType<typeof useSequencerController>;

/** What the header chip shows: it changes with the run, not with the editor. */
export type SequencerHeaderSummary = {
  chipColor: string;
  statusLoading: boolean;
  statusError: string | null;
  chipTooltip: string;
  runtimeState: string;
  chipSuffix: string;
  chipProgressStyle: CSSProperties | undefined;
  primaryAction: "start" | "pause" | "resume";
  primaryLabel: string;
  primaryDisabled: boolean;
  actionBusy: boolean;
  loaded: boolean;
  openSequencer: () => Promise<unknown> | void;
  runPrimaryAction: () => Promise<unknown> | void;
};

/** The calls App makes into the sequencer from outside the provider. */
export type SequencerApi = {
  refreshSequencerStatus: SequencerController["refreshSequencerStatus"];
};

const SequencerControllerContext = createContext<SequencerController | null>(null);
const SequencerHeaderContext = createContext<SequencerHeaderSummary | null>(null);

type ProviderProps = Parameters<typeof useSequencerController>[0] & {
  colorScheme: "light" | "dark";
  apiRef: MutableRefObject<SequencerApi | null>;
  children: ReactNode;
};

function chipProgressStyle(
  runtimeState: string,
  progressPercent: number | null,
  colorScheme: "light" | "dark"
): CSSProperties | undefined {
  const isRunning = runtimeState === "RUNNING" || runtimeState === "STOP_REQUESTED";
  if (!isRunning || progressPercent === null) {
    return undefined;
  }
  const pct = Math.max(0, Math.min(100, progressPercent));
  const fill =
    colorScheme === "dark" ? colorWithAlpha("#12b886", 0.52) : colorWithAlpha("#12b886", 0.30);
  return {
    backgroundImage: `linear-gradient(90deg, ${fill} 0%, ${fill} ${pct}%, transparent ${pct}%, transparent 100%)`,
    backgroundRepeat: "no-repeat",
    transition: "background-image 120ms linear",
  };
}

/**
 * Owns the sequencer controller below App. App passes its tree as `children`,
 * so a sequencer state change (a keystroke in the editor, a status poll)
 * re-renders only the components that read these contexts, not the
 * dashboard. The header reads the summary, which keeps its identity while
 * only editor state changes.
 */
export function SequencerProvider({ colorScheme, apiRef, children, ...deps }: ProviderProps) {
  const controller = useSequencerController(deps);
  apiRef.current = { refreshSequencerStatus: controller.refreshSequencerStatus };

  const {
    sequencerProcessState,
    sequencerRuntimeState,
    sequencerStatusLoading,
    sequencerStatus,
    sequencerChipTooltip,
    sequencerChipSuffix,
    sequencerProgressPercent,
    sequencerPrimaryAction,
    sequencerPrimaryLabel,
    sequencerPrimaryDisabled,
    sequencerActionBusy,
    sequencerLoaded,
    openSequencerModal,
    runSequencerAction,
  } = controller;
  const statusError = sequencerStatus?.error ?? null;
  const header = useMemo<SequencerHeaderSummary>(
    () => ({
      chipColor: sequencerRuntimeStateColor(sequencerRuntimeState, sequencerProcessState),
      statusLoading: sequencerStatusLoading,
      statusError,
      chipTooltip: sequencerChipTooltip,
      runtimeState: sequencerRuntimeState,
      chipSuffix: sequencerChipSuffix,
      chipProgressStyle: chipProgressStyle(
        sequencerRuntimeState,
        sequencerProgressPercent,
        colorScheme
      ),
      primaryAction: sequencerPrimaryAction,
      primaryLabel: sequencerPrimaryLabel,
      primaryDisabled: sequencerPrimaryDisabled,
      actionBusy: sequencerActionBusy,
      loaded: sequencerLoaded,
      openSequencer: openSequencerModal,
      runPrimaryAction: () => runSequencerAction(sequencerPrimaryAction),
    }),
    [
      colorScheme,
      openSequencerModal,
      runSequencerAction,
      sequencerActionBusy,
      sequencerChipSuffix,
      sequencerChipTooltip,
      sequencerLoaded,
      sequencerPrimaryAction,
      sequencerPrimaryDisabled,
      sequencerPrimaryLabel,
      sequencerProcessState,
      sequencerProgressPercent,
      sequencerRuntimeState,
      sequencerStatusLoading,
      statusError,
    ]
  );

  return (
    <SequencerControllerContext.Provider value={controller}>
      <SequencerHeaderContext.Provider value={header}>{children}</SequencerHeaderContext.Provider>
    </SequencerControllerContext.Provider>
  );
}

export function useSequencerContext(): SequencerController {
  const value = useContext(SequencerControllerContext);
  if (!value) {
    throw new Error("useSequencerContext must be used inside SequencerProvider");
  }
  return value;
}

export function useSequencerHeader(): SequencerHeaderSummary {
  const value = useContext(SequencerHeaderContext);
  if (!value) {
    throw new Error("useSequencerHeader must be used inside SequencerProvider");
  }
  return value;
}
