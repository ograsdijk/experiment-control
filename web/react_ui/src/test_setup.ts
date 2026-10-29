/**
 * Vitest setup, run before every test file.
 *
 * Floating UI (Mantine's Tooltip, Popover, Select, Combobox, Menu) asks every
 * element it positions whether it is in the browser's top layer, with
 * `element.matches(":popover-open")` and `element.matches(":modal")`. jsdom's
 * selector engine (nwsapi) answers `:modal` by walking the whole document
 * for every call, which cost ~80 s per open tooltip and made
 * HoverTooltip.test.tsx alone take ten minutes. jsdom has no top layer, so the
 * answer is always "no"; give it directly.
 */
if (typeof Element !== "undefined") {
  const TOP_LAYER = new Set([":popover-open", ":modal"]);
  const nativeMatches = Element.prototype.matches;
  Element.prototype.matches = function matches(this: Element, selector: string): boolean {
    return TOP_LAYER.has(selector) ? false : nativeMatches.call(this, selector);
  };
}
