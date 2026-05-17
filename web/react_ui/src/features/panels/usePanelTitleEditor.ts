import type { PlotPanelState } from "../stream/types";
import { usePanels } from "./PanelsContext";

/**
 * Panel title editor — the three handlers + the editor state that
 * drive the inline-rename UI on each panel header.
 *
 * The state itself (`editingPanelId` + `panelTitleDraft`) lives in
 * `PanelsContext` so `usePanelLifecycle`'s `removePanel` can clear
 * the editor when its target panel disappears.
 *
 * - `startPanelTitleEdit(panel)` — focus the rename input on `panel`.
 * - `cancelPanelTitleEdit()` — discard the draft.
 * - `commitPanelTitleEdit()` — write the (trimmed) draft to the
 *   panel's `title`, falling back to the panel's id when the draft
 *   is empty.
 */
export function usePanelTitleEditor() {
  const {
    setPanels,
    editingPanelId,
    setEditingPanelId,
    panelTitleDraft,
    setPanelTitleDraft,
  } = usePanels();

  const startPanelTitleEdit = (panel: PlotPanelState) => {
    setEditingPanelId(panel.id);
    setPanelTitleDraft(panel.title);
  };

  const cancelPanelTitleEdit = () => {
    setEditingPanelId(null);
    setPanelTitleDraft("");
  };

  const commitPanelTitleEdit = () => {
    if (!editingPanelId) {
      return;
    }
    const panelId = editingPanelId;
    const trimmed = panelTitleDraft.trim();
    setPanels((prev) =>
      prev.map((panel) =>
        panel.id === panelId
          ? { ...panel, title: trimmed.length > 0 ? trimmed : panel.id }
          : panel
      )
    );
    setEditingPanelId(null);
    setPanelTitleDraft("");
  };

  return {
    startPanelTitleEdit,
    cancelPanelTitleEdit,
    commitPanelTitleEdit,
  };
}
