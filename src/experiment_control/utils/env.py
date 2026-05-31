"""Typed environment-variable helpers.

Replaces inline ``int(os.environ.get(KEY, "default"))`` patterns with
typed, defaulted helpers. Each helper:

* Returns ``default`` when the var is unset OR cannot be parsed.
* Strips surrounding whitespace before parsing.
* Never raises on a malformed value — operator-supplied env vars
  should never crash the gateway on a typo.

Centralising these means future additions (e.g. an env-var audit log
or a ``--env-strict`` flag that DOES raise) only need to change one
file.
"""

from __future__ import annotations

import os

__all__ = ("env_bool", "env_float", "env_int", "env_str")


def env_str(name: str, default: str = "") -> str:
    """Return the env var ``name``'s value or ``default`` (str)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw


def env_int(name: str, default: int) -> int:
    """Return the env var ``name``'s value parsed as int, or ``default``.

    Returns ``default`` when the var is unset or cannot be parsed as int.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    """Return the env var ``name``'s value parsed as float, or ``default``.

    Returns ``default`` when the var is unset or cannot be parsed as float.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool = False) -> bool:
    """Return the env var ``name``'s value parsed as bool, or ``default``.

    Truthy strings: ``"1"``, ``"true"``, ``"yes"``, ``"on"`` (case-
    insensitive, whitespace-stripped). Everything else (including unset
    OR unparseable) returns ``default``. To be strictly false, set the
    env var to e.g. ``"0"`` or ``"false"``.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
