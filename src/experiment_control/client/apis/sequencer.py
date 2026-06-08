from __future__ import annotations

from typing import Any

from ._base import ClientFacadeBase
from ..types import Json


class SequencerAPI(ClientFacadeBase):
    def __init__(self, client, *, process_id: str = "sequencer") -> None:  # type: ignore[no-untyped-def]
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
        return self.call("sequencer.status", {}, timeout_ms=timeout_ms, retries=retries)

    def loaded_yaml(
        self,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call("sequencer.loaded_yaml", {}, timeout_ms=timeout_ms, retries=retries)

    def validate(
        self,
        *,
        path: str | None = None,
        text: str | None = None,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        params = _build_text_or_path(path=path, text=text)
        return self.call("sequencer.validate", params, timeout_ms=timeout_ms, retries=retries)

    def preflight(
        self,
        *,
        path: str | None = None,
        text: str | None = None,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        params = _build_text_or_path(path=path, text=text)
        return self.call("sequencer.preflight", params, timeout_ms=timeout_ms, retries=retries)

    def load(
        self,
        *,
        path: str | None = None,
        text: str | None = None,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        params = _build_text_or_path(path=path, text=text)
        return self.call("sequencer.load", params, timeout_ms=timeout_ms, retries=retries)

    def start(
        self,
        *,
        sequence_id: str | None = None,
        repeat_count: int | None = None,
        continuous: bool | None = None,
        vars_override: Json | None = None,
        adaptive: Json | None = None,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        params: Json = {}
        if sequence_id is not None:
            params["sequence_id"] = str(sequence_id)
        if repeat_count is not None:
            params["repeat_count"] = int(repeat_count)
        if continuous is not None:
            params["continuous"] = bool(continuous)
        if vars_override is not None:
            params["vars_override"] = dict(vars_override)
        if adaptive is not None:
            params["adaptive"] = dict(adaptive)
        return self.call("sequencer.start", params, timeout_ms=timeout_ms, retries=retries)

    def pause(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self.call("sequencer.pause", {}, timeout_ms=timeout_ms, retries=retries)

    def resume(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self.call("sequencer.resume", {}, timeout_ms=timeout_ms, retries=retries)

    def stop(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self.call("sequencer.stop", {}, timeout_ms=timeout_ms, retries=retries)

    def library_list(
        self,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call("sequencer.library.list", {}, timeout_ms=timeout_ms, retries=retries)

    def library_reload(
        self,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call("sequencer.library.reload", {}, timeout_ms=timeout_ms, retries=retries)

    def library_load(
        self,
        *,
        sequence_id: str,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call(
            "sequencer.library.load",
            {"sequence_id": str(sequence_id)},
            timeout_ms=timeout_ms,
            retries=retries,
        )


def _build_text_or_path(*, path: str | None, text: str | None) -> Json:
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

