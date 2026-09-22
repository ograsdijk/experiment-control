from __future__ import annotations

import time
from typing import Any

from ._base import ClientFacadeBase
from ..errors import RpcResponseError
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
        wait: bool = False,
        wait_timeout_s: float = 120.0,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        """Stop writing. The writer replies once the stop is accepted and closes
        the file in the background; ``wait=True`` blocks until it is closed
        (see `wait_for_file_op`)."""
        result = self.call("hdf.writing.stop", {}, timeout_ms=timeout_ms, retries=retries)
        return self._maybe_wait(result, wait=wait, wait_timeout_s=wait_timeout_s)

    def rotate(
        self,
        *,
        filename: str | None = None,
        disabled_devices: list[str] | None = None,
        measurement_profile: str | None = None,
        measurement_values: Json | None = None,
        wait: bool = False,
        wait_timeout_s: float = 120.0,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        """Rotate to a new file. Replies once accepted; the swap finishes in
        the background. ``wait=True`` blocks until it is done."""
        params: Json = {}
        if filename is not None:
            params["filename"] = str(filename)
        if disabled_devices is not None:
            params["disabled_devices"] = [str(item) for item in disabled_devices]
        if measurement_profile is not None:
            params["measurement_profile"] = str(measurement_profile)
        if measurement_values is not None:
            params["measurement_values"] = dict(measurement_values)
        result = self.call("hdf.rotate", params, timeout_ms=timeout_ms, retries=retries)
        return self._maybe_wait(result, wait=wait, wait_timeout_s=wait_timeout_s)

    def wait_for_file_op(
        self,
        *,
        timeout_s: float = 120.0,
        poll_interval_s: float = 0.5,
    ) -> Json | None:
        """Block until no async stop/rotate is in flight; return its outcome
        (``hdf.status`` ``last_file_op``). Raises `RpcResponseError` if the op
        failed and `TimeoutError` if it is still running after ``timeout_s``."""
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            status = self.status()
            # Writers without async file ops report no `file_op` at all.
            if not isinstance(status, dict) or status.get("file_op") is None:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"hdf_writer file op still running after {timeout_s} s: "
                    f"{status.get('file_op')!r}"
                )
            time.sleep(max(0.05, float(poll_interval_s)))
        outcome = status.get("last_file_op") if isinstance(status, dict) else None
        if isinstance(outcome, dict) and outcome.get("ok") is False:
            error = outcome.get("error")
            error = error if isinstance(error, dict) else {}
            raise RpcResponseError(
                code=str(error.get("code") or f"{outcome.get('op')}_failed"),
                message=str(error.get("message") or "hdf file op failed"),
                details=outcome,
            )
        return outcome if isinstance(outcome, dict) else None

    def _maybe_wait(self, result: Any, *, wait: bool, wait_timeout_s: float) -> Any:
        if not wait or not isinstance(result, dict) or result.get("accepted") is not True:
            return result
        return {**result, "file_op": self.wait_for_file_op(timeout_s=wait_timeout_s)}

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
