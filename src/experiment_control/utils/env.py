"""Typed environment-variable helpers.

Replaces inline ``int(os.environ.get(KEY, "default"))`` patterns with
typed, defaulted helpers. Common to all helpers:

* Return ``default`` when the var is unset.
* Never raise on a malformed value — operator-supplied env vars
  should never crash the gateway on a typo.

Per-helper specifics (see each function's docstring for the full
contract):

* ``env_int`` / ``env_float`` strip surrounding whitespace and return
  ``default`` on parse failure.
* ``env_bool`` strips and lower-cases; returns ``True`` only for
  ``"1"|"true"|"yes"|"on"``; any other set value returns ``False``
  even if ``default=True`` (matches the pre-existing
  ``EXPERIMENT_CONTROL_SERVE_UI`` semantics — see ``env_bool``).

There is intentionally no ``env_str`` helper — every in-tree string
env var caller follows the ``os.environ.get(KEY, "").strip()`` shape
which is short enough to keep inline; introducing a helper would
either need a ``strip=True`` parameter (adding complexity) or would
silently change the existing strip behaviour.

Centralising these means future additions (e.g. an env-var audit log
or a ``--env-strict`` flag that DOES raise) only need to change one
file.
"""

from __future__ import annotations

import os

__all__ = ("env_bool", "env_float", "env_int")


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

    Behaviour:

    * **Unset** -> returns ``default``.
    * **Set** -> returns ``True`` for the truthy strings ``"1"``,
      ``"true"``, ``"yes"``, ``"on"`` (case-insensitive, whitespace-
      stripped); returns ``False`` for every other value, regardless
      of ``default``.

    This matches the behaviour of the previous ``_env_bool`` helper in
    ``fastapi/app.py`` (which this function replaces) and the semantics
    operators expect for the existing ``EXPERIMENT_CONTROL_SERVE_UI``
    feature flag: setting the var to ``"0"``, ``"false"``, ``"off"``,
    ``"no"`` (or anything else non-truthy) means "explicitly disabled",
    even if the call site passes ``default=True``.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
