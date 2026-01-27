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

export function colorWithAlpha(hexColor: string, alpha: number): string {
  const hex = hexColor.trim().replace(/^#/, "");
  const expanded =
    hex.length === 3
      ? hex
          .split("")
          .map((ch) => `${ch}${ch}`)
          .join("")
      : hex;
  if (!/^[0-9a-fA-F]{6}$/.test(expanded)) {
    return hexColor;
  }
  const r = Number.parseInt(expanded.slice(0, 2), 16);
  const g = Number.parseInt(expanded.slice(2, 4), 16);
  const b = Number.parseInt(expanded.slice(4, 6), 16);
  const a = Math.max(0, Math.min(1, alpha));
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}
