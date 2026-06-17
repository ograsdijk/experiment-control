from __future__ import annotations

from typing import Any

from ._base import ProcessRpcFacade


class InfluxAPI(ProcessRpcFacade):
    def __init__(self, client, *, process_id: str = "influx_writer") -> None:  # type: ignore[no-untyped-def]
        super().__init__(client, process_id=process_id)

    def status(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self.call("influx.status", {}, timeout_ms=timeout_ms, retries=retries)

    def enable(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self.call("influx.enable", {}, timeout_ms=timeout_ms, retries=retries)

    def disable(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self.call("influx.disable", {}, timeout_ms=timeout_ms, retries=retries)

    def flush(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self.call("influx.flush", {}, timeout_ms=timeout_ms, retries=retries)

    def devices_get(
        self, *, timeout_ms: int | None = None, retries: int | None = None
    ) -> Any:
        return self.call("influx.devices.get", {}, timeout_ms=timeout_ms, retries=retries)

    def devices_enable(
        self,
        device_ids: list[str],
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call(
            "influx.devices.enable",
            {"device_ids": [str(item) for item in device_ids]},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def devices_disable(
        self,
        device_ids: list[str],
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call(
            "influx.devices.disable",
            {"device_ids": [str(item) for item in device_ids]},
            timeout_ms=timeout_ms,
            retries=retries,
        )
