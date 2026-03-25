from __future__ import annotations

from typing import Any

from ._base import ClientFacadeBase
from ..types import Json


class ManagerAPI(ClientFacadeBase):
    def identity(
        self,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._request_result(
            {"type": "manager.info.identity"},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def shutdown(
        self,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._request_result(
            {"type": "manager.control.shutdown"},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def cleanup_orphans(
        self,
        *,
        dry_run: bool = True,
        stale_only: bool = True,
        timeout_s: float = 2.0,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._request_result(
            {
                "type": "manager.control.cleanup_orphans",
                "params": {
                    "dry_run": bool(dry_run),
                    "stale_only": bool(stale_only),
                    "timeout_s": float(timeout_s),
                },
            },
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def command_journal_status(
        self,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._request_result(
            {"type": "manager.commands.journal.status"},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def command_journal_tail(
        self,
        *,
        params: Json | None = None,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._request_result(
            {"type": "manager.commands.journal.tail", "params": dict(params or {})},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def log_tail(
        self,
        *,
        params: Json | None = None,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._request_result(
            {"type": "manager.logs.tail", "params": dict(params or {})},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def telemetry_snapshot(
        self,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._request_result(
            {"type": "manager.telemetry.snapshot"},
            timeout_ms=timeout_ms,
            retries=retries,
        )


