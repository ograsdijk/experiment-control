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


def _as_exact_int_or_none(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, float) and not raw.is_integer():
        return None
    return _as_int_or_none(raw)


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
class ChunkSequenceRange:
    first_seq: int
    final_seq: int
    batch_count: int


def parse_chunk_sequence_range(
    raw: Any,
    *,
    max_batch_count: int | None = None,
) -> ChunkSequenceRange | None:
    """Validate the inclusive sequence range advertised by a chunk message."""
    payload = _as_json_object(raw)
    if payload is None:
        raise ValueError("chunk payload must be an object")
    seq = _as_exact_int_or_none(payload.get("seq"))
    first_seq = _as_exact_int_or_none(payload.get("first_seq"))
    batch_raw = payload.get("batch_count")
    batch_count = _as_exact_int_or_none(batch_raw)
    if payload.get("seq") is not None and seq is None:
        raise ValueError("chunk seq must be an integer")
    if payload.get("first_seq") is not None and first_seq is None:
        raise ValueError("chunk first_seq must be an integer")
    if batch_raw is not None and batch_count is None:
        raise ValueError("chunk batch_count must be an integer")
    if seq is None:
        if first_seq is not None or batch_count not in (None, 1):
            raise ValueError("first_seq/batch_count require seq")
        return None
    if seq < 1:
        raise ValueError("chunk seq must be positive")
    if first_seq is None:
        first_seq = seq
        if batch_count not in (None, 1):
            raise ValueError("legacy single-frame chunks must have batch_count=1")
        batch_count = 1
    else:
        if first_seq < 1 or first_seq > seq:
            raise ValueError("chunk first_seq must be positive and <= seq")
        expected = seq - first_seq + 1
        if batch_count is None or batch_count != expected:
            raise ValueError("chunk batch_count must equal seq - first_seq + 1")
    if max_batch_count is not None and batch_count > max(1, int(max_batch_count)):
        raise ValueError("chunk batch_count exceeds the shared-memory ring capacity")
    return ChunkSequenceRange(
        first_seq=int(first_seq),
        final_seq=int(seq),
        batch_count=int(batch_count),
    )


@dataclass(frozen=True)
class ChunkReadyMessage:
    device_id: str
    stream: str
    shm_name: str
    seq: int | None
    first_seq: int | None
    batch_count: int
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
        try:
            seq_range = parse_chunk_sequence_range(payload)
        except ValueError:
            return None
        context_fields_raw = payload.get("context_fields")
        context_fields = (
            dict(context_fields_raw) if isinstance(context_fields_raw, dict) else None
        )
        return cls(
            device_id=device_id,
            stream=stream,
            shm_name=shm_name,
            seq=None if seq_range is None else seq_range.final_seq,
            first_seq=None if seq_range is None else seq_range.first_seq,
            batch_count=1 if seq_range is None else seq_range.batch_count,
            context_id=_as_int_or_none(payload.get("context_id")),
            context_fields=context_fields,
            raw=payload,
        )
