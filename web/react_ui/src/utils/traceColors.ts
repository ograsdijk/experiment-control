const TRACE_COLORS = [
  "#0E9F9A",
  "#F3B04B",
  "#D8634F",
  "#3A6EA5",
  "#6C5EAE",
];

export function traceColorAt(index: number): string {
  const safeIndex = Number.isFinite(index) ? Math.trunc(index) : 0;
  const normalized =
    ((safeIndex % TRACE_COLORS.length) + TRACE_COLORS.length) %
    TRACE_COLORS.length;
  return TRACE_COLORS[normalized];
}

const COLOR_WITH_ALPHA_CACHE = new Map<string, string>();
const COLOR_WITH_ALPHA_CACHE_MAX = 256;

export function colorWithAlpha(hexColor: string, alpha: number): string {
  const cacheKey = `${hexColor}|${alpha}`;
  const cached = COLOR_WITH_ALPHA_CACHE.get(cacheKey);
  if (cached !== undefined) {
    return cached;
  }
  const hex = hexColor.trim().replace(/^#/, "");
  const expanded =
    hex.length === 3
      ? hex
          .split("")
          .map((ch) => `${ch}${ch}`)
          .join("")
      : hex;
  if (!/^[0-9a-fA-F]{6}$/.test(expanded)) {
    if (COLOR_WITH_ALPHA_CACHE.size < COLOR_WITH_ALPHA_CACHE_MAX) {
      COLOR_WITH_ALPHA_CACHE.set(cacheKey, hexColor);
    }
    return hexColor;
  }
  const r = Number.parseInt(expanded.slice(0, 2), 16);
  const g = Number.parseInt(expanded.slice(2, 4), 16);
  const b = Number.parseInt(expanded.slice(4, 6), 16);
  const a = Math.max(0, Math.min(1, alpha));
  const result = `rgba(${r}, ${g}, ${b}, ${a})`;
  if (COLOR_WITH_ALPHA_CACHE.size < COLOR_WITH_ALPHA_CACHE_MAX) {
    COLOR_WITH_ALPHA_CACHE.set(cacheKey, result);
  }
  return result;
}
