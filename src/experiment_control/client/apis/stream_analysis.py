from __future__ import annotations

from typing import Any

from ._base import ProcessRpcFacade
from ..types import Json


class StreamAnalysisAPI(ProcessRpcFacade):
    def __init__(self, client, *, process_id: str = "stream_analysis") -> None:  # type: ignore[no-untyped-def]
        super().__init__(client, process_id=process_id)

    def status(self, *, timeout_ms: int | None = None, retries: int | None = None) -> Any:
        return self.call("stream_analysis.status", {}, timeout_ms=timeout_ms, retries=retries)

    def operators(
        self, *, timeout_ms: int | None = None, retries: int | None = None
    ) -> Any:
        return self.call(
            "stream_analysis.operators", {}, timeout_ms=timeout_ms, retries=retries
        )

    def workspace_list(
        self, *, timeout_ms: int | None = None, retries: int | None = None
    ) -> Any:
        return self.call(
            "stream_analysis.workspace.list", {}, timeout_ms=timeout_ms, retries=retries
        )

    def workspace_get(
        self,
        workspace_id: str,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call(
            "stream_analysis.workspace.get",
            {"workspace_id": str(workspace_id)},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def workspace_snapshot(
        self,
        *,
        workspace_id: str | None = None,
        kinds: list[str] | None = None,
        output_ids: list[str] | None = None,
        max_trace_points: int | None = None,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        params: Json = {}
        if workspace_id is not None:
            params["workspace_id"] = str(workspace_id)
        if kinds is not None:
            params["kinds"] = [str(item) for item in kinds]
        if output_ids is not None:
            params["output_ids"] = [str(item) for item in output_ids]
        if max_trace_points is not None:
            params["max_trace_points"] = int(max_trace_points)
        return self.call(
            "stream_analysis.workspace.snapshot",
            params,
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def workspace_validate(
        self,
        workspace: Json,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        return self.call(
            "stream_analysis.workspace.validate",
            {"workspace": dict(workspace)},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def workspace_put(
        self,
        workspace: Json,
        *,
        expected_revision: int | None = None,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        params: Json = {"workspace": dict(workspace)}
        if expected_revision is not None:
            params["expected_revision"] = int(expected_revision)
        return self.call(
            "stream_analysis.workspace.put",
            params,
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def workspace_delete(
        self,
        workspace_id: str,
        *,
        expected_revision: int | None = None,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        params: Json = {"workspace_id": str(workspace_id)}
        if expected_revision is not None:
            params["expected_revision"] = int(expected_revision)
        return self.call(
            "stream_analysis.workspace.delete",
            params,
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def workspace_reset(
        self,
        *,
        workspace_id: str | None = None,
        node_id: str | None = None,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        params: Json = {}
        if workspace_id is not None:
            params["workspace_id"] = str(workspace_id)
        if node_id is not None:
            params["node_id"] = str(node_id)
        return self.call(
            "stream_analysis.workspace.reset",
            params,
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def workspace_clear(
        self, *, timeout_ms: int | None = None, retries: int | None = None
    ) -> Any:
        return self.call(
            "stream_analysis.workspace.clear", {}, timeout_ms=timeout_ms, retries=retries
        )

    def workspace_store_status(
        self, *, timeout_ms: int | None = None, retries: int | None = None
    ) -> Any:
        return self.call(
            "stream_analysis.workspace_store.status",
            {},
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def workspace_store_save(
        self,
        *,
        path: str | None = None,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        params: Json = {}
        if path is not None:
            params["path"] = str(path)
        return self.call(
            "stream_analysis.workspace_store.save",
            params,
            timeout_ms=timeout_ms,
            retries=retries,
        )

    def workspace_store_reload(
        self,
        *,
        path: str | None = None,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Any:
        params: Json = {}
        if path is not None:
            params["path"] = str(path)
        return self.call(
            "stream_analysis.workspace_store.reload",
            params,
            timeout_ms=timeout_ms,
            retries=retries,
        )
