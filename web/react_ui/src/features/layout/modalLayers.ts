/**
 * Modal stacking scale.
 *
 * - Top-level modals (opened from the header, sidebar or page) use Mantine's
 *   default modal z-index, 200, and pass no `zIndex`.
 * - Child modals opened from inside another modal (a command form, a confirm
 *   dialog, an expanded graph) use CHILD_MODAL_Z_INDEX so they always stack
 *   above their parent, independent of portal mount order.
 * - Mantine popovers, selects and tooltips default to 300, so they render
 *   above both tiers without per-component overrides.
 */
export const CHILD_MODAL_Z_INDEX = 250;
