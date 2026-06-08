from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..types import Json

if TYPE_CHECKING:
    from ..stack import StackClient


class ClientFacadeBase:
    def __init__(self, client: "StackClient") -> None:
        self._client = client

    def _request_raw(
        self,
        payload: Json,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Json:
        return self._client.rpc(payload, timeout_ms=timeout_ms, retries=retries, expect_ok=False)

    def _request_result(
        self,
        payload: Json,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self._client.rpc(payload, timeout_ms=timeout_ms, retries=retries, expect_ok=True)

    def _call_type(
        self,
        request_type: str,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
        expect_ok: bool = True,
        **kwargs: Any,
    ) -> Any:
        payload: Json = {"type": str(request_type), **kwargs}
        if expect_ok:
            return self._request_result(payload, timeout_ms=timeout_ms, retries=retries)
        return self._request_raw(payload, timeout_ms=timeout_ms, retries=retries)

