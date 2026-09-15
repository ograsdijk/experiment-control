"""Compact, terminal-safe status rendering for the resource navigator."""

from __future__ import annotations

from rich.text import Text

from .theme import DIM_TEXT, FAULT_RED, HEALTHY_GREEN, MUTED_TEXT, WARNING_ORANGE

_LINK_GLYPHS = {
    "ONLINE": ("●", HEALTHY_GREEN),
    "DISCONNECTED": ("○", MUTED_TEXT),
    "STALE": ("◐", WARNING_ORANGE),
    "": ("—", DIM_TEXT),
}
_HEALTH_GLYPHS = {
    "healthy": ("◆", HEALTHY_GREEN),
    "degraded": ("▲", WARNING_ORANGE),
    "stale": ("△", WARNING_ORANGE),
    "failed": ("×", f"bold {FAULT_RED}"),
    "neutral": ("·", DIM_TEXT),
    "transition": ("◇", WARNING_ORANGE),
}
_RUN_GLYPHS = {
    "RUNNING": ("▶", HEALTHY_GREEN),
    "STARTING": ("▷", WARNING_ORANGE),
    "STOPPING": ("◼", WARNING_ORANGE),
    "STOPPED": ("■", MUTED_TEXT),
    "EXITED": ("□", MUTED_TEXT),
    "FAILED": ("×", f"bold {FAULT_RED}"),
    "CRASHLOOP": ("↻", f"bold {FAULT_RED}"),
}


def state_name(value: object | None) -> str:
    """Normalize enum-like status values without coupling the TUI to enums."""
    return str(value or "").upper().rsplit(".", 1)[-1]


def render_link_state(link: str | None, *, failure_related: bool = False) -> Text:
    """Render manager/device communication state as a single indicator."""
    link = state_name(link)
    if link == "OFFLINE":
        return Text("×", style=FAULT_RED if failure_related else MUTED_TEXT)
    glyph, style = _LINK_GLYPHS.get(link, ("?", WARNING_ORANGE))
    return Text(glyph, style=style)


def render_health_state(health: str | None) -> Text:
    """Render derived resource health with a distinct indicator family."""
    glyph, style = _HEALTH_GLYPHS.get(
        str(health or "").lower(), ("?", MUTED_TEXT)
    )
    return Text(glyph, style=style)


def render_run_state(state: str | None) -> Text:
    """Render managed-process lifecycle without mixing in driver state."""
    state = state_name(state)
    glyph, style = _RUN_GLYPHS.get(state, ("?", MUTED_TEXT))
    return Text(glyph, style=style)


def format_age(age_s: float | None) -> str:
    """Keep heartbeat age compact while preserving numeric sorting separately."""
    if age_s is None:
        return ""
    if age_s < 10:
        return f"{age_s:.1f}s"
    if age_s < 60:
        return f"{age_s:.0f}s"
    return f"{age_s / 60:.1f}m"
