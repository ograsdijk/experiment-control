import { useCallback, useMemo, useState } from "react";

type DraftIdentity = {
  nodeId: string | null;
  snippet: string;
};

export function useStepDraftSync(nodeId: string, snippet: string) {
  const [draftIdentity, setDraftIdentity] = useState<DraftIdentity>({
    nodeId: null,
    snippet: "",
  });

  const needsSync = useMemo(
    () =>
      draftIdentity.nodeId !== nodeId || draftIdentity.snippet !== snippet,
    [draftIdentity.nodeId, draftIdentity.snippet, nodeId, snippet]
  );

  const usingDraft = draftIdentity.nodeId === nodeId;

  const markCurrent = useCallback(() => {
    setDraftIdentity({
      nodeId,
      snippet,
    });
  }, [nodeId, snippet]);

  return {
    usingDraft,
    needsSync,
    markCurrent,
  };
}
