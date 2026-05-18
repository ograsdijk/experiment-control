/**
 * Shared drag-and-drop primitives used by both the App-level UI
 * drag controller and the per-grid components that consume the
 * resulting state.
 *
 * - `UiDragData` — discriminated union for every kind of drag
 *   payload the app supports. New kinds need a case in
 *   `useUiDragController`'s drag-end handler.
 * - `panelSortableId` / `deviceSortableId` — small prefix-encoders
 *   for `<SortableContext>` ids so the over/active payloads can be
 *   round-tripped back into entity ids without ambiguity.
 * - `parseSortablePrefixedId` — inverse of the encoders; returns
 *   `null` for any non-string or wrong-prefix id.
 */

export type UiDragData =
  | { kind: "device"; deviceId: string }
  | { kind: "panel"; panelId: string }
  | { kind: "command-deck-entry"; entryId: string; groupName: string }
  | { kind: "signal"; deviceId: string; signal: string }
  | {
      kind: "trace";
      deviceId: string;
      signal: string;
      originPanelId?: string;
    };

export function deviceSortableId(deviceId: string): string {
  return `device:${deviceId}`;
}

export function panelSortableId(panelId: string): string {
  return `panel:${panelId}`;
}

export function parseSortablePrefixedId(
  raw: string | number,
  prefix: string
): string | null {
  if (typeof raw !== "string") {
    return null;
  }
  if (!raw.startsWith(prefix)) {
    return null;
  }
  const suffix = raw.slice(prefix.length);
  return suffix.length > 0 ? suffix : null;
}
