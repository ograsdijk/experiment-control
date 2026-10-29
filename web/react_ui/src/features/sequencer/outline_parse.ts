import { buildSequencerOutlineMetadata, buildSequencerStepOutline } from "./outline";
import type {
  SequencerDiagnostic,
  SequencerOutlineMetadata,
  SequencerStepOutlineNode,
} from "./types";
import { buildRuntimeOrigins, type RuntimeOrigins } from "./runtime_origins";
import { localConditionDiagnostics } from "./validation";

export type ParsedSequencerOutline = {
  /** The text this was parsed from: it can trail the editor while typing. */
  text: string;
  outline: SequencerStepOutlineNode[];
  error: string | null;
  metadata: SequencerOutlineMetadata;
  localDiagnostics: SequencerDiagnostic[];
  /** Names steps set while running, for the value chips. */
  runtimeOrigins: RuntimeOrigins;
};

const EMPTY_METADATA: SequencerOutlineMetadata = { version: null, vars: [], contextColumns: [] };

/**
 * The step outline, vars metadata, local condition checks and runtime
 * names of a sequence.
 * Plain data only: it is built in the parse worker and copied to the page.
 */
export function parseSequencerOutline(text: string): ParsedSequencerOutline {
  let metadata = EMPTY_METADATA;
  try {
    metadata = buildSequencerOutlineMetadata(text);
  } catch {
    // Keep the empty metadata.
  }
  try {
    const outline = buildSequencerStepOutline(text);
    return {
      text,
      outline,
      error: null,
      metadata,
      localDiagnostics: text.trim() ? localConditionDiagnostics(outline) : [],
      runtimeOrigins: buildRuntimeOrigins(outline),
    };
  } catch (error) {
    return {
      text,
      outline: [],
      error: error instanceof Error ? error.message : String(error),
      metadata,
      localDiagnostics: [],
      runtimeOrigins: {},
    };
  }
}
