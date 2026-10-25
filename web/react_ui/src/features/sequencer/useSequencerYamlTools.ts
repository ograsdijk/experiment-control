import { useEffect, useState } from "react";

export type SequencerYamlTools = typeof import("./yaml_tools");

let loadedTools: SequencerYamlTools | null = null;
let pendingTools: Promise<SequencerYamlTools> | null = null;

/** Import the YAML helpers once; a failed import is retried on the next call. */
export function loadSequencerYamlTools(): Promise<SequencerYamlTools> {
  pendingTools ??= import("./yaml_tools").then(
    (tools) => {
      loadedTools = tools;
      return tools;
    },
    (error: unknown) => {
      pendingTools = null;
      throw error;
    }
  );
  return pendingTools;
}

/**
 * The YAML helpers, or null until they have loaded. The import starts the
 * first time `wanted` is true (the Sequencer modal opens); once loaded they
 * stay available.
 */
export function useSequencerYamlTools(wanted: boolean): SequencerYamlTools | null {
  const [tools, setTools] = useState<SequencerYamlTools | null>(loadedTools);

  useEffect(() => {
    if (!wanted || tools) {
      return;
    }
    let active = true;
    loadSequencerYamlTools()
      .then((loaded) => {
        if (active) {
          setTools(loaded);
        }
      })
      // The modal's own chunk needs the same code, so a failed load shows
      // there; the next open tries again.
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [wanted, tools]);

  return tools;
}
