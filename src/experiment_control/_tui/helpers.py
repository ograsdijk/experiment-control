from __future__ import annotations

from typing import Any

from ..utils.logging_levels import normalize_log_severity, severity_rank


def normalize_log_severity_for_tui(raw: Any) -> str:
    return normalize_log_severity(raw, default="info")


def severity_rank_for_tui(raw: Any) -> int:
    return severity_rank(raw, default="info")


def normalize_topic_set(
    raw: list[str] | tuple[str, ...] | set[str] | None,
    *,
    default: set[str] | frozenset[str],
) -> set[str]:
    if raw is None:
        return set(default)
    out: set[str] = set()
    for item in raw:
        topic = str(item or "").strip()
        if topic:
            out.add(topic)
    return out
