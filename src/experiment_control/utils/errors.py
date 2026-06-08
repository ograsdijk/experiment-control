"""Shared error-code classifications.

Centralizes sets of error codes that multiple modules need to recognize
(e.g. for retry/transient-failure suppression), so the classifications
cannot drift independently across the codebase.
"""

from __future__ import annotations

from typing import FrozenSet

# Error codes that indicate a transient failure to fetch device capabilities.
# These typically resolve on their own (driver starting up, brief RPC stall,
# busy gateway, etc.), so callers should suppress noisy logging / retry rather
# than treat them as hard failures.
TRANSIENT_CAPABILITIES_ERROR_CODES: FrozenSet[str] = frozenset(
    {
        "device_rpc_timeout",
        "device_starting",
        "device_stopping",
        "device_rpc_not_ready",
        "driver_not_running",
        "gateway_busy",
        "gateway_timeout",
    }
)
