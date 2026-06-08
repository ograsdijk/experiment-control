"""Canonical predicates for parsing manager command responses.

Manager responses follow one of two shapes:

    {"ok": True/False, ...}          # newer, preferred convention
    {"status": "OK"|"ERROR", ...}    # older convention; both still in use

When both keys are present, ``"ok"`` wins. ``status`` comparison is
exact-case (``"OK"`` / ``"ERROR"``). If a device ever emits lowercase
``"ok"`` as a status string, the fix belongs in that device's driver,
not in this predicate; broad acceptance hides the device-side bug.
"""

from __future__ import annotations

from typing import Any


def is_response_ok(resp: Any) -> bool:
    """Return True iff ``resp`` is a manager command response indicating success."""
    if not isinstance(resp, dict):
        return False
    if "ok" in resp:
        return bool(resp.get("ok"))
    return resp.get("status") == "OK"
