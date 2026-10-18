import type { ResourceHealth } from "../features/devices/health";

const HEALTH_GLYPH: Record<ResourceHealth, string> = {
  healthy: "◆",
  degraded: "▲",
  stale: "△",
  failed: "×",
  neutral: "·",
  transition: "◇",
  unknown: "?",
};

/** Health glyph shared by every resource list (same set as the TUI). */
export function HealthMark({ health }: { health: ResourceHealth }) {
  return (
    <span className={`resource-health-mark resource-health-mark-${health}`} title={health}>
      {HEALTH_GLYPH[health]}
    </span>
  );
}
