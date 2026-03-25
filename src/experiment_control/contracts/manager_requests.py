from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Json = dict[str, Any]


def _as_json(raw: Any) -> Json | None:
    if not isinstance(raw, dict):
        return None
    return dict(raw)


def _as_text(raw: Any) -> str:
    return str(raw or "").strip()


def rpc_error(
    *,
    code: str,
    message: str,
    details: Json | None = None,
) -> Json:
    error: Json = {"code": str(code), "message": str(message)}
    if details:
        error["details"] = dict(details)
    return {"ok": False, "error": error}


@dataclass(frozen=True)
class InternalRpcEnvelope:
    raw: Json
    req_type: str
    action: str

    @classmethod
    def parse(cls, raw: Any) -> InternalRpcEnvelope | None:
        payload = _as_json(raw)
        if payload is None:
            return None
        req_type = _as_text(payload.get("type"))
        action = _as_text(payload.get("action"))
        return cls(raw=payload, req_type=req_type, action=action)


@dataclass(frozen=True)
class CommandRequest:
    device_id: str
    action: str
    params: Json
    request_id: Any
    source_kind: str | None
    source_id: str | None
    raw: Json

    @classmethod
    def parse(cls, raw: Any) -> CommandRequest | None:
        payload = _as_json(raw)
        if payload is None:
            return None
        if _as_text(payload.get("type")) != "command":
            return None
        device_id = _as_text(payload.get("device_id"))
        action = _as_text(payload.get("action"))
        if not device_id or not action:
            return None
        params_raw = payload.get("params", {})
        if params_raw is None:
            params_raw = {}
        if not isinstance(params_raw, dict):
            return None
        source_kind = _as_text(payload.get("source_kind")) or None
        source_id = _as_text(payload.get("source_id")) or None
        return cls(
            device_id=device_id,
            action=action,
            params=dict(params_raw),
            request_id=payload.get("request_id"),
            source_kind=source_kind,
            source_id=source_id,
            raw=payload,
        )


@dataclass(frozen=True)
class ProcessRpcRequest:
    process_id: str
    request: Json
    request_id: Any
    raw: Json

    @classmethod
    def parse(cls, raw: Any) -> ProcessRpcRequest | None:
        payload = _as_json(raw)
        if payload is None:
            return None
        if _as_text(payload.get("type")) != "manager.processes.rpc":
            return None
        process_id = _as_text(payload.get("process_id"))
        request = payload.get("request")
        if not process_id or not isinstance(request, dict):
            return None
        return cls(
            process_id=process_id,
            request=dict(request),
            request_id=payload.get("request_id"),
            raw=payload,
        )


@dataclass(frozen=True)
class ManagerControlRequest:
    req_type: str
    params: Json
    raw: Json

    @classmethod
    def parse(cls, raw: Any) -> ManagerControlRequest | None:
        payload = _as_json(raw)
        if payload is None:
            return None
        req_type = _as_text(payload.get("type"))
        if not req_type.startswith("manager.control."):
            return None
        params_raw = payload.get("params", {})
        if params_raw is None:
            params_raw = {}
        if not isinstance(params_raw, dict):
            return None
        return cls(req_type=req_type, params=dict(params_raw), raw=payload)
