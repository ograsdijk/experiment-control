import { prettifyOutputId } from "../stream/output_labels";
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
      prev.map((panel) => {
        if (panel.id !== panelId) {
          return panel;
        }
        if (trimmed.length > 0) {
          // A typed title is the user's: drop the auto flag so nothing
          // regenerates it later.
          const next = { ...panel, title: trimmed };
          delete next.titleAuto;
          return next;
        }
        // Clearing the field asks for the derived name back, not "panel-3".
        const outputId =
          "outputId" in panel && typeof panel.outputId === "string"
            ? panel.outputId
            : "";
        return {
          ...panel,
          title: prettifyOutputId(outputId) || panel.id,
          titleAuto: true,
        };
      })
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
