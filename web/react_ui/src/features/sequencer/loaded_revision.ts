/**
 * The backend counts every load of a sequence (`loaded_revision` in
 * `sequencer.status`, `revision` in `sequencer.loaded_yaml`). The editor
 * remembers the revision its text was fetched at, so it can tell when
 * someone else loaded a different sequence.
 */
export type RevisionInput = {
  /** `loaded_revision` from status; null from an older backend. */
  statusRevision: number | null;
  /** Revision of the text the editor was last synced to; null if unknown. */
  editorRevision: number | null;
  /** Something is loaded in the sequencer. */
  loaded: boolean;
};

/**
 * Whether the editor's lines can be trusted to match the loaded sequence.
 * Without a status revision (older backend) this stays true: the dirty flag
 * is then the only guard.
 */
export function editorMatchesLoadedRevision({
  statusRevision,
  editorRevision,
}: Pick<RevisionInput, "statusRevision" | "editorRevision">): boolean {
  return statusRevision === null || editorRevision === statusRevision;
}

export type RevisionAction = "none" | "refetch" | "notice";

/**
 * What to do about a mismatch: a clean editor silently takes the new text,
 * a dirty one is left alone and the operator is told.
 */
export function revisionAction(input: RevisionInput & { dirty: boolean }): RevisionAction {
  if (!input.loaded || input.statusRevision === null) {
    return "none";
  }
  if (input.editorRevision === input.statusRevision) {
    return "none";
  }
  return input.dirty ? "notice" : "refetch";
}
