import { useDeferredValue, useMemo, useRef } from "react";
import { buildSequencerOutlineMetadata, buildSequencerStepOutline } from "./outline";
import type {
  SequencerDiagnostic,
  SequencerOutlineMetadata,
  SequencerStepOutlineNode,
} from "./types";
import { localConditionDiagnostics } from "./validation";

export type ParsedSequencerOutline = {
  /** The text this was parsed from: it can trail the editor while typing. */
  text: string;
  outline: SequencerStepOutlineNode[];
  error: string | null;
  metadata: SequencerOutlineMetadata;
  localDiagnostics: SequencerDiagnostic[];
};

const EMPTY_METADATA: SequencerOutlineMetadata = { version: null, vars: [], contextColumns: [] };

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
    };
  } catch (error) {
    return {
      text,
      outline: [],
      error: error instanceof Error ? error.message : String(error),
      metadata,
      localDiagnostics: [],
    };
  }
}

/** The previous value when the next one has the same content. */
function keepIfEqual<T>(previous: T | undefined, next: T): T {
  return previous !== undefined && JSON.stringify(previous) === JSON.stringify(next)
    ? previous
    : next;
}

/**
 * The step outline, vars metadata and local condition checks, parsed once
 * for the whole Sequencer modal. Parsing a long sequence takes tens of
 * milliseconds, so it runs on a deferred copy of the text: keystrokes render
 * first, and React drops parses of text that has already changed again.
 *
 * Most edits leave the vars and the condition checks unchanged; those keep
 * their identity, so the memoized parts that show them do not re-render.
 */
export function useSequencerOutline(yamlText: string): ParsedSequencerOutline {
  const text = useDeferredValue(yamlText);
  const previousRef = useRef<ParsedSequencerOutline>();
  const parsed = useMemo(() => {
    const next = parseSequencerOutline(text);
    const previous = previousRef.current;
    return {
      ...next,
      metadata: keepIfEqual(previous?.metadata, next.metadata),
      localDiagnostics: keepIfEqual(previous?.localDiagnostics, next.localDiagnostics),
    };
  }, [text]);
  previousRef.current = parsed;
  return parsed;
}
