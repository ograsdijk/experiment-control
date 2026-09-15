from __future__ import annotations

from typing import Any

_SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "apikey",
    "privatekey",
    "credential",
)
REDACTED_VALUE = "*** redacted ***"


def _is_sensitive_key(key: str) -> bool:
    normalized = "".join(character for character in key.casefold() if character.isalnum())
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def redact_config(value: Any, *, key: str = "") -> Any:
    """Return a recursively copied configuration value with secrets removed."""
    if key and _is_sensitive_key(key):
        return REDACTED_VALUE
    if isinstance(value, dict):
        return {
            str(child_key): redact_config(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [redact_config(item) for item in value]
    if isinstance(value, tuple):
        return [redact_config(item) for item in value]
    return value
