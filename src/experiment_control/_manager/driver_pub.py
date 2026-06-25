from __future__ import annotations

import json
import time
from typing import Any

import zmq

from ..utils.zmq_helpers import MAX_DRAIN_PER_TICK, safe_json_loads

Json = dict[str, Any]

# MAX_DRAIN_PER_TICK is re-exported from utils.zmq_helpers (defined
# there alongside the sibling poll_and_drain helper).
__all__ = ["MAX_DRAIN_PER_TICK"]


def _positive_limit(manager: Any, attr: str, default: int) -> int:
    # Production ``Manager`` initialises every ``_telemetry_cache_*`` /
    # ``_chunk_cache_*`` limit unconditionally; ``default`` is a safety
    # net for unit-test SimpleNamespace stubs that omit a limit attr.
    try:
        value = int(getattr(manager, attr, default))
    except Exception:
        value = default
    return max(1, value)


def _touch_lru(order: dict[str, None], key: str) -> None:
    if key in order:
        order.pop(key, None)
    order[key] = None


def _cache_counter_inc(manager: Any, attr: str) -> None:
    # Counter attrs (_telemetry_cache_evicted_*, _chunk_cache_evicted_*)
    # are all initialised to 0 in Manager.__init__. The ``or 0`` guard
    # is purely a safety net for unit-test SimpleNamespace stubs that
    # may not bother to pre-seed every counter.
    setattr(manager, attr, int(getattr(manager, attr, 0) or 0) + 1)


def _ensure_cache_state(manager: Any) -> None:
    """Safety net for unit-test stubs that don't initialise LRU bookkeeping.

    Production ``Manager`` initialises ``_telemetry_device_order`` and
    ``_chunk_device_order`` unconditionally via ``ManagerCaches.bind_to_manager``,
    so this helper is a no-op in real usage. It exists so tests that
    instantiate a ``SimpleNamespace`` stub manager (e.g.
    ``test_manager_driver_pub_bounds``) can call into ``ingest_telemetry`` /
    ``ingest_chunk_ready`` without having to manually pre-populate every
    LRU-bookkeeping attribute.
    """
    if not hasattr(manager, "_telemetry_device_order"):
        manager._telemetry_device_order = {}
    if not hasattr(manager, "_telemetry_last_recv_mono"):
        manager._telemetry_last_recv_mono = {}
    if not hasattr(manager, "_chunk_device_order"):
        manager._chunk_device_order = {}


def _evict_one_device(
    manager: Any,
    *,
    device_id: str,
    latest: dict[str, Any],
    order: dict[str, None],
    side_caches: tuple[dict[str, Any], ...],
    max_devices: int,
    counter_attr: str,
) -> None:
    """Evict the oldest device from ``latest`` (+ side caches) if at cap.

    Shared between telemetry and chunk caches so the eviction policy
    has a single source of truth. ``side_caches`` is a tuple of extra
    per-device dicts that should be popped alongside ``latest`` (e.g.,
    telemetry's ``last_bundle_ts`` map). Chunk callers pass ``()``.

    Only triggers when ``device_id`` is brand-new AND ``latest`` is at
    capacity — touching an existing device is a no-op (handled by
    ``_touch_lru`` at the call site).
    """
    if device_id in latest or len(latest) < max_devices:
        return
    oldest = next(iter(order), None)
    if oldest is None and latest:
        oldest = next(iter(latest))
    if oldest is None:
        return
    latest.pop(oldest, None)
    for side in side_caches:
        side.pop(oldest, None)
    order.pop(oldest, None)
    _cache_counter_inc(manager, counter_attr)


def _ensure_telemetry_device_slot(manager: Any, device_id: str) -> dict[str, Any]:
    _ensure_cache_state(manager)
    latest = manager._telemetry_latest
    last_bundle = manager._telemetry_last_bundle_ts
    last_recv = manager._telemetry_last_recv_mono
    order = manager._telemetry_device_order
    _evict_one_device(
        manager,
        device_id=device_id,
        latest=latest,
        order=order,
        side_caches=(last_bundle, last_recv),
        max_devices=_positive_limit(manager, "_telemetry_cache_max_devices", 4096),
        counter_attr="_telemetry_cache_evicted_devices",
    )
    cache = latest.setdefault(device_id, {})
    _touch_lru(order, device_id)
    return cache


def _store_telemetry_signal(
    manager: Any,
    *,
    device_id: str,
    signal_name: str,
    value: tuple[Any, Any],
) -> None:
    device_cache = _ensure_telemetry_device_slot(manager, device_id)
    max_signals = _positive_limit(
        manager, "_telemetry_cache_max_signals_per_device", 4096
    )
    if signal_name in device_cache:
        device_cache.pop(signal_name, None)
    elif len(device_cache) >= max_signals:
        oldest_signal = next(iter(device_cache), None)
        if oldest_signal is not None:
            device_cache.pop(oldest_signal, None)
            _cache_counter_inc(manager, "_telemetry_cache_evicted_signals")
    device_cache[signal_name] = value


def _ensure_chunk_device_slot(manager: Any, device_id: str) -> dict[str, Any]:
    _ensure_cache_state(manager)
    latest = manager._latest_chunk_desc
    order = manager._chunk_device_order
    _evict_one_device(
        manager,
        device_id=device_id,
        latest=latest,
        order=order,
        side_caches=(),
        max_devices=_positive_limit(manager, "_chunk_cache_max_devices", 4096),
        counter_attr="_chunk_cache_evicted_devices",
    )
    cache = latest.setdefault(device_id, {})
    _touch_lru(order, device_id)
    return cache


def _store_chunk_descriptor(manager: Any, *, device_id: str, stream: str, desc: Json) -> None:
    device_cache = _ensure_chunk_device_slot(manager, device_id)
    max_streams = _positive_limit(
        manager, "_chunk_cache_max_streams_per_device", 2048
    )
    if stream in device_cache:
        device_cache.pop(stream, None)
    elif len(device_cache) >= max_streams:
        oldest_stream = next(iter(device_cache), None)
        if oldest_stream is not None:
            device_cache.pop(oldest_stream, None)
            _cache_counter_inc(manager, "_chunk_cache_evicted_streams")
    device_cache[stream] = desc


def _decode_driver_pub_payload(manager: Any, topic: str, payload_b: bytes) -> Json | None:
    msg_any: Any
    try:
        msg_any = safe_json_loads(payload_b)
    except Exception:
        msg_any = None
    if isinstance(msg_any, dict):
        return msg_any
    try:
        msg_any = json.loads(payload_b.decode("utf-8"))
    except Exception as e:
        manager._publish_manager_event(
            "manager.unknown_driver_pub",
            {"topic": topic, "error": f"decode failed: {e}"},
        )
        return None
    if not isinstance(msg_any, dict):
        manager._publish_manager_event(
            "manager.unknown_driver_pub",
            {"topic": topic, "error": "payload not a dict"},
        )
        return None
    return msg_any


def _handle_telemetry_topic(manager: Any, msg: Json) -> None:
    try:
        manager._ingest_telemetry(msg)
    except Exception as e:
        manager._publish_manager_event(
            "manager.telemetry_error",
            {"error": f"telemetry ingest failed: {e}", "raw": msg},
        )


def _translate_heartbeat_pid(msg: Json) -> bool:
    if "pid" not in msg and "driver_pid" in msg:
        msg["pid"] = msg["driver_pid"]
    return "pid" in msg


def _handle_heartbeat_topic(manager: Any, *, topic: str, msg: Json) -> None:
    if not _translate_heartbeat_pid(msg):
        manager._publish_manager_event(
            "manager.unknown_driver_pub",
            {"topic": topic, "error": "heartbeat missing pid", "raw": msg},
        )
        return
    try:
        manager._ingest_heartbeat(msg)
    except Exception as e:
        manager._publish_manager_event(
            "manager.heartbeat_error",
            {"error": f"heartbeat ingest failed: {e}", "raw": msg},
        )


def _handle_chunk_ready_topic(manager: Any, msg: Json) -> None:
    try:
        manager._ingest_chunk_ready(msg)
    except Exception as e:
        manager._publish_manager_event(
            "manager.chunk_error",
            {"error": f"chunk ingest failed: {e}", "raw": msg},
        )


def handle_driver_pub(manager: Any) -> None:
    # Drain all available driver pub messages per tick. One-per-tick
    # was the prior behaviour and caused a backlog under load (~18
    # devices in vacuum-cryo each pumping telemetry + heartbeats at
    # ~1 Hz can saturate a single-message-per-tick drain at 20 Hz).
    # Backlog → telemetry & device-heartbeat checks fire against
    # stale state. The drain cap bounds tick duration.
    for _ in range(MAX_DRAIN_PER_TICK):
        try:
            topic_b, payload_b = manager._sub.recv_multipart(zmq.NOBLOCK)
        except zmq.Again:
            return
        topic = manager._normalize_topic(topic_b.decode("utf-8", errors="replace"))
        msg = _decode_driver_pub_payload(manager, topic, payload_b)
        if msg is None:
            continue
        if topic.endswith("/telemetry"):
            _handle_telemetry_topic(manager, msg)
            continue
        if topic.endswith("/heartbeat"):
            _handle_heartbeat_topic(manager, topic=topic, msg=msg)
            continue
        if topic.endswith("/chunk_ready"):
            _handle_chunk_ready_topic(manager, msg)
            continue
        manager._publish_manager_event(
            "manager.unknown_driver_pub", {"topic": topic, "raw": msg}
        )
    # Loop completed full MAX_DRAIN_PER_TICK iterations without zmq.Again:
    # queue still has data. Surface this (rate-limited) so operators see
    # the backlog instead of silent message lag.
    manager._maybe_publish_drain_cap_hit("driver_pub", MAX_DRAIN_PER_TICK)


def _emit_ingest_error(manager: Any, topic: str, payload: Json) -> None:
    manager._publish_manager_event(topic, payload)


def _parse_bundle_timestamp(
    manager: Any,
    *,
    msg: Json,
    device_id: str,
    error_topic: str,
    error_prefix: str,
    timestamp_cls: Any,
) -> Any:
    ts_raw = msg.get("ts")
    try:
        return manager._parse_timestamp(ts_raw)
    except Exception as e:
        ts = timestamp_cls(t_wall=time.time(), t_mono=time.monotonic())
        _emit_ingest_error(
            manager,
            error_topic,
            {"device_id": device_id, "error": f"{error_prefix}: {e}", "raw": msg},
        )
        return ts


def _require_device_id(manager: Any, *, msg: Json, error_topic: str, noun: str) -> str | None:
    device_id_raw = msg.get("device_id")
    if device_id_raw is None:
        _emit_ingest_error(
            manager,
            error_topic,
            {"error": f"{noun} missing device_id", "raw": msg},
        )
        return None
    return str(device_id_raw)


def _coerce_signal_quality(
    manager: Any,
    *,
    raw_signal: Json,
    telemetry_quality_enum: Any,
) -> tuple[Any, Any]:
    quality_raw = raw_signal.get("quality", telemetry_quality_enum.BAD)
    quality = manager._coerce_enum(
        telemetry_quality_enum, quality_raw, telemetry_quality_enum.BAD
    )
    return quality, quality_raw


def _is_bad_quality_value(quality: Any, quality_raw: Any, telemetry_quality_enum: Any) -> bool:
    return quality is telemetry_quality_enum.BAD and quality_raw not in {
        telemetry_quality_enum.BAD,
        "BAD",
    }


def ingest_telemetry(
    manager: Any,
    msg: Json,
    *,
    telemetry_signal_cls: Any,
    timestamp_cls: Any,
    telemetry_quality_enum: Any,
) -> None:
    device_id = _require_device_id(
        manager,
        msg=msg,
        error_topic="manager.telemetry_error",
        noun="telemetry",
    )
    if device_id is None:
        return
    recv_mono = time.monotonic()
    ts = _parse_bundle_timestamp(
        manager,
        msg=msg,
        device_id=device_id,
        error_topic="manager.telemetry_error",
        error_prefix="telemetry bad ts",
        timestamp_cls=timestamp_cls,
    )
    raw_signals = msg.get("signals")
    if not isinstance(raw_signals, dict):
        _emit_ingest_error(
            manager,
            "manager.telemetry_error",
            {
                "device_id": device_id,
                "error": "telemetry signals must be a dict",
                "raw": msg,
            },
        )
        return
    _ = _ensure_telemetry_device_slot(manager, device_id)
    bad_signals: list[str] = []
    for name, raw_signal in raw_signals.items():
        if not isinstance(name, str) or not isinstance(raw_signal, dict):
            continue
        quality, quality_raw = _coerce_signal_quality(
            manager,
            raw_signal=raw_signal,
            telemetry_quality_enum=telemetry_quality_enum,
        )
        if "quality" in raw_signal and _is_bad_quality_value(
            quality, quality_raw, telemetry_quality_enum
        ):
            bad_signals.append(name)
        sig_ts = None
        if raw_signal.get("ts") is not None:
            try:
                sig_ts = manager._parse_timestamp(raw_signal["ts"])
            except Exception:
                bad_signals.append(name)
                sig_ts = None
        sig = telemetry_signal_cls(
            value=raw_signal.get("value"),
            units=raw_signal.get("units"),
            quality=quality,
            ts=sig_ts,
            quality_source="device",
        )
        _store_telemetry_signal(
            manager,
            device_id=device_id,
            signal_name=name,
            value=(ts, sig),
        )
    manager._telemetry_last_bundle_ts[device_id] = ts
    manager._telemetry_last_recv_mono[device_id] = recv_mono
    _touch_lru(manager._telemetry_device_order, device_id)
    if bad_signals:
        _emit_ingest_error(
            manager,
            "manager.telemetry_error",
            {
                "device_id": device_id,
                "signals": sorted(set(bad_signals)),
                "error": "telemetry had invalid quality or ts",
            },
        )
    seq = int(msg.get("seq", -1))
    republished: Json = {
        "version": 1,
        "device_id": device_id,
        "seq": seq,
        "ts": {"t_wall": ts.t_wall, "t_mono": ts.t_mono, "t_mono_recv": recv_mono},
        "signals": raw_signals,
    }
    # Forward driver-side per-call telemetry exceptions verbatim so the UI
    # can show why a device went DEGRADED without operators having to read
    # driver stderr. The driver enforces a 200-char per-error truncation.
    call_errors = msg.get("call_errors")
    if isinstance(call_errors, dict) and call_errors:
        # Defensively filter to (str, str) entries; never trust unbounded
        # producer payloads on the manager side.
        clean: dict[str, str] = {}
        for key, value in call_errors.items():
            if isinstance(key, str) and isinstance(value, str) and key:
                clean[key] = value
        if clean:
            republished["call_errors"] = clean
    manager._publish_manager_event("manager.telemetry_update", republished)


def _parse_heartbeat_pid(manager: Any, *, msg: Json, device_id: str) -> int | None:
    pid_raw = msg.get("pid")
    if pid_raw is None:
        return None
    try:
        return int(pid_raw)
    except Exception:
        _emit_ingest_error(
            manager,
            "manager.heartbeat_error",
            {"device_id": device_id, "error": "heartbeat bad pid", "raw": msg},
        )
        return None


def _parse_heartbeat_seq(msg: Json) -> int:
    seq_raw = msg.get("seq", -1)
    try:
        return int(seq_raw)
    except Exception:
        return -1


def _heartbeat_state_invalid(state_raw: Any, *, state_enum: Any, field_present: bool) -> bool:
    state_values = {s.value for s in state_enum}
    if isinstance(state_raw, str):
        return state_raw not in state_values
    return field_present and not isinstance(state_raw, state_enum)


def _coerce_heartbeat_states(
    manager: Any,
    *,
    msg: Json,
    device_id: str,
    driver_state_enum: Any,
    device_state_enum: Any,
) -> tuple[Any, Any]:
    driver_state_raw = msg.get("driver_state", driver_state_enum.INIT)
    driver_state = manager._coerce_enum(
        driver_state_enum, driver_state_raw, driver_state_enum.INIT
    )
    device_state_raw = msg.get("device_state", device_state_enum.UNKNOWN)
    device_state = manager._coerce_enum(
        device_state_enum, device_state_raw, device_state_enum.UNKNOWN
    )
    bad_state = _heartbeat_state_invalid(
        driver_state_raw,
        state_enum=driver_state_enum,
        field_present="driver_state" in msg,
    ) or _heartbeat_state_invalid(
        device_state_raw,
        state_enum=device_state_enum,
        field_present="device_state" in msg,
    )
    if bad_state:
        _emit_ingest_error(
            manager,
            "manager.heartbeat_error",
            {
                "device_id": device_id,
                "error": "heartbeat had invalid state values",
                "raw": msg,
            },
        )
    return driver_state, device_state


def _store_heartbeat_on_handle(manager: Any, *, device_id: str, hb: Any) -> None:
    handle = manager._devices.get(device_id)
    if handle is None:
        return
    handle.last_hb = hb
    handle.last_hb_recv_mono = time.monotonic()
    handle.driver_heartbeat_pid = hb.pid
    if handle.driver_pid != hb.pid:
        handle.driver_pid = hb.pid


def ingest_heartbeat(
    manager: Any,
    msg: Json,
    *,
    heartbeat_cls: Any,
    timestamp_cls: Any,
    driver_state_enum: Any,
    device_state_enum: Any,
) -> None:
    device_id = _require_device_id(
        manager,
        msg=msg,
        error_topic="manager.heartbeat_error",
        noun="heartbeat",
    )
    if device_id is None:
        return
    pid = _parse_heartbeat_pid(manager, msg=msg, device_id=device_id)
    if pid is None:
        return
    seq = _parse_heartbeat_seq(msg)
    ts = _parse_bundle_timestamp(
        manager,
        msg=msg,
        device_id=device_id,
        error_topic="manager.heartbeat_error",
        error_prefix="heartbeat bad ts",
        timestamp_cls=timestamp_cls,
    )
    driver_state, device_state = _coerce_heartbeat_states(
        manager,
        msg=msg,
        device_id=device_id,
        driver_state_enum=driver_state_enum,
        device_state_enum=device_state_enum,
    )
    hb = heartbeat_cls(
        pid=pid,
        seq=seq,
        driver_state=driver_state,
        device_reachable=bool(msg.get("device_reachable", False)),
        device_state=device_state,
        device_health=msg.get("device_health"),
        last_error=msg.get("last_error"),
        last_ok_wall=msg.get("last_ok_wall"),
        last_ok_mono=msg.get("last_ok_mono"),
        loop_lag_s=msg.get("loop_lag_s"),
        ts=ts,
    )
    _store_heartbeat_on_handle(manager, device_id=device_id, hb=hb)
    manager._publish_manager_event(
        "manager.heartbeat",
        {
            "version": 1,
            "device_id": device_id,
            "pid": hb.pid,
            "seq": hb.seq,
            "driver_state": hb.driver_state,
            "device_state": hb.device_state,
            "device_reachable": hb.device_reachable,
            "device_health": hb.device_health,
            "last_error": hb.last_error,
            "last_ok_wall": hb.last_ok_wall,
            "last_ok_mono": hb.last_ok_mono,
            "loop_lag_s": hb.loop_lag_s,
            "ts": {"t_wall": hb.ts.t_wall, "t_mono": hb.ts.t_mono},
        },
    )


def _coerce_int_field(desc: Json, key: str) -> None:
    if key in desc and desc[key] is not None:
        try:
            desc[key] = int(desc[key])
        except Exception:
            pass


def _normalized_chunk_descriptor(msg: Json) -> Json | None:
    desc_raw = msg.get("descriptor") if isinstance(msg.get("descriptor"), dict) else msg
    if not isinstance(desc_raw, dict):
        return None
    desc = dict(desc_raw)
    device_id = str(desc.get("device_id") or msg.get("device_id") or "")
    stream = str(desc.get("stream") or msg.get("stream") or "")
    if not device_id or not stream:
        return None
    shm_name = desc.get("shm_name")
    if not shm_name:
        return None
    desc["version"] = int(desc.get("version", 1))
    desc["device_id"] = device_id
    desc["stream"] = stream
    desc["shm_name"] = shm_name
    try:
        desc["layout_version"] = int(desc.get("layout_version", 1))
    except Exception:
        desc["layout_version"] = 1
    _coerce_int_field(desc, "seq")
    _coerce_int_field(desc, "t0_mono_ns")
    _coerce_int_field(desc, "t0_wall_ns")
    return desc


def ingest_chunk_ready(manager: Any, msg: Json) -> None:
    desc = _normalized_chunk_descriptor(msg)
    if desc is None:
        return
    device_id = str(desc["device_id"])
    stream = str(desc["stream"])
    _store_chunk_descriptor(
        manager,
        device_id=device_id,
        stream=stream,
        desc=desc,
    )
    manager._publish_manager_event("manager.chunk_ready", desc)


class DriverPubMixin:
    """Thin mixin exposing the 4 public driver-pub entry points.

    Phase 8.2.13 decision: the 23 module-level helpers in this file
    form a tight cluster of inter-calling functions (eviction +
    decode + ingest paths). Converting them to mixin methods would
    require ~600 LOC of mechanical ``manager.X`` -> ``self.X`` rewrites
    with little benefit (the module is already cohesive and tested
    directly via ``tests.test_manager_driver_pub_bounds``,
    ``tests.test_manager_telemetry_call_errors_forwarding``).

    Instead the mixin wraps only the 4 public entry points
    ``Manager`` ever called as forwarders. The helpers stay as
    module-level pure-ish functions and the tests' direct imports
    keep working unchanged. Net effect: Manager forwarder methods go
    away, MRO does the dispatch, no behavior change, no extra LOC.

    ``_ingest_telemetry`` / ``_ingest_heartbeat`` are kept on
    ``Manager`` itself (not on this mixin) because they need to
    forward the ``TelemetrySignal`` / ``Heartbeat`` / ``Timestamp``
    / ``DriverState`` / ``DeviceState`` / ``TelemetryQuality`` enum
    classes — those are imported at the Manager-module level and
    moving them onto this mixin would force the imports here, making
    a circular reference. The 2-line forwarders on Manager stay.
    """

    def _handle_driver_pub(self) -> None:
        handle_driver_pub(self)

    def _ingest_chunk_ready(self, msg: Json) -> None:
        ingest_chunk_ready(self, msg)
