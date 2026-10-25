// Colormap shared by the waterfall and 2-D bin heatmaps.
//
// Heatmap redraws colour every cell of the grid, which can be several
// hundred thousand pixels. The ramp is precomputed into a 256-entry table so
// the per-pixel work is one index and three byte copies, with no allocation.

const STOPS: ReadonlyArray<readonly [number, number, number, number]> = [
  [0.0, 10, 12, 36],
  [0.25, 51, 85, 137],
  [0.5, 28, 170, 156],
  [0.75, 168, 220, 50],
  [1.0, 255, 243, 156],
];

const LUT_SIZE = 256;

function clamp01(value: number): number {
  if (!Number.isFinite(value) || value <= 0) {
    return 0;
  }
  return value >= 1 ? 1 : value;
}

/** Exact colour for a normalised value; used to build the lookup table. */
export function heatmapColorExact(tRaw: number): [number, number, number] {
  const t = clamp01(tRaw);
  for (let i = 0; i < STOPS.length - 1; i += 1) {
    const a = STOPS[i];
    const b = STOPS[i + 1];
    if (t < a[0] || t > b[0]) {
      continue;
    }
    const local = (t - a[0]) / Math.max(1e-9, b[0] - a[0]);
    return [
      Math.trunc(a[1] + (b[1] - a[1]) * local),
      Math.trunc(a[2] + (b[2] - a[2]) * local),
      Math.trunc(a[3] + (b[3] - a[3]) * local),
    ];
  }
  const tail = STOPS[STOPS.length - 1];
  return [tail[1], tail[2], tail[3]];
}

const HEATMAP_LUT = (() => {
  const lut = new Uint8Array(LUT_SIZE * 3);
  for (let i = 0; i < LUT_SIZE; i += 1) {
    const [r, g, b] = heatmapColorExact(i / (LUT_SIZE - 1));
    lut[i * 3] = r;
    lut[i * 3 + 1] = g;
    lut[i * 3 + 2] = b;
  }
  return lut;
})();

/** Writes the opaque RGBA pixel for normalised value `t` at byte offset `idx`. */
export function writeHeatmapPixel(data: Uint8ClampedArray, idx: number, t: number): void {
  const entry = Math.round(clamp01(t) * (LUT_SIZE - 1)) * 3;
  data[idx] = HEATMAP_LUT[entry];
  data[idx + 1] = HEATMAP_LUT[entry + 1];
  data[idx + 2] = HEATMAP_LUT[entry + 2];
  data[idx + 3] = 255;
}

/**
 * Colours `count` values from `values[start]` into consecutive opaque RGBA
 * pixels at byte `offset`. Same result as `writeHeatmapPixel` per value with
 * `t = (value - zMin) / span`, without a call per pixel.
 */
export function writeHeatmapRow(
  data: Uint8ClampedArray,
  offset: number,
  values: ArrayLike<number>,
  start: number,
  count: number,
  zMin: number,
  span: number
): void {
  let idx = offset;
  for (let i = 0; i < count; i += 1) {
    const t = (values[start + i] - zMin) / span;
    const entry =
      Number.isFinite(t) && t > 0 ? Math.round((t >= 1 ? 1 : t) * (LUT_SIZE - 1)) * 3 : 0;
    data[idx] = HEATMAP_LUT[entry];
    data[idx + 1] = HEATMAP_LUT[entry + 1];
    data[idx + 2] = HEATMAP_LUT[entry + 2];
    data[idx + 3] = 255;
    idx += 4;
  }
}
