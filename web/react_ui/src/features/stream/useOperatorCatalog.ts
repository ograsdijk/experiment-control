import { useEffect, useMemo, useRef, useState } from "react";

import { fetchStreamOperators } from "../../api";
import {
  STREAM_DAG_OPS,
  collectNodeParamValues,
  mergeOpParamChoices,
  parseOperatorCatalogChoices,
  type OpParamChoices,
} from "./dag";
import type { StreamDagOpDef, StreamDagOpId } from "./types";

export type OperatorCatalogStatus = "idle" | "loading" | "ready" | "error";

/**
 * Op definitions with param choices taken from the backend catalog.
 *
 * The backend model registry is the source of truth for which fit models
 * exist, so adding one needs no frontend change. Until the fetch resolves --
 * or if it fails -- the static table in `dag.ts` is used unchanged, which
 * keeps the editor usable either way.
 */
export function useOperatorCatalog(
  nodes?: Array<{ op: string; params: Record<string, unknown> }>
): {
  ops: Record<StreamDagOpId, StreamDagOpDef>;
  status: OperatorCatalogStatus;
} {
  const [choices, setChoices] = useState<OpParamChoices>({});
  const [status, setStatus] = useState<OperatorCatalogStatus>("idle");
  const requested = useRef(false);

  useEffect(() => {
    if (requested.current) return;
    requested.current = true;
    let cancelled = false;
    setStatus("loading");
    void (async () => {
      try {
        const resp = await fetchStreamOperators();
        if (cancelled) return;
        const operators = (resp as { operators?: unknown } | null)?.operators;
        setChoices(parseOperatorCatalogChoices(operators));
        setStatus("ready");
      } catch {
        if (cancelled) return;
        // Keep the static table; the editor stays usable offline.
        setStatus("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Values already set on nodes are preserved as options even when the
  // catalog does not list them, so a saved workspace never silently renders
  // as a different model than the one it runs.
  const currentValues = useMemo(
    () => (nodes && nodes.length > 0 ? collectNodeParamValues(nodes) : {}),
    [nodes]
  );

  const ops = useMemo(
    () => mergeOpParamChoices(STREAM_DAG_OPS, choices, currentValues),
    [choices, currentValues]
  );

  return { ops, status };
}
