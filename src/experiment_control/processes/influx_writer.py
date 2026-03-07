from __future__ import annotations

import argparse
import math
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import yaml
import zmq

from ..capabilities import capabilities_payload, method, param
from ..utils.cli_args import (
    add_heartbeat_args,
    add_manager_args,
    add_process_id_arg,
    add_rpc_timeout_arg,
)
from ..utils.config_parsing import optional_dict, require_dict, require_str
from ..utils.value_coercion import coerce_bool, coerce_float, coerce_int
from ..utils.yaml_helpers import load_yaml_file
from ..utils.zmq_helpers import safe_json_loads
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
    tag_parts: list[str] = []
    for key in sorted(tags.keys()):
        value = tags.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        tag_parts.append(
            f"{_escape_tag_component(key)}={_escape_tag_component(text)}"
        )
    if tag_parts:
        line += "," + ",".join(tag_parts)

    field_parts: list[str] = []
    for key in sorted(fields.keys()):
        raw = fields[key]
        if isinstance(raw, bool):
            encoded = "true" if raw else "false"
        elif isinstance(raw, int):
            encoded = f"{int(raw)}i"
        elif isinstance(raw, float):
            if not math.isfinite(raw):
                continue
            encoded = format(float(raw), ".17g")
        elif isinstance(raw, str):
            encoded = f'"{_escape_field_str(raw)}"'
        else:
            continue
        field_parts.append(f"{_escape_field_key(key)}={encoded}")
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
        from_meta = device_metadata.get(metadata_key)
        if isinstance(from_meta, str) and from_meta.strip():
            return from_meta.strip()

    yaml_text = payload.get("yaml_text")
    if not isinstance(yaml_text, str) or not yaml_text.strip():
        return None
    try:
        raw = yaml.safe_load(yaml_text)
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    driver = raw.get("driver")
    if not isinstance(driver, dict):
        return None
    class_name = driver.get("class_name")
    if not isinstance(class_name, str):
        return None
    text = class_name.strip()
    return text or None


class InfluxWriterProcess(ManagedProcessBase):
    def __init__(
        self,
        *,
        manager_rpc: str,
        manager_pub: str,
        process_id: str,
        rpc_timeout_ms: int,
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

    def _parse_destinations(self, raw: dict[str, Any]) -> dict[str, InfluxDestination]:
        out: dict[str, InfluxDestination] = {}
        for dest_name, item in raw.items():
            if not isinstance(item, dict):
                continue
            name = str(dest_name).strip()
            if not name:
                continue
            url = _expand_env_vars(str(item.get("url", "")).strip())
            org = _expand_env_vars(str(item.get("org", "")).strip())
            bucket = _expand_env_vars(str(item.get("bucket", "")).strip())
            token = _expand_env_vars(str(item.get("token", "")).strip())
            if not url or not org or not bucket:
                continue
            measurement = str(item.get("measurement", "unknown_device")).strip()
            if not measurement:
                measurement = "unknown_device"
            precision = str(item.get("precision", "ns")).strip().lower() or "ns"
            if precision not in {"ns", "us", "ms", "s"}:
                precision = "ns"
            request_timeout_s = coerce_float(
                item.get("request_timeout_s"), default=5.0
            )
            static_tags: dict[str, str] = {}
            raw_tags = item.get("static_tags")
            if isinstance(raw_tags, dict):
                for key, value in raw_tags.items():
                    tag_key = str(key).strip()
                    tag_value = str(value).strip()
                    if tag_key and tag_value:
                        static_tags[tag_key] = tag_value
            out[name] = InfluxDestination(
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
        if not out:
            raise ValueError("influx_writer requires at least one destination")
        return out

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

        tags: dict[str, str] = {}
        device_metadata = payload.get("device_metadata")
        if isinstance(device_metadata, dict):
            for key in self._device_tag_keys:
                value = device_metadata.get(key)
                if value is None:
                    continue
                value_text = str(value).strip()
                if value_text:
                    tags[key] = value_text
        self._device_tags_by_id[device_id] = tags

        route = self._routes.get(device_id)
        if route is not None and route.device_type:
            self._device_type_by_id[device_id] = route.device_type
            return
        device_type = _extract_device_type_from_config(
            payload, metadata_key=self._device_type_key
        )
        if device_type:
            self._device_type_by_id[device_id] = device_type

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
        if len(self._queue) >= self._max_queue_points:
            if self._overflow_policy == "drop_newest":
                self._points_dropped_overflow += 1
                return
            self._queue.popleft()
            self._points_dropped_overflow += 1
        self._queue.append(point)
        self._points_queued += 1

    def _drain_sub(self) -> None:
        while True:
            try:
                topic_b, payload_b = self._sub.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break
            except Exception:
                break

            topic = topic_b.decode("utf-8", errors="replace").strip()
            payload = safe_json_loads(payload_b)
            if not isinstance(payload, dict):
                continue
            if topic == "manager.device_config":
                self._handle_device_config(payload)
                continue
            if topic == "manager.telemetry_update":
                self._ingest_telemetry(payload)

    def _ingest_telemetry(self, payload: Json) -> None:
        if not self._enabled:
            return
        device_id = str(payload.get("device_id", "")).strip()
        if not device_id or device_id in self._disabled_devices:
            return
        if device_id in self._remote_device_ids:
            self._points_skipped_remote += 1
            return
        signals = payload.get("signals")
        if not isinstance(signals, dict):
            return

        destination_name = self._resolve_destination(device_id)
        destination = self._destinations.get(destination_name)
        if destination is None:
            self._write_errors += 1
            self._last_error = (
                f"unknown destination {destination_name!r} for device {device_id!r}"
            )
            return

        fields: dict[str, Any] = {}
        for signal_name_raw, signal_payload_raw in signals.items():
            signal_name = str(signal_name_raw).strip()
            if not signal_name or not isinstance(signal_payload_raw, dict):
                continue
            value = signal_payload_raw.get("value")
            quality = str(signal_payload_raw.get("quality", "UNKNOWN")).strip().upper()
            unit_raw = signal_payload_raw.get("units")
            unit = str(unit_raw).strip() if isinstance(unit_raw, str) else ""

            if isinstance(value, bool):
                fields[signal_name] = bool(value)
            elif isinstance(value, int):
                as_i64 = int(value)
                if as_i64 < -(2**63) or as_i64 > 2**63 - 1:
                    continue
                fields[signal_name] = as_i64
            elif isinstance(value, float):
                if not math.isfinite(value):
                    continue
                fields[signal_name] = float(value)
            elif isinstance(value, str):
                fields[signal_name] = value
            else:
                continue

            if self._include_quality_fields:
                fields[f"{signal_name}__quality"] = quality
            if self._include_unit_fields and unit:
                fields[f"{signal_name}__unit"] = unit

        if not fields:
            self._points_skipped_invalid += 1
            return

        self._points_received += 1
        ts_ns = _timestamp_ns_from_bundle(payload)
        measurement = self._resolve_measurement(device_id=device_id, destination=destination)
        tags: dict[str, str] = {
            "instance_id": self._instance_id,
            "device_id": device_id,
        }
        tags.update(destination.static_tags)
        route = self._routes.get(device_id)
        tags.update(self._device_tags_by_id.get(device_id, {}))
        if route is not None and route.tags:
            tags.update(route.tags)
        device_type = self._device_type_by_id.get(device_id)
        if self._include_device_type_tag and device_type:
            tags["device_type"] = device_type

        try:
            line = _build_line_protocol(
                measurement=measurement,
                tags=tags,
                fields=fields,
                ts_ns=ts_ns,
            )
        except Exception:
            self._points_skipped_invalid += 1
            return
        self._enqueue_point(QueuedPoint(destination=destination_name, line=line))

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
        for point in reversed(points):
            if len(self._queue) >= self._max_queue_points:
                if self._overflow_policy == "drop_newest":
                    self._points_dropped_overflow += 1
                    continue
                self._queue.popleft()
                self._points_dropped_overflow += 1
            self._queue.appendleft(point)

    def _flush(self) -> None:
        if not self._queue:
            return
        pending: list[QueuedPoint] = []
        while self._queue:
            pending.append(self._queue.popleft())

        by_destination: dict[str, list[QueuedPoint]] = {}
        for point in pending:
            by_destination.setdefault(point.destination, []).append(point)

        failed: list[QueuedPoint] = []
        for destination_name, points in by_destination.items():
            destination = self._destinations.get(destination_name)
            if destination is None:
                self._write_errors += 1
                self._last_error = f"missing destination {destination_name!r}"
                failed.extend(points)
                continue
            lines = [point.line for point in points]
            try:
                self._write_batch_http(destination=destination, lines=lines)
                self._points_written += len(points)
                self._batches_written += 1
                self._last_error = None
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
                failed.extend(points)
            except URLError as e:
                self._write_errors += 1
                self._last_error = f"URLError destination={destination_name}: {e}"
                failed.extend(points)
            except Exception as e:
                self._write_errors += 1
                self._last_error = f"write failed destination={destination_name}: {e}"
                failed.extend(points)

        if failed:
            self._requeue_failed(failed)

        now_wall = time.time()
        now_mono = time.monotonic()
        self._last_flush_wall_s = now_wall
        self._last_flush_mono_s = now_mono

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

    def _status_payload(self) -> Json:
        return {
            "enabled": self._enabled,
            "instance_id": self._instance_id,
            "default_destination": self._default_destination,
            "destinations": sorted(self._destinations.keys()),
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
            },
            "last_error": self._last_error,
            "last_flush": {
                "t_wall": self._last_flush_wall_s,
                "t_mono": self._last_flush_mono_s,
            },
            "device_type_known_count": len(self._device_type_by_id),
            "remote_device_known_count": len(self._remote_device_ids),
        }

    def _handle_rpc(self, req: Json) -> Json:
        request_id = req.get("request_id")
        rtype = str(req.get("type", "")).strip()
        common = self._handle_common_rpc(req)
        if common is not None:
            return common
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return {
                "request_id": request_id,
                "ok": False,
                "error": {"code": "invalid_params"},
            }

        if rtype == "process.capabilities":
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
            members = self._with_common_capabilities(members)
            return {
                "request_id": request_id,
                "ok": True,
                "result": capabilities_payload(members),
            }

        if rtype == "influx.status":
            return {"request_id": request_id, "ok": True, "result": self._status_payload()}

        if rtype == "influx.enable":
            self._enabled = True
            return {
                "request_id": request_id,
                "ok": True,
                "result": {"enabled": True},
            }

        if rtype == "influx.disable":
            self._enabled = False
            return {
                "request_id": request_id,
                "ok": True,
                "result": {"enabled": False},
            }

        if rtype == "influx.flush":
            self._flush()
            return {
                "request_id": request_id,
                "ok": True,
                "result": {"queue_depth": len(self._queue)},
            }

        if rtype == "influx.devices.get":
            return {
                "request_id": request_id,
                "ok": True,
                "result": {"disabled_devices": sorted(self._disabled_devices)},
            }

        if rtype in {"influx.devices.enable", "influx.devices.disable"}:
            device_ids = self._normalize_device_list(params)
            if not device_ids:
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {"code": "invalid_params", "message": "missing device_id(s)"},
                }
            if rtype == "influx.devices.enable":
                for device_id in device_ids:
                    self._disabled_devices.discard(device_id)
            else:
                for device_id in device_ids:
                    self._disabled_devices.add(device_id)
            return {
                "request_id": request_id,
                "ok": True,
                "result": {"disabled_devices": sorted(self._disabled_devices)},
            }

        return {
            "request_id": request_id,
            "ok": False,
            "error": {"code": "unknown_request"},
        }

    def run(self) -> None:
        try:
            next_flush_mono = time.monotonic() + self._flush_interval_s
            while not self._stop_evt.is_set():
                now = time.monotonic()
                timeout_s = max(0.0, next_flush_mono - now)
                timeout_ms = int(max(1.0, min(500.0, timeout_s * 1000.0)))
                events = self._poll_and_drain(timeout_ms)
                if events.get(self._sub) == zmq.POLLIN:
                    self._drain_sub()

                now = time.monotonic()
                if (
                    self._enabled
                    and (
                        now >= next_flush_mono
                        or len(self._queue) >= self._batch_max_points
                    )
                ):
                    self._flush()
                    next_flush_mono = now + self._flush_interval_s
        finally:
            try:
                self._flush()
            except Exception:
                pass
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
