from __future__ import annotations

from typing import Any, Protocol


class ManagerProtocol(Protocol):
    def call(self, payload: dict[str, Any], *, timeout_ms: int | None = None) -> dict[str, Any] | None: ...

    def get_latest(self, device_id: str, signal: str) -> dict[str, Any] | None: ...

    def get_latest_process(self, process_id: str, signal: str) -> dict[str, Any] | None: ...

    def drain_telemetry(
        self,
        *,
        max_messages: int | None = 1000,
        max_duration_s: float | None = 0.1,
    ) -> dict[str, Any]: ...

    def publish_event(
        self,
        *,
        topic: str,
        payload: dict[str, Any],
        include_process_id: bool = True,
        include_ts: bool = True,
        severity: str | None = None,
        device_id: str | None = None,
    ) -> None: ...
