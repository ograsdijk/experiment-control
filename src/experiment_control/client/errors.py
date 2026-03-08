from __future__ import annotations

from typing import Any

from .types import Json


class StackClientError(RuntimeError):
    pass


class RpcTransportError(StackClientError):
    pass


class RpcTimeoutError(RpcTransportError):
    pass


class RpcResponseError(StackClientError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        response: Json | None = None,
        request: Json | None = None,
        details: Any | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = str(code)
        self.message = str(message)
        self.response = response
        self.request = request
        self.details = details


class ProcessRpcNotReadyError(RpcResponseError):
    pass


def _coerce_error(error: Any) -> tuple[str, str, Any | None]:
    if isinstance(error, dict):
        code = str(error.get("code") or "rpc_error").strip() or "rpc_error"
        message = str(error.get("message") or code).strip() or code
        return code, message, error.get("details")
    if isinstance(error, str):
        text = error.strip() or "rpc_error"
        return text, text, None
    return "rpc_error", "rpc_error", None


def _raise_rpc_error(*, error: Any, response: Json, request: Json | None) -> None:
    code, message, details = _coerce_error(error)
    exc_cls = (
        ProcessRpcNotReadyError
        if code in {"process_rpc_not_ready", "process_starting"}
        else RpcResponseError
    )
    raise exc_cls(
        code=code,
        message=message,
        response=response,
        request=request,
        details=details,
    )


def result_from_response(response: Any, *, request: Json | None = None) -> Any:
    if not isinstance(response, dict):
        raise RpcResponseError(
            code="invalid_response",
            message="RPC response was not a dict",
            response=None,
            request=request,
        )

    if "ok" in response:
        if bool(response.get("ok")):
            return response.get("result")
        _raise_rpc_error(error=response.get("error"), response=response, request=request)

    status_raw = response.get("status")
    if isinstance(status_raw, str):
        status = status_raw.strip().upper()
        if status == "OK":
            return response.get("result")
        if status in {"ERROR", "FAIL", "FAILED"}:
            _raise_rpc_error(
                error=response.get("error") or {"code": status.lower()},
                response=response,
                request=request,
            )

    if "error" in response and response.get("error") not in (None, "", {}):
        _raise_rpc_error(error=response.get("error"), response=response, request=request)

    return response.get("result")

