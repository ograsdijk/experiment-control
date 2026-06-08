from __future__ import annotations

from typing import Any

from ._base import ClientFacadeBase
from ..types import Json


class DeviceAPI(ClientFacadeBase):
    def call_raw(
        self,
        device_id: str,
        action: str,
        params: Json | None = None,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Json:
        return self._call_type(
            "command",
            device_id=str(device_id),
            action=str(action),
            params=dict(params or {}),
            timeout_ms=timeout_ms,
            retries=retries,
            expect_ok=False,
        )

    def call(
        self,
        device_id: str,
        action: str,
        params: Json | None = None,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._call_type(
            "command",
            device_id=str(device_id),
            action=str(action),
            params=dict(params or {}),
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def capabilities(
        self,
        device_id: str,
        *,
        refresh: bool = False,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        action = "refresh_capabilities" if refresh else "capabilities"
        return self.call(device_id, action, {}, timeout_ms=timeout_ms, retries=retries)

    def get(
        self,
        device_id: str,
        *,
        name: str,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call(
            device_id,
            "get",
            {"name": str(name)},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def set(
        self,
        device_id: str,
        *,
        name: str,
        value: Any,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call(
            device_id,
            "set",
            {"name": str(name), "value": value},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def list_status(
        self,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._call_type("device.list_status", timeout_ms=timeout_ms, retries=retries)

    def connect(
        self,
        device_id: str,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._call_type(
            "device.connect",
            device_id=str(device_id),
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def start(
        self,
        device_id: str,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._call_type(
            "device.driver.start",
            device_id=str(device_id),
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def disconnect(
        self,
        device_id: str,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._call_type(
            "device.disconnect",
            device_id=str(device_id),
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def restart(
        self,
        device_id: str,
        *,
        force: bool = False,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._call_type(
            "device.driver.restart",
            device_id=str(device_id),
            force=bool(force),
            timeout_ms=timeout_ms,
            retries=retries,
        )


class DeviceHandle:
    def __init__(self, api: DeviceAPI, device_id: str) -> None:
        self._api = api
        self.device_id = str(device_id)

    def call(
        self,
        action: str,
        params: Json | None = None,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._api.call(
            self.device_id,
            action,
            params,
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def call_raw(
        self,
        action: str,
        params: Json | None = None,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Json:
        return self._api.call_raw(
            self.device_id,
            action,
            params,
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def capabilities(
        self,
        *,
        refresh: bool = False,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._api.capabilities(
            self.device_id,
            refresh=refresh,
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def get(
        self,
        *,
        name: str,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._api.get(
            self.device_id,
            name=name,
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def set(
        self,
        *,
        name: str,
        value: Any,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._api.set(
            self.device_id,
            name=name,
            value=value,
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def connect(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self._api.connect(self.device_id, timeout_ms=timeout_ms, retries=retries)

    def start(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self._api.start(self.device_id, timeout_ms=timeout_ms, retries=retries)

    def disconnect(
        self,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._api.disconnect(self.device_id, timeout_ms=timeout_ms, retries=retries)

    def restart(
        self,
        *,
        force: bool = False,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._api.restart(
            self.device_id,
            force=force,
            timeout_ms=timeout_ms,
            retries=retries,
        )

