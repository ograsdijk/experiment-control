from __future__ import annotations

from typing import Any

from ._base import ProcessRpcFacade
from ..types import Json


class InterlockAPI(ProcessRpcFacade):
    def __init__(self, client, *, process_id: str = "interlock") -> None:  # type: ignore[no-untyped-def]
        super().__init__(client, process_id=process_id)

    def list(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self.call("interlock.list", {}, timeout_ms=timeout_ms, retries=retries)

    def status(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self.call("interlock.status", {}, timeout_ms=timeout_ms, retries=retries)

    def load(
        self,
        *,
        path: str | None = None,
        text: str | None = None,
        replace: bool | None = None,
        enable: bool | None = None,
        source: str | None = None,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        params: Json = _text_or_path(path=path, text=text)
        if replace is not None:
            params["replace"] = bool(replace)
        if enable is not None:
            params["enable"] = bool(enable)
        if source is not None:
            params["source"] = str(source)
        return self.call("interlock.load", params, timeout_ms=timeout_ms, retries=retries)

    def enable(
        self,
        interceptor_id: str,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call(
            "interlock.enable",
            {"interceptor_id": str(interceptor_id)},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def disable(
        self,
        interceptor_id: str,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call(
            "interlock.disable",
            {"interceptor_id": str(interceptor_id)},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def enable_rule(
        self,
        interceptor_id: str,
        rule_id: str,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call(
            "interlock.enable_rule",
            {"interceptor_id": str(interceptor_id), "rule_id": str(rule_id)},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def disable_rule(
        self,
        interceptor_id: str,
        rule_id: str,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call(
            "interlock.disable_rule",
            {"interceptor_id": str(interceptor_id), "rule_id": str(rule_id)},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def enable_all(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self.call("interlock.enable_all", {}, timeout_ms=timeout_ms, retries=retries)

    def disable_all(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self.call("interlock.disable_all", {}, timeout_ms=timeout_ms, retries=retries)


def _text_or_path(*, path: str | None, text: str | None) -> Json:
    has_path = path is not None and str(path).strip() != ""
    has_text = text is not None
    if has_path and has_text:
        raise ValueError("provide either path or text, not both")
    if not has_path and not has_text:
        raise ValueError("either path or text is required")
    if has_path:
        return {"path": str(path)}
    assert text is not None
    return {"text": str(text)}
