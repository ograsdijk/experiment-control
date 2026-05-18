from __future__ import annotations

import argparse
import json
import math
import os
import queue
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import zmq

from ..capabilities import capabilities_payload, method, param
from ..utils.cli_args import (
    add_heartbeat_args,
    add_manager_args,
    add_process_id_arg,
    add_rpc_timeout_arg,
)
from ..utils.config_parsing import optional_dict, require_dict, require_str
from ..utils.rpc_dispatch import RpcDispatchRegistry
from ..utils.value_coercion import coerce_bool, coerce_float, coerce_int
from ..utils.yaml_helpers import load_yaml_file
from ..utils.zmq_helpers import drain_multipart_nonblocking, safe_json_loads
from .manager_client_helper import ManagerClientHelper
from .process_base import ManagedProcessBase

Json = dict[str, Any]


@dataclass(frozen=True)
class InfluxDestination:
    name: str
    url: str
    org: str
    bucket: str
    token: str
    measurement: str
    precision: str
    request_timeout_s: float
    static_tags: dict[str, str]


@dataclass(frozen=True)
class DeviceRoute:
    destination: str
    measurement: str | None
    device_type: str | None
    tags: dict[str, str]


@dataclass(frozen=True)
class QueuedPoint:
    destination: str
    line: str


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser("experiment_control influx writer")
    add_manager_args(p)
    add_process_id_arg(p, default="influx_writer")
    add_rpc_timeout_arg(p, default_ms=2000)
    add_heartbeat_args(p, default_period_s=1.0)
    p.add_argument("--config", default="")
    return p.parse_args(argv)


def _expand_env_vars(text: str) -> str:
    # Allow ${VAR} / $VAR expansion in URL/token style strings.
    return os.path.expandvars(text)


def _normalize_meta_tag_key(raw: Any) -> str | None:
    text = str(raw).strip()
    return text if text else None


def _normalize_device_type_name(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-zA-Z0-9_]", "_", text)
    text = re.sub(r"_+", "_", text).strip("_").lower()
    if text.endswith("_driver"):
        text = text[: -len("_driver")].rstrip("_")
    elif text.endswith("driver"):
        text = text[: -len("driver")].rstrip("_")
    if text.endswith("_device"):
        text = text[: -len("_device")].rstrip("_")
    elif text.endswith("device"):
        text = text[: -len("device")].rstrip("_")
    return text or None


def _parse_driver_dict_from_yaml_text(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    parsed: Any = None
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
    if parsed is None:
        try:
            import yaml  # type: ignore[import-not-found]

            parsed = yaml.safe_load(text)
        except Exception:
            parsed = None
    if not isinstance(parsed, dict):
        return None
    driver = parsed.get("driver")
    if not isinstance(driver, dict):
        return None
    return driver


def _extract_driver_dict_from_config_payload(payload: Json) -> dict[str, Any] | None:
    driver = payload.get("driver")
    if isinstance(driver, dict):
        return driver
    return _parse_driver_dict_from_yaml_text(payload.get("yaml_text"))


def _derive_device_type_from_driver_config(payload: Json) -> str | None:
    driver = _extract_driver_dict_from_config_payload(payload)
    if not isinstance(driver, dict):
        return None
    class_name = _normalize_device_type_name(driver.get("class_name"))
    if class_name:
        return class_name
    module_raw = driver.get("module")
    if isinstance(module_raw, str) and module_raw.strip():
        tail = module_raw.strip().split(".")[-1]
        module_name = _normalize_device_type_name(tail)
        if module_name:
            return module_name
    file_raw = driver.get("file")
    if isinstance(file_raw, str) and file_raw.strip():
        stem = Path(file_raw.strip()).stem
        file_name = _normalize_device_type_name(stem)
        if file_name:
            return file_name
    return None


def _escape_measurement(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(" ", "\\ ")
    )


def _escape_tag_component(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace("=", "\\=")
        .replace(" ", "\\ ")
    )


def _escape_field_key(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace("=", "\\=")
        .replace(" ", "\\ ")
    )


def _escape_field_str(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _encode_tag_parts(tags: dict[str, str]) -> list[str]:
    out: list[str] = []
    for key in sorted(tags.keys()):
        value = tags.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        out.append(f"{_escape_tag_component(key)}={_escape_tag_component(text)}")
    return out


def _encode_field_value(raw: Any) -> str | None:
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, int):
        return f"{int(raw)}i"
    if isinstance(raw, float):
        if not math.isfinite(raw):
            return None
        return format(float(raw), ".17g")
    if isinstance(raw, str):
        return f'"{_escape_field_str(raw)}"'
    return None


def _encode_field_parts(fields: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in sorted(fields.keys()):
        encoded = _encode_field_value(fields[key])
        if encoded is None:
            continue
        out.append(f"{_escape_field_key(key)}={encoded}")
    return out


def _build_line_protocol(
    *,
    measurement: str,
    tags: dict[str, str],
    fields: dict[str, Any],
    ts_ns: int,
) -> str:
    if not fields:
        raise ValueError("line protocol requires at least one field")

    line = _escape_measurement(measurement)
    tag_parts = _encode_tag_parts(tags)
    if tag_parts:
        line += "," + ",".join(tag_parts)

    field_parts = _encode_field_parts(fields)
    if not field_parts:
        raise ValueError("line protocol fields could not be encoded")
    line += " " + ",".join(field_parts)
    line += f" {int(ts_ns)}"
    return line


def _timestamp_ns_from_payload(
    signal_payload: dict[str, Any], bundle_payload: dict[str, Any]
) -> int:
    ts_obj = signal_payload.get("ts")
    if isinstance(ts_obj, dict):
        t_wall = ts_obj.get("t_wall")
        try:
            return int(float(t_wall) * 1_000_000_000)
        except Exception:
            pass
    ts_obj = bundle_payload.get("ts")
    if isinstance(ts_obj, dict):
        t_wall = ts_obj.get("t_wall")
        try:
            return int(float(t_wall) * 1_000_000_000)
        except Exception:
            pass
    return int(time.time() * 1_000_000_000)


def _timestamp_ns_from_bundle(payload: Json) -> int:
    ts_obj = payload.get("ts")
    if isinstance(ts_obj, dict):
        t_wall = ts_obj.get("t_wall")
        try:
            return int(float(t_wall) * 1_000_000_000)
        except Exception:
            pass
    signals = payload.get("signals")
    if isinstance(signals, dict):
        for signal_payload in signals.values():
            if not isinstance(signal_payload, dict):
                continue
            ts_ns = _timestamp_ns_from_payload(signal_payload, payload)
            if ts_ns > 0:
                return ts_ns
    return int(time.time() * 1_000_000_000)


def _extract_device_type_from_config(
    payload: Json, *, metadata_key: str = "device_type"
) -> str | None:
    device_metadata = payload.get("device_metadata")
    if isinstance(device_metadata, dict):
        from_meta = _normalize_device_type_name(device_metadata.get(metadata_key))
        if from_meta:
            return from_meta
    return None


class InfluxWriterProcess(ManagedProcessBase):
    def __init__(
        self,
        *,
        manager_rpc: str,
        manager_pub: str,
        process_id: str,
        rpc_timeout_ms: int = 2000,
        heartbeat_endpoint: str | None,
        heartbeat_period_s: float,
        instance_id: str | None = None,
        destinations: dict[str, Any] | None = None,
        default_destination: str | None = None,
        routes: dict[str, Any] | None = None,
        disabled_devices: list[str] | None = None,
        enabled: bool = True,
        write_batch_size: int = 500,
        write_flush_interval_ms: int = 1000,
        max_queue_points: int = 100_000,
        overflow_policy: str = "drop_oldest",
        include_device_type_tag: bool = True,
        include_quality_fields: bool = True,
        include_unit_fields: bool = False,
        device_type_key: str = "device_type",
        device_tag_keys: list[str] | None = None,
    ) -> None:
        super().__init__(
            process_id=process_id,
            heartbeat_endpoint=heartbeat_endpoint,
            heartbeat_period_s=heartbeat_period_s,
        )
        instance = str(
            instance_id or os.environ.get("EXPERIMENT_CONTROL_INSTANCE_ID", "")
        ).strip()
        if not instance:
            raise ValueError("influx_writer requires instance_id (arg or environment)")
        self._instance_id = instance

        self._manager_helper = ManagerClientHelper(
            manager_rpc=manager_rpc,
            manager_pub=manager_pub,
            rpc_timeout_ms=int(rpc_timeout_ms),
        )

        self._batch_max_points = max(1, int(write_batch_size))
        self._flush_interval_s = max(
            0.05, float(write_flush_interval_ms) / 1000.0
        )
        self._max_queue_points = max(1, int(max_queue_points))
        overflow = str(overflow_policy or "drop_oldest").strip().lower()
        if overflow not in {"drop_oldest", "drop_newest"}:
            overflow = "drop_oldest"
        self._overflow_policy = overflow
        self._enabled = bool(enabled)
        self._include_device_type_tag = bool(include_device_type_tag)
        self._include_quality_fields = bool(include_quality_fields)
        self._include_unit_fields = bool(include_unit_fields)
        type_key = str(device_type_key).strip()
        self._device_type_key = type_key if type_key else "device_type"
        self._device_tag_keys = self._parse_device_tag_keys(
            device_tag_keys if device_tag_keys is not None else ["location"]
        )
        self._disabled_devices: set[str] = {
            str(device_id).strip()
            for device_id in (disabled_devices or [])
            if str(device_id).strip()
        }

        self._destinations = self._parse_destinations(destinations or {})
        self._default_destination = self._resolve_default_destination(
            default_destination=default_destination
        )
        self._routes = self._parse_routes(routes or {})

        # Runtime metadata from manager.device_config
        self._device_type_by_id: dict[str, str] = {}
        self._device_tags_by_id: dict[str, dict[str, str]] = {}
        self._remote_device_ids: set[str] = set()

        self._queue: deque[QueuedPoint] = deque()
        self._points_received = 0
        self._points_queued = 0
        self._points_written = 0
        self._points_skipped_invalid = 0
        self._points_skipped_remote = 0
        self._points_dropped_overflow = 0
        self._write_errors = 0
        self._batches_written = 0
        self._last_error: str | None = None
        self._last_flush_wall_s: float | None = None
        self._last_flush_mono_s: float | None = None
        self._last_flush_start_wall_s: float | None = None
        self._last_flush_start_mono_s: float | None = None
        self._last_flush_duration_s: float | None = None
        self._last_flush_destination: str | None = None
        self._last_drain_count = 0
        self._last_drain_duration_s = 0.0
        self._total_drained = 0
        self._drain_limited_count = 0
        self._drain_parse_errors = 0
        self._pending_log_payloads: deque[Json] = deque(maxlen=200)
        self._last_published_error_text: str | None = None

        # Background HTTP worker: hands off batched groupings so the main loop
        # never blocks on urlopen. Queue depth 64 caps in-flight batches; on
        # overflow we drop the batch (counted) rather than blocking the main
        # loop. Thread-safety: bg thread only touches self._queue under
        # _queue_lock and never calls self._manager (ZMQ REQ is not thread-safe);
        # it surfaces errors via self._last_error which the main loop publishes.
        self._queue_lock = threading.Lock()
        self._http_queue: queue.Queue[dict[str, list[QueuedPoint]] | None] = (
            queue.Queue(maxsize=64)
        )
        self._http_thread_dead = False
        self._dropped_http_batches = 0
        self._http_thread: threading.Thread | None = None

        self._init_rpc_router()
        self._manager = self._manager_helper.init_client(
            ctx=self._ctx,
            process_id=self._process_id,
            subscribe_telemetry=False,
        )
        self._sub = self._manager_helper.open_sub(
            ctx=self._ctx,
            topics=("manager.telemetry_update", "manager.device_config"),
            rcvtimeo_ms=200,
        )
        self._init_poller(
            include_rpc=True,
            include_sub=False,
            extra=[(self._sub, zmq.POLLIN)],
        )
        self._advertise_process_rpc()
        self._start_heartbeat_thread(state_provider=lambda: "RUNNING")

        self._refresh_device_catalog()
        self._rpc_registry = self._build_rpc_registry()

    def _parse_destinations(self, raw: dict[str, Any]) -> dict[str, InfluxDestination]:
        out: dict[str, InfluxDestination] = {}
        for dest_name, item in raw.items():
            destination = self._parse_destination_entry(dest_name=dest_name, item=item)
            if destination is not None:
                out[destination.name] = destination
        if not out:
            raise ValueError("influx_writer requires at least one destination")
        return out

    @staticmethod
    def _parse_destination_precision(raw: Any) -> str:
        precision = str(raw or "ns").strip().lower() or "ns"
        if precision not in {"ns", "us", "ms", "s"}:
            return "ns"
        return precision

    @staticmethod
    def _parse_destination_static_tags(raw: Any) -> dict[str, str]:
        out: dict[str, str] = {}
        if not isinstance(raw, dict):
            return out
        for key, value in raw.items():
            tag_key = str(key).strip()
            tag_value = str(value).strip()
            if tag_key and tag_value:
                out[tag_key] = tag_value
        return out

    def _parse_destination_entry(
        self,
        *,
        dest_name: Any,
        item: Any,
    ) -> InfluxDestination | None:
        if not isinstance(item, dict):
            return None
        name = str(dest_name).strip()
        if not name:
            return None
        url = _expand_env_vars(str(item.get("url", "")).strip())
        org = _expand_env_vars(str(item.get("org", "")).strip())
        bucket = _expand_env_vars(str(item.get("bucket", "")).strip())
        token = _expand_env_vars(str(item.get("token", "")).strip())
        if not url or not org or not bucket:
            return None
        measurement = str(item.get("measurement", "unknown_device")).strip()
        if not measurement:
            measurement = "unknown_device"
        precision = self._parse_destination_precision(item.get("precision", "ns"))
        request_timeout_s = coerce_float(item.get("request_timeout_s"), default=5.0)
        static_tags = self._parse_destination_static_tags(item.get("static_tags"))
        return InfluxDestination(
            name=name,
            url=url,
            org=org,
            bucket=bucket,
            token=token,
            measurement=measurement,
            precision=precision,
            request_timeout_s=max(0.5, float(request_timeout_s)),
            static_tags=static_tags,
        )

    def _resolve_default_destination(self, *, default_destination: str | None) -> str:
        if isinstance(default_destination, str) and default_destination.strip():
            name = default_destination.strip()
            if name in self._destinations:
                return name
        if len(self._destinations) == 1:
            return next(iter(self._destinations.keys()))
        raise ValueError("influx_writer default_destination is required")

    def _parse_routes(self, raw: dict[str, Any]) -> dict[str, DeviceRoute]:
        out: dict[str, DeviceRoute] = {}
        for device_id_raw, item in raw.items():
            if not isinstance(item, dict):
                continue
            device_id = str(device_id_raw).strip()
            if not device_id:
                continue
            destination = str(item.get("destination", self._default_destination)).strip()
            if destination not in self._destinations:
                continue
            device_type_raw = item.get("device_type")
            device_type = (
                str(device_type_raw).strip()
                if isinstance(device_type_raw, str) and str(device_type_raw).strip()
                else None
            )
            measurement_raw = item.get("measurement")
            measurement = (
                str(measurement_raw).strip()
                if isinstance(measurement_raw, str) and str(measurement_raw).strip()
                else None
            )
            tags: dict[str, str] = {}
            tags_raw = item.get("tags")
            if isinstance(tags_raw, dict):
                for key_raw, value_raw in tags_raw.items():
                    key = str(key_raw).strip()
                    value = str(value_raw).strip()
                    if key and value:
                        tags[key] = value
            out[device_id] = DeviceRoute(
                destination=destination,
                measurement=measurement,
                device_type=device_type,
                tags=tags,
            )
        return out

    @staticmethod
    def _parse_device_tag_keys(raw: list[str] | tuple[str, ...] | Any) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        if isinstance(raw, (list, tuple)):
            items = list(raw)
        else:
            items = [raw]
        for item in items:
            key = _normalize_meta_tag_key(item)
            if key is None or key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

    def _refresh_device_catalog(self) -> None:
        req = {"type": "device.config.list"}
        resp = self._manager.call(req, timeout_ms=2000)
        if not isinstance(resp, dict) or not resp.get("ok"):
            return
        result = resp.get("result")
        if not isinstance(result, list):
            return
        for item in result:
            if not isinstance(item, dict):
                continue
            self._handle_device_config(item)

    def _handle_device_config(self, payload: Json) -> None:
        device_id = str(payload.get("device_id", "")).strip()
        if not device_id:
            return
        source_kind = str(payload.get("source_kind", "")).strip().lower()
        is_remote = bool(payload.get("is_remote")) or source_kind == "federated"
        if is_remote:
            self._remote_device_ids.add(device_id)
        else:
            self._remote_device_ids.discard(device_id)

        self._device_tags_by_id[device_id] = self._extract_device_tags(
            payload.get("device_metadata")
        )
        self._refresh_device_type(device_id=device_id, payload=payload)

    def _extract_device_tags(self, device_metadata: Any) -> dict[str, str]:
        tags: dict[str, str] = {}
        if not isinstance(device_metadata, dict):
            return tags
        for key in self._device_tag_keys:
            value = device_metadata.get(key)
            if value is None:
                continue
            value_text = str(value).strip()
            if value_text:
                tags[key] = value_text
        return tags

    def _refresh_device_type(self, *, device_id: str, payload: Json) -> None:
        route = self._routes.get(device_id)
        if route is not None and route.device_type:
            normalized_route = _normalize_device_type_name(route.device_type)
            if normalized_route:
                self._device_type_by_id[device_id] = normalized_route
            return
        device_type = _extract_device_type_from_config(
            payload, metadata_key=self._device_type_key
        )
        if device_type:
            self._device_type_by_id[device_id] = device_type
            return
        derived_type = _derive_device_type_from_driver_config(payload)
        if derived_type:
            self._device_type_by_id[device_id] = derived_type

    def _resolve_destination(self, device_id: str) -> str:
        route = self._routes.get(device_id)
        if route is not None and route.destination in self._destinations:
            return route.destination
        return self._default_destination

    def _resolve_measurement(self, *, device_id: str, destination: InfluxDestination) -> str:
        route = self._routes.get(device_id)
        if route is not None and route.measurement:
            return route.measurement
        if route is not None and route.device_type:
            return route.device_type
        device_type = self._device_type_by_id.get(device_id)
        if device_type:
            return device_type
        return destination.measurement

    def _enqueue_point(self, point: QueuedPoint) -> None:
        with self._queue_lock:
            if len(self._queue) >= self._max_queue_points:
                if self._overflow_policy == "drop_newest":
                    self._points_dropped_overflow += 1
                    return
                self._queue.popleft()
                self._points_dropped_overflow += 1
            self._queue.append(point)
            self._points_queued += 1

    def _drain_sub(
        self,
        *,
        max_messages: int | None = 1000,
        max_duration_s: float | None = 0.1,
    ) -> dict[str, Any]:
        def _handle(topic_b: bytes, payload_b: bytes) -> bool:
            topic = topic_b.decode("utf-8", errors="replace").strip()
            payload = safe_json_loads(payload_b)
            if not isinstance(payload, dict):
                return False
            if topic == "manager.device_config":
                self._handle_device_config(payload)
                return True
            if topic == "manager.telemetry_update":
                self._ingest_telemetry(payload)
                return True
            return False

        result = drain_multipart_nonblocking(
            self._sub,
            _handle,
            max_messages=max_messages,
            max_duration_s=max_duration_s,
        )
        self._last_drain_count = result.count
        self._last_drain_duration_s = result.duration_s
        self._total_drained += result.count
        self._drain_parse_errors += result.parse_errors
        if result.limited:
            self._drain_limited_count += 1
        return {
            "count": result.count,
            "limited": result.limited,
            "duration_s": result.duration_s,
            "parse_errors": result.parse_errors,
        }

    def _resolve_ingest_destination(
        self,
        *,
        device_id: str,
    ) -> tuple[str, InfluxDestination] | None:
        destination_name = self._resolve_destination(device_id)
        destination = self._destinations.get(destination_name)
        if destination is not None:
            return destination_name, destination
        self._write_errors += 1
        self._last_error = (
            f"unknown destination {destination_name!r} for device {device_id!r}"
        )
        return None

    @staticmethod
    def _coerce_signal_field_value(value: Any) -> Any | None:
        if isinstance(value, bool):
            return bool(value)
        if isinstance(value, int):
            as_i64 = int(value)
            if as_i64 < -(2**63) or as_i64 > 2**63 - 1:
                return None
            return as_i64
        if isinstance(value, float):
            if not math.isfinite(value):
                return None
            return float(value)
        if isinstance(value, str):
            return value
        return None

    def _build_telemetry_fields(self, signals: Any) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if not isinstance(signals, dict):
            return fields
        for signal_name_raw, signal_payload_raw in signals.items():
            signal_name = str(signal_name_raw).strip()
            if not signal_name or not isinstance(signal_payload_raw, dict):
                continue
            coerced_value = self._coerce_signal_field_value(signal_payload_raw.get("value"))
            if coerced_value is None:
                continue
            fields[signal_name] = coerced_value
            if self._include_quality_fields:
                quality = str(signal_payload_raw.get("quality", "UNKNOWN")).strip().upper()
                fields[f"{signal_name}__quality"] = quality
            if self._include_unit_fields:
                unit_raw = signal_payload_raw.get("units")
                unit = str(unit_raw).strip() if isinstance(unit_raw, str) else ""
                if unit:
                    fields[f"{signal_name}__unit"] = unit
        return fields

    def _build_point_tags(
        self,
        *,
        device_id: str,
        destination: InfluxDestination,
    ) -> dict[str, str]:
        tags: dict[str, str] = {
            "instance_id": self._instance_id,
            "device_id": device_id,
        }
        tags.update(destination.static_tags)
        tags.update(self._device_tags_by_id.get(device_id, {}))
        route = self._routes.get(device_id)
        if route is not None and route.tags:
            tags.update(route.tags)
        device_type = self._device_type_by_id.get(device_id)
        if self._include_device_type_tag and device_type:
            tags["device_type"] = device_type
        return tags

    def _ingest_device_id(self, payload: Json) -> str | None:
        device_id = str(payload.get("device_id", "")).strip()
        if not device_id or device_id in self._disabled_devices:
            return None
        if device_id in self._remote_device_ids:
            self._points_skipped_remote += 1
            return None
        return device_id

    def _build_queued_point(
        self,
        *,
        payload: Json,
        device_id: str,
        destination_name: str,
        destination: InfluxDestination,
    ) -> QueuedPoint | None:
        fields = self._build_telemetry_fields(payload.get("signals"))
        if not fields:
            self._points_skipped_invalid += 1
            return None

        self._points_received += 1
        ts_ns = _timestamp_ns_from_bundle(payload)
        measurement = self._resolve_measurement(
            device_id=device_id,
            destination=destination,
        )
        tags = self._build_point_tags(device_id=device_id, destination=destination)
        try:
            line = _build_line_protocol(
                measurement=measurement,
                tags=tags,
                fields=fields,
                ts_ns=ts_ns,
            )
        except Exception:
            self._points_skipped_invalid += 1
            return None
        return QueuedPoint(destination=destination_name, line=line)

    def _ingest_telemetry(self, payload: Json) -> None:
        if not self._enabled:
            return
        device_id = self._ingest_device_id(payload)
        if device_id is None:
            return
        destination_info = self._resolve_ingest_destination(device_id=device_id)
        if destination_info is None:
            return
        if not isinstance(payload.get("signals"), dict):
            return
        destination_name, destination = destination_info
        point = self._build_queued_point(
            payload=payload,
            device_id=device_id,
            destination_name=destination_name,
            destination=destination,
        )
        if point is None:
            return
        self._enqueue_point(point)

    @staticmethod
    def _destination_write_url(destination: InfluxDestination) -> str:
        parsed = urlsplit(destination.url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"invalid influx url: {destination.url!r}")

        path = parsed.path.rstrip("/")
        if path.endswith("/api/v2/write"):
            write_path = path
        elif path:
            write_path = f"{path}/api/v2/write"
        else:
            write_path = "/api/v2/write"

        query_map = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query_map["org"] = destination.org
        query_map["bucket"] = destination.bucket
        query_map["precision"] = destination.precision
        query = urlencode(query_map)
        return urlunsplit(
            (parsed.scheme, parsed.netloc, write_path, query, parsed.fragment)
        )

    @staticmethod
    def _write_batch_http(
        *,
        destination: InfluxDestination,
        lines: list[str],
    ) -> None:
        if not lines:
            return
        body = "\n".join(lines).encode("utf-8")
        url = InfluxWriterProcess._destination_write_url(destination)
        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "Accept": "application/json",
        }
        if destination.token:
            headers["Authorization"] = f"Token {destination.token}"
        req = Request(url, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=destination.request_timeout_s) as resp:
            status = int(resp.getcode())
            if status < 200 or status >= 300:
                raise RuntimeError(f"influx write failed with status {status}")

    def _requeue_failed(self, points: list[QueuedPoint]) -> None:
        # Reinsert at the front to preserve ordering semantics.
        # Caller must hold _queue_lock.
        for point in reversed(points):
            if len(self._queue) >= self._max_queue_points:
                if self._overflow_policy == "drop_newest":
                    self._points_dropped_overflow += 1
                    continue
                self._queue.popleft()
                self._points_dropped_overflow += 1
            self._queue.appendleft(point)

    def _drain_pending_points(self) -> list[QueuedPoint]:
        pending: list[QueuedPoint] = []
        while self._queue:
            pending.append(self._queue.popleft())
        return pending

    @staticmethod
    def _group_points_by_destination(
        pending: list[QueuedPoint],
    ) -> dict[str, list[QueuedPoint]]:
        by_destination: dict[str, list[QueuedPoint]] = {}
        for point in pending:
            by_destination.setdefault(point.destination, []).append(point)
        return by_destination

    def _flush_destination_points(
        self,
        *,
        destination_name: str,
        points: list[QueuedPoint],
    ) -> bool:
        destination = self._destinations.get(destination_name)
        if destination is None:
            self._write_errors += 1
            self._last_error = f"missing destination {destination_name!r}"
            return False
        lines = [point.line for point in points]
        self._last_flush_destination = destination_name
        self._last_flush_start_wall_s = time.time()
        self._last_flush_start_mono_s = time.monotonic()
        self._set_phase("influx_write", f"destination={destination_name} points={len(points)}")
        try:
            self._write_batch_http(destination=destination, lines=lines)
            self._points_written += len(points)
            self._batches_written += 1
            self._last_error = None
            return True
        except HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace").strip()
            except Exception:
                body = ""
            self._write_errors += 1
            self._last_error = (
                f"HTTPError status={e.code} destination={destination_name}: {body or str(e)}"
            )
            return False
        except URLError as e:
            self._write_errors += 1
            self._last_error = f"URLError destination={destination_name}: {e}"
            return False
        except Exception as e:
            self._write_errors += 1
            self._last_error = f"write failed destination={destination_name}: {e}"
            return False
        finally:
            if self._last_flush_start_mono_s is not None:
                self._last_flush_duration_s = time.monotonic() - self._last_flush_start_mono_s

    def _mark_flush_timestamp(self) -> None:
        self._last_flush_wall_s = time.time()
        self._last_flush_mono_s = time.monotonic()

    def _flush_grouped_points(
        self,
        *,
        by_destination: dict[str, list[QueuedPoint]],
    ) -> list[QueuedPoint]:
        failed: list[QueuedPoint] = []
        for destination_name, points in by_destination.items():
            if self._flush_destination_points(
                destination_name=destination_name,
                points=points,
            ):
                continue
            failed.extend(points)
        return failed

    def _flush(self) -> None:
        with self._queue_lock:
            if not self._queue:
                return
            pending = self._drain_pending_points()
        if not pending:
            return
        by_destination = self._group_points_by_destination(pending)
        try:
            self._http_queue.put_nowait(by_destination)
        except queue.Full:
            # HTTP thread is backlogged; put the points back so we don't lose
            # them. Counted so it shows up in status / heartbeat.
            self._dropped_http_batches += 1
            with self._queue_lock:
                self._requeue_failed(pending)

    def _http_thread_run(self) -> None:
        try:
            while not self._stop_evt.is_set():
                try:
                    batch = self._http_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if batch is None:
                    # Sentinel: shutdown.
                    return
                try:
                    failed = self._flush_grouped_points(by_destination=batch)
                except Exception as exc:
                    # _flush_grouped_points already catches HTTPError/URLError/
                    # generic per-destination Exception. Reaching here means
                    # an unexpected error in grouping itself; treat the whole
                    # batch as failed and keep the thread alive.
                    self._record_exception(exc, phase="http_thread_batch")
                    self._write_errors += 1
                    self._last_error = f"http thread batch error: {exc}"
                    with self._queue_lock:
                        for points in batch.values():
                            self._requeue_failed(points)
                    self._mark_flush_timestamp()
                    continue
                if failed:
                    with self._queue_lock:
                        self._requeue_failed(failed)
                self._mark_flush_timestamp()
        except Exception as exc:
            self._record_exception(exc, phase="http_thread_fatal")
            self._write_errors += 1
            self._last_error = f"http thread died: {exc}"
            self._http_thread_dead = True
            self._stop_evt.set()

    @staticmethod
    def _normalize_device_list(params: Json) -> list[str]:
        if "device_ids" in params and isinstance(params.get("device_ids"), list):
            out = []
            for item in params["device_ids"]:
                text = str(item).strip()
                if text:
                    out.append(text)
            return out
        device_id = params.get("device_id")
        if device_id is None:
            return []
        text = str(device_id).strip()
        return [text] if text else []

    @staticmethod
    def _destination_status_info(destination: InfluxDestination) -> Json:
        parsed = urlsplit(destination.url)
        host = parsed.hostname or ""
        port: int | None
        try:
            port = parsed.port
        except ValueError:
            port = None
        return {
            "name": destination.name,
            "url": destination.url,
            "scheme": parsed.scheme or "",
            "host": host,
            "port": port,
            "org": destination.org,
            "bucket": destination.bucket,
            "precision": destination.precision,
            "measurement": destination.measurement,
            "request_timeout_s": destination.request_timeout_s,
            "static_tags": dict(destination.static_tags),
            "token_present": bool(destination.token),
        }

    def _measurement_resolution_status(self) -> list[Json]:
        known_device_ids = sorted(set(self._routes.keys()) | set(self._device_type_by_id.keys()))
        rows: list[Json] = []
        for device_id in known_device_ids:
            destination_name = self._resolve_destination(device_id=device_id)
            destination = self._destinations.get(destination_name)
            if destination is None:
                continue
            route = self._routes.get(device_id)
            rows.append(
                {
                    "device_id": device_id,
                    "device_type": self._device_type_by_id.get(device_id),
                    "destination": destination_name,
                    "measurement": self._resolve_measurement(
                        device_id=device_id, destination=destination
                    ),
                    "route_measurement": route.measurement if route is not None else None,
                    "route_device_type": route.device_type if route is not None else None,
                }
            )
        return rows

    def _status_payload(self) -> Json:
        destinations_sorted = sorted(self._destinations.keys())
        destinations_info = [
            self._destination_status_info(self._destinations[name])
            for name in destinations_sorted
        ]
        return {
            "enabled": self._enabled,
            "instance_id": self._instance_id,
            "default_destination": self._default_destination,
            "destinations": destinations_sorted,
            "destinations_info": destinations_info,
            "measurement_resolution": self._measurement_resolution_status(),
            "routes_count": len(self._routes),
            "disabled_devices": sorted(self._disabled_devices),
            "queue_depth": len(self._queue),
            "queue_capacity": self._max_queue_points,
            "overflow_policy": self._overflow_policy,
            "batch_max_points": self._batch_max_points,
            "flush_interval_s": self._flush_interval_s,
            "include_quality_fields": self._include_quality_fields,
            "include_unit_fields": self._include_unit_fields,
            "device_tag_keys": list(self._device_tag_keys),
            "counters": {
                "points_received": self._points_received,
                "points_queued": self._points_queued,
                "points_written": self._points_written,
                "points_skipped_invalid": self._points_skipped_invalid,
                "points_skipped_remote": self._points_skipped_remote,
                "points_dropped_overflow": self._points_dropped_overflow,
                "write_errors": self._write_errors,
                "batches_written": self._batches_written,
                "dropped_http_batches": self._dropped_http_batches,
            },
            "http_thread": {
                "queue_depth": self._http_queue.qsize(),
                "dead": self._http_thread_dead,
            },
            "last_error": self._last_error,
            "last_flush": {
                "t_wall": self._last_flush_wall_s,
                "t_mono": self._last_flush_mono_s,
                "start_wall": self._last_flush_start_wall_s,
                "start_mono": self._last_flush_start_mono_s,
                "duration_s": self._last_flush_duration_s,
                "destination": self._last_flush_destination,
            },
            "telemetry_drain": {
                "last_count": self._last_drain_count,
                "last_duration_s": self._last_drain_duration_s,
                "total_drained": self._total_drained,
                "limited_count": self._drain_limited_count,
                "parse_errors": self._drain_parse_errors,
            },
            "device_type_known_count": len(self._device_type_by_id),
            "remote_device_known_count": len(self._remote_device_ids),
        }

    def _influx_capability_members(self) -> list[Json]:
        members = [
            method("influx.status", params=None, doc="Get influx writer status."),
            method("influx.enable", params=None, doc="Enable ingest/writes."),
            method("influx.disable", params=None, doc="Disable ingest/writes."),
            method("influx.flush", params=None, doc="Flush queued points now."),
            method("influx.devices.get", params=None, doc="Get device filter state."),
            method(
                "influx.devices.enable",
                params=[
                    param("device_id", required=False, default=None, annotation="str"),
                    param("device_ids", required=False, default=None, annotation="list[str]"),
                ],
                doc="Enable one/many devices for writes.",
            ),
            method(
                "influx.devices.disable",
                params=[
                    param("device_id", required=False, default=None, annotation="str"),
                    param("device_ids", required=False, default=None, annotation="list[str]"),
                ],
                doc="Disable one/many devices for writes.",
            ),
        ]
        return self._with_common_capabilities(members)

    def _rpc_influx_capabilities(self, req: Json) -> Json:
        return {
            "request_id": req.get("request_id"),
            "ok": True,
            "result": capabilities_payload(self._influx_capability_members()),
        }

    def _rpc_influx_status(self, req: Json) -> Json:
        return {
            "request_id": req.get("request_id"),
            "ok": True,
            "result": self._status_payload(),
        }

    def _rpc_influx_enable(self, req: Json) -> Json:
        self._enabled = True
        return {
            "request_id": req.get("request_id"),
            "ok": True,
            "result": {"enabled": True},
        }

    def _rpc_influx_disable(self, req: Json) -> Json:
        self._enabled = False
        return {
            "request_id": req.get("request_id"),
            "ok": True,
            "result": {"enabled": False},
        }

    def _rpc_influx_flush(self, req: Json) -> Json:
        self._flush()
        return {
            "request_id": req.get("request_id"),
            "ok": True,
            "result": {"queue_depth": len(self._queue)},
        }

    def _rpc_influx_devices_get(self, req: Json) -> Json:
        return {
            "request_id": req.get("request_id"),
            "ok": True,
            "result": {"disabled_devices": sorted(self._disabled_devices)},
        }

    def _rpc_influx_devices_toggle(self, req: Json, *, enable: bool) -> Json:
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return {
                "request_id": req.get("request_id"),
                "ok": False,
                "error": {"code": "invalid_params"},
            }
        device_ids = self._normalize_device_list(params)
        if not device_ids:
            return {
                "request_id": req.get("request_id"),
                "ok": False,
                "error": {"code": "invalid_params", "message": "missing device_id(s)"},
            }
        for device_id in device_ids:
            if enable:
                self._disabled_devices.discard(device_id)
            else:
                self._disabled_devices.add(device_id)
        return {
            "request_id": req.get("request_id"),
            "ok": True,
            "result": {"disabled_devices": sorted(self._disabled_devices)},
        }

    def _rpc_influx_devices_enable(self, req: Json) -> Json:
        return self._rpc_influx_devices_toggle(req, enable=True)

    def _rpc_influx_devices_disable(self, req: Json) -> Json:
        return self._rpc_influx_devices_toggle(req, enable=False)

    def _build_rpc_registry(self) -> RpcDispatchRegistry:
        handlers = {
            "process.capabilities": self._rpc_influx_capabilities,
            "influx.status": self._rpc_influx_status,
            "influx.enable": self._rpc_influx_enable,
            "influx.disable": self._rpc_influx_disable,
            "influx.flush": self._rpc_influx_flush,
            "influx.devices.get": self._rpc_influx_devices_get,
            "influx.devices.enable": self._rpc_influx_devices_enable,
            "influx.devices.disable": self._rpc_influx_devices_disable,
        }
        return RpcDispatchRegistry(
            handlers=handlers,
            aliases={"influx.get_status": "influx.status"},
        )

    def _handle_rpc(self, req: Json) -> Json:
        common = self._handle_common_rpc(req)
        if common is not None:
            return common
        if not hasattr(self, "_rpc_registry"):
            self._rpc_registry = self._build_rpc_registry()
        dispatched = self._rpc_registry.dispatch_with_canonical(req)
        if dispatched is not None:
            return dispatched
        return self._rpc_unknown(req)

    def _publish_log(self, *, severity: str, message: str) -> None:
        payload: Json = {
            "version": 1,
            "severity": severity,
            "topic": "influx_writer",
            "source_kind": "process",
            "source_id": self._process_id,
            "device_id": None,
            "process_id": self._process_id,
            "message": message,
            "payload_json": json.dumps({"process_id": self._process_id}),
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        }
        if self._try_publish_log_payload(payload):
            return
        normalized_severity = str(severity).strip().lower()
        if normalized_severity in {"error", "critical"}:
            self._emit_stderr_fallback(severity=severity, message=message)
        self._queue_log_payload(payload)

    def _try_publish_log_payload(self, payload: Json, *, timeout_ms: int = 120) -> bool:
        try:
            resp = self._manager.call(
                {"type": "manager.logs.publish", "payload": payload},
                timeout_ms=timeout_ms,
            )
        except Exception:
            return False
        return isinstance(resp, dict) and resp.get("ok") is True

    def _queue_log_payload(self, payload: Json) -> None:
        self._pending_log_payloads.append(payload)

    def _flush_pending_logs(self, *, max_items: int = 8) -> None:
        for _ in range(max(0, int(max_items))):
            if not self._pending_log_payloads:
                return
            payload = self._pending_log_payloads[0]
            if not self._try_publish_log_payload(payload, timeout_ms=80):
                return
            self._pending_log_payloads.popleft()

    @staticmethod
    def _emit_stderr_fallback(*, severity: str, message: str) -> None:
        try:
            sys.stderr.write(f"[influx_writer][{severity}] {message}\n")
            sys.stderr.flush()
        except Exception:
            pass

    def _maybe_publish_last_error(self) -> None:
        text = self._last_error
        if text and text != self._last_published_error_text:
            self._publish_log(severity="error", message=text)
            self._last_published_error_text = text
        elif text is None:
            self._last_published_error_text = None

    def run(self) -> None:
        self._http_thread = threading.Thread(
            target=self._http_thread_run,
            name="influx-http",
            daemon=True,
        )
        self._http_thread.start()
        try:
            next_flush_mono = time.monotonic() + self._flush_interval_s
            while not self._stop_evt.is_set():
                if self._http_thread_dead:
                    self._stop_evt.set()
                    break
                now = time.monotonic()
                timeout_s = max(0.0, next_flush_mono - now)
                timeout_ms = int(max(1.0, min(500.0, timeout_s * 1000.0)))
                self._set_phase("poll", f"timeout_ms={timeout_ms}")
                events = self._poll_and_drain(timeout_ms)
                if events.get(self._sub) == zmq.POLLIN:
                    self._set_phase("drain_telemetry")
                    drain = self._drain_sub()
                    self._mark_progress(
                        f"drained={drain['count']} limited={drain['limited']}"
                    )

                now = time.monotonic()
                if (
                    self._enabled
                    and (
                        now >= next_flush_mono
                        or len(self._queue) >= self._batch_max_points
                    )
                ):
                    self._set_phase("flush", f"queue_depth={len(self._queue)}")
                    self._flush()
                    next_flush_mono = now + self._flush_interval_s
                self._set_phase("publish_error_log")
                self._maybe_publish_last_error()
                self._set_phase("flush_pending_logs")
                self._flush_pending_logs()
                self._set_phase("idle")
                self._mark_progress(f"queue_depth={len(self._queue)}")
        finally:
            # Drain any remaining queued points one last time, then shut the
            # http thread down before closing sockets.
            try:
                self._flush()
            except Exception:
                pass
            try:
                self._http_queue.put(None, timeout=1.0)
            except Exception:
                pass
            thread = self._http_thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=5.0)
            try:
                self._sub.close(0)
            except Exception:
                pass
            self.close()


def main(argv: list[str] | None = None) -> None:
    ns = _parse_args(argv)
    cfg_path_raw = str(ns.config or "").strip()
    if not cfg_path_raw:
        raise SystemExit("--config is required")
    cfg_path = Path(cfg_path_raw).expanduser().resolve()
    raw = load_yaml_file(cfg_path)
    raw_obj = require_dict(raw, path=[])
    parse_version = raw_obj.get("version")
    if parse_version not in {None, 1}:
        raise SystemExit(f"Unsupported config version: {parse_version!r}")

    process_cfg = optional_dict(raw_obj.get("process"), path=["process"])
    init_kwargs = optional_dict(raw_obj.get("init_kwargs"), path=["init_kwargs"])
    if process_cfg:
        process_id = require_str(
            process_cfg.get("process_id", ns.process_id), path=["process", "process_id"]
        )
    else:
        process_id = ns.process_id

    proc = InfluxWriterProcess(
        manager_rpc=ns.manager_rpc,
        manager_pub=ns.manager_pub,
        process_id=process_id,
        rpc_timeout_ms=ns.rpc_timeout_ms,
        heartbeat_endpoint=ns.heartbeat_endpoint,
        heartbeat_period_s=ns.heartbeat_period_s,
        instance_id=init_kwargs.get("instance_id"),
        destinations=optional_dict(init_kwargs.get("destinations"), path=["destinations"]),
        default_destination=init_kwargs.get("default_destination"),
        routes=optional_dict(init_kwargs.get("routes"), path=["routes"]),
        disabled_devices=list(init_kwargs.get("disabled_devices") or []),
        enabled=coerce_bool(init_kwargs.get("enabled"), default=True),
        write_batch_size=coerce_int(init_kwargs.get("write_batch_size"), default=500),
        write_flush_interval_ms=coerce_int(
            init_kwargs.get("write_flush_interval_ms"), default=1000
        ),
        max_queue_points=coerce_int(init_kwargs.get("max_queue_points"), default=100_000),
        overflow_policy=str(init_kwargs.get("overflow_policy", "drop_oldest")),
        include_device_type_tag=coerce_bool(
            init_kwargs.get("include_device_type_tag"), default=True
        ),
        include_quality_fields=coerce_bool(
            init_kwargs.get("include_quality_fields"), default=True
        ),
        include_unit_fields=coerce_bool(
            init_kwargs.get("include_unit_fields"), default=False
        ),
        device_type_key=str(init_kwargs.get("device_type_key", "device_type")),
        device_tag_keys=init_kwargs.get("device_tag_keys", ["location"]),
    )
    proc.run()


if __name__ == "__main__":
    main()
