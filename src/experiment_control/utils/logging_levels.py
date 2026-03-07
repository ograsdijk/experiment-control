from __future__ import annotations

import logging
from typing import Any

LOG_SEVERITY_NAMES: tuple[str, ...] = (
    "debug",
    "info",
    "warning",
    "error",
    "critical",
)


def _normalize_candidate(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text == "warn":
        return "warning"
    return text


def is_valid_log_severity(raw: Any) -> bool:
    return _normalize_candidate(raw) in LOG_SEVERITY_NAMES


def normalize_log_severity(raw: Any, *, default: str = "info") -> str:
    default_norm = _normalize_candidate(default)
    if default_norm not in LOG_SEVERITY_NAMES:
        default_norm = "info"
    candidate = _normalize_candidate(raw if raw is not None else default_norm)
    if not candidate:
        return default_norm
    if candidate not in LOG_SEVERITY_NAMES:
        return default_norm
    return candidate


def severity_rank(raw: Any, *, default: str = "info") -> int:
    severity = normalize_log_severity(raw, default=default)
    return int(getattr(logging, severity.upper()))
