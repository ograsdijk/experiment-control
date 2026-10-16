import { useEffect, useMemo, useRef, useState } from "react";
import type { ApiResponse } from "../../api";
import {
  extractTemplateExpressions,
  normalizeResolvedVars,
  type ResolvedVars,
} from "./resolved_values";

export const RESOLVE_VARS_DEBOUNCE_MS = 400;

function markResolvedStale(prev: ResolvedVars | null): ResolvedVars | null {
  return prev && !prev.stale ? { ...prev, stale: true } : prev;
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
  const expressions = useMemo(() => extractTemplateExpressions(yamlText), [yamlText]);

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
        expressions,
      };
      // Empty editor: the loaded sequence.
      if (yamlText.trim()) {
        params.text = yamlText;
      }
      callProcessFn(processId, "sequencer.resolve_vars", params)
        .then((resp) => {
          if (requestId !== requestIdRef.current) {
            return;
          }
          const next = resp.ok ? normalizeResolvedVars(resp.result) : null;
          setResolved((prev) => next ?? markResolvedStale(prev));
        })
        .catch(() => {
          if (requestId === requestIdRef.current) {
            setResolved(markResolvedStale);
          }
        });
    }, RESOLVE_VARS_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [callProcessFn, processId, enabled, yamlText, overridesKey, expressions]);

  return enabled ? resolved : null;
}
