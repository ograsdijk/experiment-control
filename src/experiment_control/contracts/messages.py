from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Json = dict[str, Any]


def _as_json_object(raw: Any) -> Json | None:
    if not isinstance(raw, dict):
        return None
    return dict(raw)


def _as_json_params(raw: Any) -> Json | None:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    return None


def _as_non_empty_text(raw: Any) -> str | None:
    text = str(raw or "").strip()
    return text or None


def _as_int_or_none(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except Exception:
        return None


@dataclass(frozen=True)
class RpcActionRequest:
    request_id: Any
    action: str
    params: Json
    raw: Json

    @classmethod
    def parse(
        cls,
        raw: Any,
        *,
        action_field: str,
        request_id_field: str,
        params_field: str = "params",
        fallback_action_field: str | None = None,
    ) -> RpcActionRequest | None:
        payload = _as_json_object(raw)
        if payload is None:
            return None
        action = _as_non_empty_text(payload.get(action_field))
        if action is None and fallback_action_field:
            action = _as_non_empty_text(payload.get(fallback_action_field))
        if action is None:
            return None
        params = _as_json_params(payload.get(params_field))
        if params is None:
            return None
        return cls(
            request_id=payload.get(request_id_field),
            action=action,
            params=params,
            raw=payload,
        )

    def as_dispatch_payload(
        self,
        *,
        request_id_field: str,
        type_field: str = "type",
        action_field: str = "action",
        params_field: str = "params",
    ) -> Json:
        payload = dict(self.raw)
        payload[request_id_field] = self.request_id
        payload[type_field] = self.action
        payload[action_field] = self.action
        payload[params_field] = dict(self.params)
        return payload


@dataclass(frozen=True)
class DeviceScopedMessage:
    device_id: str
    raw: Json

    @classmethod
    def parse(cls, raw: Any) -> DeviceScopedMessage | None:
        payload = _as_json_object(raw)
        if payload is None:
            return None
        device_id = _as_non_empty_text(payload.get("device_id"))
        if device_id is None:
            return None
        return cls(device_id=device_id, raw=payload)


@dataclass(frozen=True)
class ChunkReadyMessage:
    device_id: str
    stream: str
    shm_name: str
    seq: int | None
    context_id: int | None
    context_fields: Json | None
    raw: Json

    @classmethod
    def parse(cls, raw: Any) -> ChunkReadyMessage | None:
        payload = _as_json_object(raw)
        if payload is None:
            return None
        device_id = _as_non_empty_text(payload.get("device_id"))
        stream = _as_non_empty_text(payload.get("stream"))
        shm_name = _as_non_empty_text(payload.get("shm_name"))
        if device_id is None or stream is None or shm_name is None:
            return None
        context_fields_raw = payload.get("context_fields")
        context_fields = (
            dict(context_fields_raw)
            if isinstance(context_fields_raw, dict)
            else None
        )
        return cls(
            device_id=device_id,
            stream=stream,
            shm_name=shm_name,
            seq=_as_int_or_none(payload.get("seq")),
            context_id=_as_int_or_none(payload.get("context_id")),
            context_fields=context_fields,
            raw=payload,
        )
