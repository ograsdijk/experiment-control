import { useEffect, useMemo, useRef, useState } from "react";
import type { ApiResponse } from "../../api";
import {
  decodedTemplates,
  extractTemplateExpressions,
  extractVarsBlock,
  normalizeResolvedVars,
  type ResolvedVars,
} from "./resolved_values";

export const RESOLVE_VARS_DEBOUNCE_MS = 400;

function markResolvedStale(prev: ResolvedVars | null, reason: string): ResolvedVars | null {
  return prev ? { ...prev, stale: reason } : prev;
}

/** Why a request failed, for the chips' "may be out of date" line. */
export function staleReason(resp: ApiResponse<unknown> | null): string {
  if (resp?.error?.code === "invalid_yaml") {
    return "the YAML doesn't parse right now";
  }
  const message = resp?.error?.message ?? resp?.error?.code;
  return message ? `the sequencer couldn't resolve them (${message})` : "the sequencer can't be reached";
}

/**
 * Expressions to evaluate, as the runtime sees them (YAML-decoded), and a
 * function that keys the results by the text as written in the editor.
 */
function expressionRequest(yamlText: string) {
  const written = extractTemplateExpressions(yamlText);
  const decoded = decodedTemplates(yamlText);
  const send = Array.from(new Set(written.map((text) => decoded[text] ?? text)));
  const rekey = (resolved: ResolvedVars): ResolvedVars => {
    const expressions = { ...resolved.expressions };
    for (const [text, runtimeText] of Object.entries(decoded)) {
      const result = resolved.expressions[runtimeText];
      if (result) {
        expressions[text] = result;
      }
    }
    return { ...resolved, expressions };
  };
  return { send, rekey };
}

type CallProcessFn = (
  processId: string,
  action: string,
  params: Record<string, unknown>
) => Promise<ApiResponse<unknown>>;

/**
 * Resolve the editor's vars (with overrides) and its `${...}` expressions via
 * `sequencer.resolve_vars`, shortly after the text or overrides stop changing.
 * While a request fails (the YAML doesn't parse) the last values are kept,
 * marked stale. They are dropped when the modal closes or the process changes.
 */
export function useResolvedVars({
  callProcessFn,
  processId,
  enabled,
  yamlText,
  overrides,
}: {
  callProcessFn: CallProcessFn;
  processId: string | null;
  enabled: boolean;
  yamlText: string;
  overrides: Record<string, unknown>;
}): ResolvedVars | null {
  const [resolved, setResolved] = useState<ResolvedVars | null>(null);
  const requestIdRef = useRef(0);
  const overridesKey = JSON.stringify(overrides);
  const request = useMemo(() => expressionRequest(yamlText), [yamlText]);

  // A closed modal or another process: the old values belong to other text.
  useEffect(() => {
    setResolved(null);
  }, [enabled, processId]);

  useEffect(() => {
    if (!enabled || !processId) {
      return;
    }
    const requestId = ++requestIdRef.current;
    const timer = window.setTimeout(() => {
      const params: Record<string, unknown> = {
        vars_override: JSON.parse(overridesKey) as Record<string, unknown>,
        expressions: request.send,
      };
      // Empty editor: the loaded sequence. Otherwise only the vars block,
      // the part that is read: parsing a long sequence blocked the sequencer.
      if (yamlText.trim()) {
        params.text = extractVarsBlock(yamlText);
      }
      callProcessFn(processId, "sequencer.resolve_vars", params)
        .then((resp) => {
          if (requestId !== requestIdRef.current) {
            return;
          }
          const next = resp.ok ? normalizeResolvedVars(resp.result) : null;
          setResolved((prev) => (next ? request.rekey(next) : markResolvedStale(prev, staleReason(resp))));
        })
        .catch(() => {
          if (requestId === requestIdRef.current) {
            setResolved((prev) => markResolvedStale(prev, staleReason(null)));
          }
        });
    }, RESOLVE_VARS_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      // A reply that arrives after this (newer text, a closed modal) is dropped.
      requestIdRef.current += 1;
    };
  }, [callProcessFn, processId, enabled, yamlText, overridesKey, request]);

  return enabled ? resolved : null;
}
