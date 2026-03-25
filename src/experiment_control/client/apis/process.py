from __future__ import annotations

from typing import Any

from ._base import ClientFacadeBase
from ..types import Json


class ProcessAPI(ClientFacadeBase):
    def list_status(
        self,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._request_result(
            {"type": "manager.processes.list"},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def get_status(
        self,
        process_id: str,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._request_result(
            {"type": "manager.processes.get", "process_id": str(process_id)},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def start(
        self,
        process_id: str,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._request_result(
            {"type": "manager.processes.start", "process_id": str(process_id)},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def stop(
        self,
        process_id: str,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._request_result(
            {"type": "manager.processes.stop", "process_id": str(process_id)},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def restart(
        self,
        process_id: str,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._request_result(
            {"type": "manager.processes.restart", "process_id": str(process_id)},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def call_raw(
        self,
        process_id: str,
        action: str,
        params: Json | None = None,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Json:
        payload: Json = {
            "type": "manager.processes.rpc",
            "process_id": str(process_id),
            "request": {"type": str(action), "params": dict(params or {})},
        }
        return self._request_raw(payload, timeout_ms=timeout_ms, retries=retries)

    def call(
        self,
        process_id: str,
        action: str,
        params: Json | None = None,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        payload: Json = {
            "type": "manager.processes.rpc",
            "process_id": str(process_id),
            "request": {"type": str(action), "params": dict(params or {})},
        }
        return self._request_result(payload, timeout_ms=timeout_ms, retries=retries)

    def capabilities(
        self,
        process_id: str,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call(
            process_id,
            "process.capabilities",
            {},
            timeout_ms=timeout_ms,
            retries=retries,
        )


class ProcessHandle:
    def __init__(self, api: ProcessAPI, process_id: str) -> None:
        self._api = api
        self.process_id = str(process_id)

    def start(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self._api.start(self.process_id, timeout_ms=timeout_ms, retries=retries)

    def stop(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self._api.stop(self.process_id, timeout_ms=timeout_ms, retries=retries)

    def restart(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self._api.restart(self.process_id, timeout_ms=timeout_ms, retries=retries)

    def status(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self._api.get_status(self.process_id, timeout_ms=timeout_ms, retries=retries)

    def capabilities(
        self,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._api.capabilities(self.process_id, timeout_ms=timeout_ms, retries=retries)

    def call(
        self,
        action: str,
        params: Json | None = None,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._api.call(
            self.process_id,
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
            self.process_id,
            action,
            params,
            timeout_ms=timeout_ms,
            retries=retries,
        )


