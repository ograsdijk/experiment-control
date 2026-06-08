"""Canonical predicates and envelopes for parsing manager command responses.

Manager responses follow one of two shapes:

    {"ok": True/False, ...}          # newer, preferred convention
    {"status": "OK"|"ERROR", ...}    # older convention; both still in use

When both keys are present, ``"ok"`` wins, and the ``"ok"`` test is
identity-strict (``d.get("ok") is True``) — values like ``1`` or
truthy strings are NOT accepted as success, so a buggy device that
emits ``{"ok": "yes"}`` will surface as a failure rather than being
silently coerced. ``status`` comparison is exact-case (``"OK"`` /
``"ERROR"``). If a device ever emits lowercase ``"ok"`` as a status
string, the fix belongs in that device's driver, not in this
predicate; broad acceptance hides the device-side bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ErrorPayload:
    code: str
    message: str | None = None
    details: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code}
        if self.message is not None:
            out["message"] = self.message
        if self.details is not None:
            out["details"] = self.details
        return out


@dataclass(frozen=True)
class RpcResponse:
    ok: bool
    result: Any | None = None
    error: ErrorPayload | None = None
    include_result: bool = False

    @classmethod
    def success(cls, result: Any | None = None) -> RpcResponse:
        return cls(ok=True, result=result, include_result=True)

    @classmethod
    def failure(
        cls,
        code: str,
        message: str | None = None,
        details: Any | None = None,
        *,
        result: Any | None = None,
        include_result: bool = False,
    ) -> RpcResponse:
        return cls(
            ok=False,
            result=result,
            error=ErrorPayload(code=code, message=message, details=details),
            include_result=include_result,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok}
        if self.include_result:
            out["result"] = self.result
        if self.error is not None:
            out["error"] = self.error.to_dict()
        return out


def from_driver_status(d: dict[str, Any]) -> RpcResponse:
    if "ok" in d:
        if d.get("ok") is True:
            return RpcResponse.success(d.get("result"))
        err = d.get("error")
        if isinstance(err, dict):
            return RpcResponse.failure(
                str(err.get("code") or "error"),
                None if err.get("message") is None else str(err.get("message")),
                err.get("details"),
                result=d.get("result"),
                include_result="result" in d,
            )
        return RpcResponse.failure(
            "error",
            str(err),
            result=d.get("result"),
            include_result="result" in d,
        )
    # Exact-case match per module docstring — refuse to coerce
    # lowercase "ok" or stray whitespace into success.
    if d.get("status") == "OK":
        return RpcResponse.success(d.get("result"))
    return RpcResponse.failure(
        "device_error",
        str(d.get("error") or "device error"),
        d,
        result=d.get("result"),
        include_result=True,
    )


def ensure_error_shape(resp: Any) -> dict[str, Any]:
    if not isinstance(resp, dict):
        return RpcResponse.failure(
            "invalid_response",
            "router response was not a dict",
        ).to_dict()
    if resp.get("ok") is False and isinstance(resp.get("error"), str):
        resp["error"] = {"code": "error", "message": resp["error"]}
    return resp


def normalize_command_response(resp: Any) -> dict[str, Any]:
    resp = ensure_error_shape(resp)
    if "ok" in resp:
        return resp
    return from_driver_status(resp).to_dict()


def is_response_ok(resp: Any) -> bool:
    """Return True iff ``resp`` is a manager command response indicating success.

    See the module docstring for the strictness rules: ``ok`` is checked
    by identity (``is True``) and ``status`` is exact-case.
    """
    if not isinstance(resp, dict):
        return False
    if "ok" in resp:
        return resp.get("ok") is True
    return resp.get("status") == "OK"
