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

