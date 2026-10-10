import { useState } from "react";
import { yamlSourceToDisplay } from "./value_text";

/**
 * Local draft for a field whose value lives in the sequence YAML. Each write
 * re-serializes the step, so committing per keystroke turned intermediate
 * text into YAML (and back into the field). The draft is committed only when
 * asked, and only if it differs from what the field shows.
 */
export function useDraftValue(
  source: string | null | undefined,
  onCommit: (value: string) => void
) {
  const shown = yamlSourceToDisplay(source);
  const [draft, setDraft] = useState<string | null>(null);
  return {
    text: draft ?? shown,
    editing: draft !== null,
    edit: (next: string) => setDraft(next),
    cancel: () => setDraft(null),
    commit: (next?: string) => {
      const value = next ?? draft;
      setDraft(null);
      if (value !== null && value !== shown) {
        onCommit(value);
      }
    },
  };
}
