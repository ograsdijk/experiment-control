from __future__ import annotations

import copy
import json
from typing import Any

from .schemas.run_meta import run_meta_calls_to_json
from .schemas.stream import stream_calls_to_json
from .schemas.telemetry import telemetry_calls_to_json

Json = dict[str, Any]


def normalize_runtime_metadata_dict(
    raw: object,
    *,
    label: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TypeError(f"{label} must be an object/dict")
    out: dict[str, Any] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name:
            raise ValueError(f"{label} keys must be non-empty strings")
        out[name] = copy.deepcopy(value)
    return out


def normalize_runtime_stream_metadata_dict(
    raw: object,
    *,
    label: str,
) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        raise TypeError(f"{label} must be an object/dict")
    out: dict[str, dict[str, Any]] = {}
    for stream_raw, attrs_raw in raw.items():
        stream = str(stream_raw).strip()
        if not stream:
            raise ValueError(f"{label} stream names must be non-empty strings")
        attrs = normalize_runtime_metadata_dict(
            attrs_raw,
            label=f"{label}.{stream}",
        )
        out[stream] = attrs
    return out


def merge_stream_metadata_dicts(
    base: dict[str, dict[str, Any]],
    overlay: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for stream, attrs in base.items():
        merged[stream] = dict(attrs)
    for stream, attrs in overlay.items():
        cur = dict(merged.get(stream, {}))
        cur.update(attrs)
        merged[stream] = cur
    return merged


def effective_metadata_for_device(
    manager: Any,
    device_id: str,
    spec: Any,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    base_device = copy.deepcopy(spec.device_metadata or {})
    base_stream = copy.deepcopy(spec.stream_metadata or {})
    override_device = copy.deepcopy(manager._runtime_device_metadata_overrides.get(device_id, {}))
    override_stream = copy.deepcopy(manager._runtime_stream_metadata_overrides.get(device_id, {}))
    effective_device = dict(base_device)
    effective_device.update(override_device)
    effective_stream = merge_stream_metadata_dicts(base_stream, override_stream)
    return effective_device, effective_stream


def runtime_metadata_state(manager: Any, device_id: str, handle: Any) -> Json:
    base_device = copy.deepcopy(handle.spec.device_metadata or {})
    base_stream = copy.deepcopy(handle.spec.stream_metadata or {})
    override_device = copy.deepcopy(manager._runtime_device_metadata_overrides.get(device_id, {}))
    override_stream = copy.deepcopy(manager._runtime_stream_metadata_overrides.get(device_id, {}))
    effective_device, effective_stream = effective_metadata_for_device(
        manager, device_id, handle.spec
    )
    return {
        "device_id": device_id,
        "revision": int(manager._runtime_metadata_revision.get(device_id, 0)),
        "base": {
            "device_metadata": base_device,
            "stream_metadata": base_stream,
        },
        "overrides": {
            "device_metadata": override_device,
            "stream_metadata": override_stream,
        },
        "effective": {
            "device_metadata": effective_device,
            "stream_metadata": effective_stream,
        },
    }


def touch_runtime_metadata_revision(manager: Any, device_id: str) -> int:
    current = int(manager._runtime_metadata_revision.get(device_id, 0))
    next_rev = current + 1
    manager._runtime_metadata_revision[device_id] = next_rev
    return next_rev


def publish_device_config(manager: Any, handle: Any) -> None:
    payload: Json = device_config_payload(manager, handle)
    manager._publish_manager_event("manager.device_config", payload)


def device_config_payload(manager: Any, handle: Any) -> Json:
    yaml_text = handle.spec.config_yaml_text
    if yaml_text is None:
        yaml_text = serialize_spec_yaml(handle.spec)
    device_metadata, stream_metadata = effective_metadata_for_device(
        manager, handle.spec.device_id, handle.spec
    )
    return {
        "version": 1,
        "device_id": handle.spec.device_id,
        "yaml_text": yaml_text,
        "device_metadata": device_metadata,
        "stream_metadata": stream_metadata,
        "connect_check": {
            "enabled": bool(handle.spec.connect_check.enabled),
            "identity": copy.deepcopy(handle.spec.connect_check.identity),
            "on_fail": str(handle.spec.connect_check.on_fail),
        },
        "telemetry_calls": telemetry_calls_to_json(handle.spec.telemetry_calls),
        "stream_calls": stream_calls_to_json(list(handle.spec.stream_calls or [])),
        "run_meta_calls": run_meta_calls_to_json(list(handle.spec.run_meta_calls or [])),
        "metadata_revision": int(manager._runtime_metadata_revision.get(handle.spec.device_id, 0)),
        "source_kind": "local",
        "is_remote": False,
        "owner_peer_id": None,
        "remote_device_id": None,
    }


def serialize_spec_yaml(spec: Any) -> str:
    payload = {
        "device_id": spec.device_id,
        "driver": {
            "file": str(spec.device_class_path),
            "class_name": spec.device_class_name,
        },
        "init_kwargs": spec.device_init_kwargs,
        "telemetry_calls": telemetry_calls_to_json(spec.telemetry_calls),
        "stream_calls": stream_calls_to_json(list(spec.stream_calls or [])),
        "run_meta_calls": run_meta_calls_to_json(list(spec.run_meta_calls or [])),
        "device_metadata": spec.device_metadata or {},
        "stream_metadata": spec.stream_metadata or {},
        "connect_check": {
            "enabled": bool(spec.connect_check.enabled),
            "identity": copy.deepcopy(spec.connect_check.identity),
            "on_fail": str(spec.connect_check.on_fail),
        },
    }
    try:
        import yaml  # type: ignore[import-not-found]

        return yaml.safe_dump(payload, sort_keys=False)
    except Exception:
        return json.dumps(payload, indent=2, sort_keys=False)
