from __future__ import annotations

from typing import Any

from ._base import ClientFacadeBase
from ..types import Json


class HdfAPI(ClientFacadeBase):
    def __init__(self, client, *, process_id: str = "hdf_writer") -> None:  # type: ignore[no-untyped-def]
        super().__init__(client)
        self.process_id = str(process_id)

    def call(
        self,
        action: str,
        params: Json | None = None,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._call_type(
            "manager.processes.rpc",
            process_id=self.process_id,
            request={"type": str(action), "params": dict(params or {})},
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
        return self._call_type(
            "manager.processes.rpc",
            process_id=self.process_id,
            request={"type": str(action), "params": dict(params or {})},
            timeout_ms=timeout_ms,
            retries=retries,
            expect_ok=False,
        )

    def status(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self.call("hdf.status", {}, timeout_ms=timeout_ms, retries=retries)

    def writing_start(
        self,
        *,
        filename: str | None = None,
        disabled_devices: list[str] | None = None,
        measurement_profile: str | None = None,
        measurement_values: Json | None = None,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        params: Json = {}
        if filename is not None:
            params["filename"] = str(filename)
        if disabled_devices is not None:
            params["disabled_devices"] = [str(item) for item in disabled_devices]
        if measurement_profile is not None:
            params["measurement_profile"] = str(measurement_profile)
        if measurement_values is not None:
            params["measurement_values"] = dict(measurement_values)
        return self.call(
            "hdf.writing.start", params, timeout_ms=timeout_ms, retries=retries
        )

    def writing_stop(
        self,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call("hdf.writing.stop", {}, timeout_ms=timeout_ms, retries=retries)

    def rotate(
        self,
        *,
        filename: str | None = None,
        disabled_devices: list[str] | None = None,
        measurement_profile: str | None = None,
        measurement_values: Json | None = None,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        params: Json = {}
        if filename is not None:
            params["filename"] = str(filename)
        if disabled_devices is not None:
            params["disabled_devices"] = [str(item) for item in disabled_devices]
        if measurement_profile is not None:
            params["measurement_profile"] = str(measurement_profile)
        if measurement_values is not None:
            params["measurement_values"] = dict(measurement_values)
        return self.call("hdf.rotate", params, timeout_ms=timeout_ms, retries=retries)

    def devices_get(
        self,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call("hdf.devices.get", {}, timeout_ms=timeout_ms, retries=retries)

    def devices_enable(
        self,
        device_ids: list[str],
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call(
            "hdf.devices.enable",
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
            "hdf.devices.disable",
            {"device_ids": [str(item) for item in device_ids]},
            timeout_ms=timeout_ms,
            retries=retries,
        )
