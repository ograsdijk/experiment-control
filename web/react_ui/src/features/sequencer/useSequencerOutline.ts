import { startTransition, useCallback, useEffect, useRef, useState } from "react";
import { parseSequencerOutline, type ParsedSequencerOutline } from "./outline_parse";
import { parseInWorker } from "./parse_worker_client";

export type { ParsedSequencerOutline } from "./outline_parse";

const EMPTY_PARSE: ParsedSequencerOutline = {
  text: "",
  outline: [],
  error: null,
  metadata: { version: null, vars: [], contextColumns: [] },
  localDiagnostics: [],
  runtimeOrigins: {},
};

/** The previous value when the next one has the same content. */
function keepIfEqual<T>(previous: T, next: T): T {
  return JSON.stringify(previous) === JSON.stringify(next) ? previous : next;
}

/** The next parse, keeping the parts whose content did not change. */
function merge(previous: ParsedSequencerOutline, next: ParsedSequencerOutline): ParsedSequencerOutline {
  if (previous.text === next.text) {
    return previous;
  }
  return {
    ...next,
    metadata: keepIfEqual(previous.metadata, next.metadata),
    localDiagnostics: keepIfEqual(previous.localDiagnostics, next.localDiagnostics),
    runtimeOrigins: keepIfEqual(previous.runtimeOrigins, next.runtimeOrigins),
  };
}

/**
 * The step outline, vars metadata and local condition checks, parsed once
 * for the whole Sequencer modal, in the parse worker: a long sequence takes
 * tens of milliseconds per parse, which no longer blocks typing. The result
 * trails the editor text (its `text` says which text it belongs to). Only a
 * result for the current text is shown: while the operator types, the
 * outline (and everything drawn from it) updates once per pause, not once
 * per parse.
 *
 * Most edits leave the vars, the condition checks and the runtime names
 * unchanged; those keep their identity, so the memoized parts that show
 * them do not re-render.
 *
 * `parseNow` parses a text in the page, at once, for an edit made from the
 * outline itself: the next such edit (often a moment later) then works on
 * the new lines instead of waiting for the worker.
 */
export function useSequencerOutline(
  yamlText: string
): [ParsedSequencerOutline, (text: string) => void] {
  const [parsed, setParsed] = useState<ParsedSequencerOutline>(EMPTY_PARSE);
  const mountedRef = useRef(true);
  const latestTextRef = useRef(yamlText);
  latestTextRef.current = yamlText;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    parseInWorker("outline", yamlText).then(
      (next) => {
        // A newer text is waiting for its own parse.
        if (!mountedRef.current || next.text !== latestTextRef.current) {
          return;
        }
        // A transition: the re-render of everything that shows the outline
        // can be interrupted by keystrokes instead of blocking them.
        startTransition(() => {
          setParsed((previous) => merge(previous, next));
        });
      },
      // Superseded by newer text (its result follows), or a parse failure:
      // keep showing the last good result.
      () => undefined
    );
  }, [yamlText]);

  const parseNow = useCallback((text: string) => {
    // The text about to be current: older worker results no longer apply.
    latestTextRef.current = text;
    const next = parseSequencerOutline(text);
    setParsed((previous) => merge(previous, next));
  }, []);

  return [parsed, parseNow];
}
