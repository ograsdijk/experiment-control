export const FOR_SOURCE_OPTIONS = [
  { value: "generator", label: "Generator" },
  { value: "direct", label: "Direct (advanced)" },
] as const;

export const FOR_GENERATOR_OPTIONS = [
  { value: "range", label: "range" },
  { value: "linspace", label: "linspace" },
  { value: "triangle", label: "triangle" },
  { value: "logspace", label: "logspace" },
  { value: "geomspace", label: "geomspace" },
  { value: "values", label: "values" },
  { value: "scan2d", label: "scan2d" },
] as const;

export const SCALAR_FOR_FIELDS = ["value", "index", "u", "count"] as const;
export const SCAN2D_FOR_FIELDS = [
  "x",
  "y",
  "row",
  "col",
  "index",
  "u",
  "v",
  "count",
] as const;

export const SCAN2D_FORM_OPTIONS = [
  { value: "shorthand", label: "Shorthand" },
  { value: "explicit", label: "Explicit" },
] as const;

export const SCAN2D_RESOLUTION_OPTIONS = [
  { value: "steps", label: "Steps" },
  { value: "pitch", label: "Pitch" },
] as const;

export const SCAN2D_PATTERN_OPTIONS = [
  { value: "raster", label: "raster" },
  { value: "serpentine", label: "serpentine" },
  { value: "random", label: "random" },
  { value: "center_out", label: "center_out" },
] as const;

export const SCAN2D_ORDER_OPTIONS = [
  { value: "row_major", label: "row_major" },
  { value: "col_major", label: "col_major" },
] as const;

export const ADAPTIVE_CONTROLLER_OPTIONS = [
  { value: "adaptive.adaptive_grid_1d", label: "adaptive.adaptive_grid_1d" },
] as const;

export const ADAPTIVE_BIND_EXTRA_FIELDS = ["trial_index"] as const;

export const ADAPTIVE_METRIC_SOURCE_OPTIONS = [
  { value: "analysis_output", label: "analysis_output" },
  { value: "telemetry", label: "telemetry" },
  { value: "call", label: "call" },
] as const;
