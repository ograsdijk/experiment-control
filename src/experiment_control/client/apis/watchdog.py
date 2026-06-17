from __future__ import annotations

from typing import Any

from ._base import ProcessRpcFacade
from ..types import Json


class WatchdogAPI(ProcessRpcFacade):
    def __init__(self, client, *, process_id: str = "watchdog") -> None:  # type: ignore[no-untyped-def]
        super().__init__(client, process_id=process_id)

    def status(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self.call("watchdog.status", {}, timeout_ms=timeout_ms, retries=retries)

    def enable(
        self,
        watchdog_id: str,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call(
            "watchdog.enable",
            {"watchdog_id": str(watchdog_id)},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def disable(
        self,
        watchdog_id: str,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call(
            "watchdog.disable",
            {"watchdog_id": str(watchdog_id)},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def enable_all(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self.call("watchdog.enable_all", {}, timeout_ms=timeout_ms, retries=retries)

    def disable_all(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self.call("watchdog.disable_all", {}, timeout_ms=timeout_ms, retries=retries)

    def clear_latch(
        self,
        *,
        watchdog_id: str | None = None,
        rule: str | None = None,
        all: bool = False,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        params: Json = {}
        if all:
            params["all"] = True
        if watchdog_id is not None:
            params["watchdog_id"] = str(watchdog_id)
        if rule is not None:
            params["rule"] = str(rule)
        return self.call(
            "watchdog.clear_latch", params, timeout_ms=timeout_ms, retries=retries
        )
