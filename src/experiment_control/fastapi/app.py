from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .gateway import GatewaySettings, RouterRpcClient, StreamFrameHub, TelemetryHub
from ..shm.shm_ring import ShmRingReader
from ..utils.zmq_helpers import json_dumps as _orjson_dumps
from ..utils.instance_lock import (
    derive_lock_effective_status,
    lock_effective_status_help,
    read_instance_lock_status,
)
from ..utils.network_hosts import (
    is_loopback_host as shared_is_loopback_host,
    server_ipv4_candidates as shared_server_ipv4_candidates,
)
from ..utils.trace_processing import (
    coerce_stream_values_array as _coerce_stream_values_array,
    coerce_trace_array as _coerce_trace_array,
    decimate_trace_values as _decimate_trace_values,
    normalize_shape as _normalize_shape,
    parse_channel_index as _parse_channel_index,
    parse_csv_query_list as _parse_csv_query_list,
    parse_trace_average_mode as _parse_trace_average_mode,
    parse_trace_decimator as _parse_trace_decimator,
    parse_trace_max_fps as _parse_trace_max_fps,
    parse_trace_max_points as _parse_trace_max_points,
    parse_trace_rolling_window as _parse_trace_rolling_window,
    select_trace_from_array as _select_trace_from_array,
)


_EXTRA_UI_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


async def _ws_send_json(ws: WebSocket, msg: Any) -> None:
    """Send a JSON message over WS using the project's orjson-backed
    encoder. Equivalent to `ws.send_json(msg)` (text frame, UTF-8), but
    avoids Starlette's stdlib `json.dumps` — orjson is ~13–22× faster on
    the stream-frame payload shapes the gateway broadcasts. See `json_dumps`
    in `utils/zmq_helpers.py`; falls back to pyzmq's encoder when orjson
    rejects a payload (NaN/Inf, exotic types).
    """
    await ws.send_text(_orjson_dumps(msg).decode("utf-8"))


@dataclass(frozen=True)
class ExtraUiSpec:
    slug: str
    label: str
    dist: Path

    @property
    def href(self) -> str:
        return f"/instance-ui/{self.slug}/"


class DeviceCommandRequest(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None
    source_kind: str | None = None
    source_id: str | None = None


class DeviceRestartRequest(BaseModel):
    force: bool = False


class ProcessCommandRequest(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None
    source_kind: str | None = None
    source_id: str | None = None


@dataclass(frozen=True)
class ProcessCachedCallTarget:
    process_id: str
    action: str
    params: dict[str, Any]
    period_s: float


class HdfWritingStartRequest(BaseModel):
    filename: str | None = None
    disabled_devices: list[str] | None = None
    measurement_profile: str | None = None
    measurement_values: dict[str, Any] | None = None
    source_kind: str | None = None
    source_id: str | None = None


class InstanceCleanupRequest(BaseModel):
    dry_run: bool = True
    stale_only: bool = True
    timeout_s: float = 2.0


class LogTailRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class CommandJournalTailRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class StreamWorkspaceRequest(BaseModel):
    workspace: dict[str, Any]
    expected_revision: int | None = None


class StreamWorkspaceValidateRequest(BaseModel):
    workspace: dict[str, Any] | None = None


class StreamWorkspaceResetRequest(BaseModel):
    node_id: str | None = None


class StreamWorkspaceStoreRequest(BaseModel):
    path: str | None = None


STREAM_ANALYSIS_PROCESS_ID = "stream_analysis"


def _load_settings() -> GatewaySettings:
    router_rpc_public_hint = os.environ.get(
        "EXPERIMENT_CONTROL_ROUTER_RPC_HINT", ""
    ).strip()
    manager_pub_public_hint = os.environ.get(
        "EXPERIMENT_CONTROL_MANAGER_PUB_HINT", ""
    ).strip()
    instance_id = os.environ.get("EXPERIMENT_CONTROL_INSTANCE_ID", "").strip()
    return GatewaySettings(
        router_rpc=os.environ.get(
            "EXPERIMENT_CONTROL_ROUTER_RPC", "tcp://127.0.0.1:6000"
        ),
        manager_pub=os.environ.get(
            "EXPERIMENT_CONTROL_MANAGER_PUB", "tcp://127.0.0.1:6001"
        ),
        instance_id=instance_id or None,
        router_rpc_public_hint=router_rpc_public_hint or None,
        manager_pub_public_hint=manager_pub_public_hint or None,
        rpc_timeout_ms=int(os.environ.get("EXPERIMENT_CONTROL_RPC_TIMEOUT_MS", "2000")),
        rpc_queue_max=max(
            1, int(os.environ.get("EXPERIMENT_CONTROL_RPC_QUEUE_MAX", "1024"))
        ),
        stream_max_payload_points=max(
            1,
            int(
                os.environ.get(
                    "EXPERIMENT_CONTROL_STREAM_MAX_PAYLOAD_POINTS", "200000"
                )
            ),
        ),
        stream_max_record_events=max(
            1,
            int(os.environ.get("EXPERIMENT_CONTROL_STREAM_MAX_RECORD_EVENTS", "512")),
        ),
        stream_max_keys=max(
            1, int(os.environ.get("EXPERIMENT_CONTROL_STREAM_MAX_KEYS", "1024"))
        ),
        stream_key_ttl_s=max(
            0.0,
            float(os.environ.get("EXPERIMENT_CONTROL_STREAM_KEY_TTL_S", "600")),
        ),
    )


def _ensure_error_shape(resp: Any) -> dict[str, Any]:
    if not isinstance(resp, dict):
        return {
            "ok": False,
            "error": {
                "code": "invalid_response",
                "message": "router response was not a dict",
            },
        }
    if resp.get("ok") is False and isinstance(resp.get("error"), str):
        resp["error"] = {"code": "error", "message": resp["error"]}
    return resp


async def _route_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch ``payload`` through the router and shape the response.

    Most HTTP handlers follow the pattern ``payload = {...}; resp = await
    app.state.router.request(payload); return _ensure_error_shape(resp)``.
    Use this helper for those. Handlers that post-process the response
    (filter, reshape, fan out, etc.) should keep calling the router
    directly.
    """
    return _ensure_error_shape(await app.state.router.request(payload))


def _build_trace_frame_payload(
    payload: dict[str, Any],
    *,
    channel_index: int,
    trace_decimator: str,
    trace_max_points: int | None,
    pre_decimate: Callable[[np.ndarray], np.ndarray | None] | None = None,
) -> dict[str, Any] | None:
    """Shared trace-processing chain used by /ws/raw_stream and
    /api/streams/raw_snapshot.

    Normalises shape, coerces values to an ndarray, selects the requested
    channel, optionally runs a caller-supplied pre-decimate step (used by
    the WS handler for rolling-average) and decimates to ``trace_max_points``.
    Returns the rebuilt outgoing payload (with ``shape``/``values``/
    ``channel_index``/``point_count``/optional ``decimated`` set), or ``None``
    if the input payload was unusable.
    """
    shape = _normalize_shape(payload.get("shape"))
    arr = _coerce_stream_values_array(payload.get("values"), shape)
    if arr is None:
        return None
    trace = _select_trace_from_array(arr, channel_index)
    if pre_decimate is not None:
        trace = pre_decimate(trace)
        if trace is None:
            return None
    if trace_max_points is not None:
        trace_values = _decimate_trace_values(
            trace,
            mode=trace_decimator,
            max_points=trace_max_points,
        )
    else:
        trace_values = trace.tolist()
    if not isinstance(trace_values, list):
        return None
    out_payload: dict[str, Any] = dict(payload)
    out_payload["shape"] = [len(trace_values)]
    out_payload["values"] = trace_values
    out_payload["channel_index"] = int(channel_index)
    out_payload["point_count"] = len(trace_values)
    if trace_max_points is not None and len(trace_values) < int(trace.size):
        out_payload["decimated"] = True
    return out_payload


def _command_source_fields(
    request: Request,
    *,
    source_kind: str | None = None,
    source_id: str | None = None,
) -> dict[str, str]:
    kind = str(source_kind or "").strip()
    if not kind:
        kind = str(request.headers.get("x-ec-source-kind", "") or "").strip()
    if not kind:
        kind = "webui"

    ident = str(source_id or "").strip()
    if not ident:
        ident = str(request.headers.get("x-ec-source-id", "") or "").strip()
    if not ident:
        ident = "fastapi"

    return {"source_kind": kind, "source_id": ident}


def _normalize_command_response(resp: Any) -> dict[str, Any]:
    resp = _ensure_error_shape(resp)
    if "ok" in resp:
        return resp
    status = str(resp.get("status", "")).upper()
    ok = status == "OK" or resp.get("ok") is True
    out: dict[str, Any] = {"ok": ok, "result": resp.get("result")}
    if not ok:
        out["error"] = {
            "code": "device_error",
            "message": resp.get("error") or "device error",
            "details": resp,
        }
    return out


_TRANSIENT_CAPABILITIES_ERROR_CODES = {
    "device_rpc_timeout",
    "device_starting",
    "device_stopping",
    "device_rpc_not_ready",
    "driver_not_running",
    "gateway_busy",
    "gateway_timeout",
}


def _command_error_code(resp: dict[str, Any]) -> str:
    err = resp.get("error")
    if isinstance(err, dict):
        return str(err.get("code", "") or "").strip().lower()
    return ""


def _command_error_message(resp: dict[str, Any]) -> str:
    err = resp.get("error")
    if isinstance(err, dict):
        return str(err.get("message", "") or "").strip().lower()
    if isinstance(err, str):
        return err.strip().lower()
    return ""


def _is_transient_capabilities_failure(resp: dict[str, Any]) -> bool:
    if not isinstance(resp, dict) or resp.get("ok") is not False:
        return False
    code = _command_error_code(resp)
    if code in _TRANSIENT_CAPABILITIES_ERROR_CODES:
        return True
    err = resp.get("error")
    if isinstance(err, dict):
        if bool(err.get("transient")):
            return True
        details = err.get("details")
        if isinstance(details, dict):
            nested_msg = str(details.get("message", "") or "").strip().lower()
            if "resource temporarily unavailable" in nested_msg:
                return True
    msg = _command_error_message(resp)
    if "resource temporarily unavailable" in msg:
        return True
    if code in {"gateway_error", "device_error", "error"} and "timed out" in msg:
        return True
    return False


async def _request_device_capabilities_with_retry(
    payload: dict[str, Any],
) -> dict[str, Any]:
    first = await app.state.router.request(payload)
    first_shaped = _normalize_command_response(first)
    if not _is_transient_capabilities_failure(first_shaped):
        return first_shaped

    await asyncio.sleep(0.2)
    retry_payload = dict(payload)
    retry_payload["request_id"] = uuid.uuid4().hex
    second = await app.state.router.request(retry_payload)
    second_shaped = _normalize_command_response(second)
    if _is_transient_capabilities_failure(second_shaped):
        err = second_shaped.get("error")
        if isinstance(err, dict):
            err.setdefault("transient", True)
            err.setdefault("retryable", True)
            err.setdefault("retry_attempted", True)
    return second_shaped


def _normalize_command_interceptor_routes(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        process_id = str(item.get("process_id", "")).strip()
        device_id = str(item.get("device_id", "")).strip()
        action = str(item.get("action", "")).strip()
        if not process_id or not device_id or not action:
            continue
        try:
            order = int(item.get("order", 0))
        except Exception:
            continue
        out.append(
            {
                "order": order,
                "process_id": process_id,
                "device_id": device_id,
                "action": action,
            }
        )
    out.sort(key=lambda item: int(item.get("order", 0)))
    return out


async def _fetch_manager_identity(router: RouterRpcClient) -> dict[str, Any] | None:
    request_id = uuid.uuid4().hex
    payload = {"type": "manager.info.identity", "request_id": request_id}
    try:
        resp = await router.request(payload)
    except Exception:
        return None
    shaped = _ensure_error_shape(resp)
    if not shaped.get("ok"):
        return None
    result = shaped.get("result")
    if not isinstance(result, dict):
        return None
    return result


async def _lookup_process_status(process_id: str) -> dict[str, Any] | None:
    payload = {"type": "manager.processes.list"}
    try:
        resp = await app.state.router.request(payload)
    except Exception:
        return None
    shaped = _ensure_error_shape(resp)
    if not shaped.get("ok"):
        return None
    result = shaped.get("result")
    if not isinstance(result, list):
        return None
    pid = str(process_id or "").strip()
    if not pid:
        return None
    for item in result:
        if not isinstance(item, dict):
            continue
        if str(item.get("process_id", "")).strip() == pid:
            return item
    return None


def _process_rpc_registered(status: dict[str, Any]) -> bool:
    if "registered" in status:
        return bool(status.get("registered"))
    rpc_endpoint = status.get("rpc_endpoint")
    return isinstance(rpc_endpoint, str) and bool(rpc_endpoint.strip())


async def _fetch_instance_runtime_status(
    *,
    requested_instance_id: str | None,
    router: RouterRpcClient | None,
) -> dict[str, Any]:
    instance_id = str(requested_instance_id or "").strip() or "unknown"
    manager_identity: dict[str, Any] | None = None
    if router is not None:
        manager_identity = await _fetch_manager_identity(router)
        if isinstance(manager_identity, dict):
            manager_instance_id = str(manager_identity.get("instance_id", "")).strip()
            if manager_instance_id:
                instance_id = manager_instance_id
    started_ts = (
        manager_identity.get("started_ts")
        if isinstance(manager_identity, dict)
        else None
    )
    if not isinstance(started_ts, dict):
        started_ts = None
    last_orphan_cleanup = (
        manager_identity.get("last_orphan_cleanup")
        if isinstance(manager_identity, dict)
        else None
    )
    if not isinstance(last_orphan_cleanup, dict):
        last_orphan_cleanup = None
    manager_reachable = isinstance(manager_identity, dict)
    if isinstance(manager_identity, dict):
        identity_lock_status = manager_identity.get("lock_status")
    else:
        identity_lock_status = None
    if isinstance(identity_lock_status, dict):
        lock_status = identity_lock_status
    else:
        lock_status = read_instance_lock_status(instance_id)
    manager_pid: int | None = None
    if isinstance(manager_identity, dict):
        manager_pid_raw = manager_identity.get("manager_pid")
        try:
            manager_pid_candidate = int(manager_pid_raw)
            if manager_pid_candidate > 0:
                manager_pid = manager_pid_candidate
        except Exception:
            manager_pid = None
    identity_effective_raw: str | None = None
    if isinstance(manager_identity, dict):
        identity_effective_raw = manager_identity.get("lock_effective_status")
        if isinstance(identity_effective_raw, str) and identity_effective_raw.strip():
            identity_effective_raw = identity_effective_raw.strip().lower()
        else:
            identity_effective_raw = None
    lock_effective_status = derive_lock_effective_status(
        lock_status=lock_status,
        manager_pid=manager_pid,
        manager_reachable=manager_reachable,
        reported_effective_status=identity_effective_raw,
    )
    return {
        "instance_id": instance_id,
        "started_ts": started_ts,
        "manager_pid": manager_pid,
        "manager_reachable": manager_reachable,
        "lock_status": lock_status,
        "lock_effective_status": lock_effective_status,
        "lock_effective_help": lock_effective_status_help(lock_effective_status),
        "last_orphan_cleanup": last_orphan_cleanup,
    }


async def _process_rpc(
    process_id: str, action: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    payload = {
        "type": "manager.processes.rpc",
        "request_id": request_id,
        "process_id": process_id,
        "request": {
            "type": str(action),
            "params": dict(params or {}),
            "request_id": request_id,
        },
    }
    resp = await app.state.router.request(payload)
    shaped = _ensure_error_shape(resp)
    if shaped.get("ok") is True:
        got = shaped.get("request_id")
        if isinstance(got, str) and got and got != request_id:
            return {
                "ok": False,
                "error": {
                    "code": "rpc_request_id_mismatch",
                    "message": (
                        f"manager.processes.rpc request_id mismatch: expected "
                        f"{request_id}, got {got}"
                    ),
                },
            }
    return shaped


async def _stream_analysis_rpc(
    action: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    return await _process_rpc(STREAM_ANALYSIS_PROCESS_ID, action, params)


def _process_cached_call_key(
    process_id: str, action: str, params: dict[str, Any] | None = None
) -> str:
    params_json = json.dumps(dict(params or {}), sort_keys=True, separators=(",", ":"))
    return f"{process_id}\u241f{action}\u241f{params_json}"


def _normalize_process_cached_call_targets(raw: Any) -> list[ProcessCachedCallTarget]:
    if not isinstance(raw, list):
        return []
    out: list[ProcessCachedCallTarget] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        process_id = str(item.get("process_id", "") or "").strip()
        action = str(item.get("action", "") or "").strip()
        params_raw = item.get("params")
        params = dict(params_raw) if isinstance(params_raw, dict) else {}
        if not process_id or not action:
            continue
        try:
            period_s = float(item.get("period_s", 1.0))
        except Exception:
            period_s = 1.0
        key = _process_cached_call_key(process_id, action, params)
        if key in seen:
            continue
        out.append(
            ProcessCachedCallTarget(
                process_id=process_id,
                action=action,
                params=params,
                period_s=max(0.1, period_s),
            )
        )
        seen.add(key)
    return out


def _load_process_cached_call_targets() -> list[ProcessCachedCallTarget]:
    raw_text = os.environ.get("EXPERIMENT_CONTROL_PROCESS_CACHED_CALLS_JSON", "").strip()
    if not raw_text:
        return []
    try:
        raw = json.loads(raw_text)
    except Exception as e:
        sys.stderr.write(
            f"[fastapi] ignoring invalid EXPERIMENT_CONTROL_PROCESS_CACHED_CALLS_JSON: {e}\n"
        )
        return []
    targets = _normalize_process_cached_call_targets(raw)
    if not targets:
        sys.stderr.write(
            "[fastapi] ignoring EXPERIMENT_CONTROL_PROCESS_CACHED_CALLS_JSON: no valid targets.\n"
        )
    return targets


async def _process_cached_call_loop(target: ProcessCachedCallTarget) -> None:
    cache: dict[str, dict[str, Any]] = app.state.process_cached_calls
    key = _process_cached_call_key(target.process_id, target.action, target.params)
    while True:
        # Use monotonic for period accounting so a wall-clock jump
        # (NTP correction, manual time change) doesn't either starve
        # the loop or make it spin. Wall-clock `time.time()` is kept
        # only for the `updated_at` field exposed to the UI.
        started_mono = time.monotonic()
        try:
            status = await _lookup_process_status(target.process_id)
            if not isinstance(status, dict):
                resp = {
                    "ok": False,
                    "error": {
                        "code": "process_not_running",
                        "message": "process is not running",
                    },
                }
            elif not _process_rpc_registered(status):
                resp = {
                    "ok": False,
                    "error": {
                        "code": "process_rpc_not_ready",
                        "message": "process RPC is not ready",
                    },
                }
            else:
                resp = await _process_rpc(target.process_id, target.action, target.params)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            resp = {
                "ok": False,
                "error": {"code": "gateway_error", "message": str(e)},
            }
        cache[key] = {
            **_ensure_error_shape(resp),
            "cached": True,
            "updated_at": time.time(),
        }
        await asyncio.sleep(
            max(0.0, target.period_s - (time.monotonic() - started_mono))
        )


def _is_loopback_host(raw_host: str | None) -> bool:
    return bool(shared_is_loopback_host(raw_host))


def _extract_host(endpoint: str) -> str | None:
    text = str(endpoint or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
        if parsed.hostname:
            return parsed.hostname
        parsed = urlsplit(f"//{text}")
        return parsed.hostname
    except Exception:
        return None


def _replace_endpoint_host(endpoint: str, new_host: str) -> str:
    text = str(endpoint or "").strip()
    host = str(new_host or "").strip()
    if not text or not host:
        return text
    try:
        parsed = urlsplit(text)
        if not parsed.hostname:
            return text
        out_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        out_netloc = out_host
        if parsed.port is not None:
            out_netloc = f"{out_host}:{parsed.port}"
        rebuilt = SplitResult(
            scheme=parsed.scheme,
            netloc=out_netloc,
            path=parsed.path,
            query=parsed.query,
            fragment=parsed.fragment,
        )
        return urlunsplit(rebuilt)
    except Exception:
        return text


def _server_ip_candidates() -> list[str]:
    return shared_server_ipv4_candidates()


def _request_origin_and_host(request: Request) -> tuple[str, str | None]:
    forwarded_proto = str(request.headers.get("x-forwarded-proto", "")).strip()
    forwarded_host = str(request.headers.get("x-forwarded-host", "")).strip()
    scheme = (
        forwarded_proto.split(",")[0].strip()
        if forwarded_proto
        else str(request.url.scheme)
    )
    host_part = (
        forwarded_host.split(",")[0].strip()
        if forwarded_host
        else str(request.headers.get("host") or request.url.netloc)
    )
    if not host_part:
        base = str(request.base_url).rstrip("/")
        parsed = urlsplit(base)
        return base, parsed.hostname
    origin = f"{scheme}://{host_part}"
    parsed = urlsplit(origin)
    return origin, parsed.hostname


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    text = raw.strip().lower()
    return text in {"1", "true", "yes", "on"}


def _default_ui_dist_path() -> Path:
    # Prefer packaged static assets so `pip install experiment-control` works
    # without requiring a local repo checkout.
    packaged = Path(__file__).resolve().parents[1] / "_ui_dist"
    if (packaged / "index.html").exists():
        return packaged

    # Dev fallback when running from source tree without packaged assets.
    repo_root = Path(__file__).resolve().parents[3]
    repo_dist = repo_root / "web" / "react_ui" / "dist"
    if (repo_dist / "index.html").exists():
        return repo_dist

    # In installed environments without bundled UI, keep pointing at packaged
    # location so error messages are accurate and don't reference invalid
    # venv-relative repo paths (e.g. .../.venv/Lib/web/react_ui/dist).
    return packaged


def _resolve_ui_dist_path() -> Path | None:
    if not _env_bool("EXPERIMENT_CONTROL_SERVE_UI", default=False):
        return None
    raw = os.environ.get("EXPERIMENT_CONTROL_UI_DIST", "").strip()
    path = Path(raw).expanduser().resolve() if raw else _default_ui_dist_path()
    index = path / "index.html"
    if not path.exists() or not index.exists():
        sys.stderr.write(
            f"[fastapi] ui serving enabled but build not found at {str(path)!r}; "
            "continuing with API-only mode.\n"
        )
        return None
    return path.resolve()


def _resolve_default_profile_path() -> Path | None:
    raw = os.environ.get("EXPERIMENT_CONTROL_DEFAULT_PROFILE", "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    if not path.exists() or not path.is_file():
        sys.stderr.write(
            f"[fastapi] default profile path does not exist: {str(path)!r}; "
            "the /api/ui/default_profile endpoint will return 404.\n"
        )
        return None
    return path


def _resolve_extra_ui_specs() -> list[ExtraUiSpec]:
    if not _env_bool("EXPERIMENT_CONTROL_SERVE_UI", default=False):
        return []
    raw = os.environ.get("EXPERIMENT_CONTROL_EXTRA_UI_JSON", "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception as e:
        sys.stderr.write(
            f"[fastapi] ignoring invalid EXPERIMENT_CONTROL_EXTRA_UI_JSON: {e}\n"
        )
        return []
    if not isinstance(parsed, list):
        sys.stderr.write(
            "[fastapi] ignoring EXPERIMENT_CONTROL_EXTRA_UI_JSON: expected a list.\n"
        )
        return []

    specs: list[ExtraUiSpec] = []
    seen_slugs: set[str] = set()
    for idx, item in enumerate(parsed):
        if not isinstance(item, dict):
            sys.stderr.write(f"[fastapi] ignoring extra UI #{idx}: expected object.\n")
            continue
        slug = str(item.get("slug", "") or "").strip()
        label = str(item.get("label", "") or "").strip()
        dist_raw = str(item.get("dist", "") or "").strip()
        if not slug or not _EXTRA_UI_SLUG_RE.fullmatch(slug):
            sys.stderr.write(
                f"[fastapi] ignoring extra UI #{idx}: invalid slug {slug!r}.\n"
            )
            continue
        if slug in seen_slugs:
            sys.stderr.write(
                f"[fastapi] ignoring extra UI #{idx}: duplicate slug {slug!r}.\n"
            )
            continue
        if not label:
            label = slug.replace("-", " ").title()
        if not dist_raw:
            sys.stderr.write(
                f"[fastapi] ignoring extra UI {slug!r}: missing dist path.\n"
            )
            continue
        dist = Path(dist_raw).expanduser().resolve()
        if not dist.exists() or not (dist / "index.html").is_file():
            sys.stderr.write(
                f"[fastapi] ignoring extra UI {slug!r}: build not found at "
                f"{str(dist)!r}.\n"
            )
            continue
        specs.append(ExtraUiSpec(slug=slug, label=label, dist=dist))
        seen_slugs.add(slug)
    return specs


app = FastAPI(title="Experiment Control Gateway", version="0.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_UI_DIST_PATH: Path | None = _resolve_ui_dist_path()
_DEFAULT_PROFILE_PATH: Path | None = _resolve_default_profile_path()
_EXTRA_UI_SPECS: list[ExtraUiSpec] = _resolve_extra_ui_specs()
_EXTRA_UI_BY_SLUG: dict[str, ExtraUiSpec] = {
    spec.slug: spec for spec in _EXTRA_UI_SPECS
}


@app.on_event("startup")
async def _startup() -> None:
    settings = _load_settings()
    router = RouterRpcClient(
        settings.router_rpc,
        timeout_ms=settings.rpc_timeout_ms,
        queue_max=settings.rpc_queue_max,
    )
    router.start(asyncio.get_running_loop())
    manager_identity = await _fetch_manager_identity(router)
    telemetry_hub = TelemetryHub(settings.manager_pub, topics=settings.telemetry_topics)
    telemetry_hub.start(asyncio.get_running_loop())
    logs_hub = TelemetryHub(settings.manager_pub, topics=settings.log_topics)
    logs_hub.start(asyncio.get_running_loop())
    stream_hub = StreamFrameHub(
        settings.manager_pub,
        topics=settings.stream_topics,
        max_payload_points=settings.stream_max_payload_points,
        max_record_events=settings.stream_max_record_events,
        max_stream_keys=settings.stream_max_keys,
        stream_key_ttl_s=settings.stream_key_ttl_s,
    )
    stream_hub.start(asyncio.get_running_loop())
    stream_analysis_hub = TelemetryHub(
        settings.manager_pub, topics=settings.stream_analysis_topics
    )
    stream_analysis_hub.start(asyncio.get_running_loop())
    process_cached_call_targets = _load_process_cached_call_targets()
    process_cached_calls: dict[str, dict[str, Any]] = {}
    app.state.settings = settings
    app.state.manager_identity = manager_identity
    app.state.router = router
    app.state.telemetry_hub = telemetry_hub
    app.state.logs_hub = logs_hub
    app.state.stream_hub = stream_hub
    app.state.stream_analysis_hub = stream_analysis_hub
    app.state.process_cached_calls = process_cached_calls
    app.state.process_cached_call_targets = process_cached_call_targets
    app.state.process_cached_call_tasks = [
        asyncio.create_task(_process_cached_call_loop(target))
        for target in process_cached_call_targets
    ]


@app.on_event("shutdown")
async def _shutdown() -> None:
    router: RouterRpcClient | None = getattr(app.state, "router", None)
    telemetry_hub: TelemetryHub | None = getattr(app.state, "telemetry_hub", None)
    logs_hub: TelemetryHub | None = getattr(app.state, "logs_hub", None)
    stream_hub: StreamFrameHub | None = getattr(app.state, "stream_hub", None)
    stream_analysis_hub: TelemetryHub | None = getattr(
        app.state, "stream_analysis_hub", None
    )
    process_cached_call_tasks: list[asyncio.Task] = getattr(
        app.state, "process_cached_call_tasks", []
    )
    for task in process_cached_call_tasks:
        task.cancel()
    if process_cached_call_tasks:
        await asyncio.gather(*process_cached_call_tasks, return_exceptions=True)
    if telemetry_hub is not None:
        telemetry_hub.close()
    if logs_hub is not None:
        logs_hub.close()
    if stream_hub is not None:
        stream_hub.close()
    if stream_analysis_hub is not None:
        stream_analysis_hub.close()
    if router is not None:
        router.close()


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/api/ui/default_profile")
async def ui_default_profile() -> FileResponse:
    if _DEFAULT_PROFILE_PATH is None:
        raise HTTPException(status_code=404, detail="no default profile configured")
    return FileResponse(_DEFAULT_PROFILE_PATH, media_type="application/json")


@app.get("/api/ui/extra")
async def ui_extra() -> dict[str, Any]:
    return {
        "ok": True,
        "result": {
            "items": [
                {"slug": spec.slug, "label": spec.label, "href": spec.href}
                for spec in _EXTRA_UI_SPECS
            ]
        },
    }


@app.get("/api/settings")
async def settings_view(request: Request) -> dict[str, Any]:
    settings: GatewaySettings = app.state.settings
    manager_identity = getattr(app.state, "manager_identity", None)
    if not isinstance(manager_identity, dict):
        router: RouterRpcClient | None = getattr(app.state, "router", None)
        if router is not None:
            manager_identity = await _fetch_manager_identity(router)
            if isinstance(manager_identity, dict):
                app.state.manager_identity = manager_identity
    instance_id = settings.instance_id
    if isinstance(manager_identity, dict):
        manager_instance_id = manager_identity.get("instance_id")
        if isinstance(manager_instance_id, str) and manager_instance_id.strip():
            instance_id = manager_instance_id.strip()
    api_origin, api_host = _request_origin_and_host(request)
    host_ip_candidates = _server_ip_candidates()

    router_host = _extract_host(settings.router_rpc)
    manager_host = _extract_host(settings.manager_pub)
    loopback_warning = _is_loopback_host(router_host) or _is_loopback_host(manager_host)

    preferred_host: str | None = None
    if api_host and not _is_loopback_host(api_host):
        preferred_host = api_host
    elif host_ip_candidates:
        preferred_host = host_ip_candidates[0]

    router_rpc_hint = settings.router_rpc_public_hint or settings.router_rpc
    manager_pub_hint = settings.manager_pub_public_hint or settings.manager_pub
    if preferred_host:
        if settings.router_rpc_public_hint is None and _is_loopback_host(router_host):
            router_rpc_hint = _replace_endpoint_host(settings.router_rpc, preferred_host)
        if settings.manager_pub_public_hint is None and _is_loopback_host(manager_host):
            manager_pub_hint = _replace_endpoint_host(settings.manager_pub, preferred_host)
    router: RouterRpcClient | None = getattr(app.state, "router", None)
    rpc_queue: dict[str, Any] | None = None
    if router is not None:
        rpc_queue = router.stats()
    stream_hub: StreamFrameHub | None = getattr(app.state, "stream_hub", None)
    stream_state: dict[str, Any] | None = None
    if stream_hub is not None:
        stream_state = stream_hub.stats()

    return {
        "ok": True,
        "result": {
            "router_rpc": settings.router_rpc,
            "manager_pub": settings.manager_pub,
            "instance_id": instance_id,
            "router_rpc_hint": router_rpc_hint,
            "manager_pub_hint": manager_pub_hint,
            "rpc_timeout_ms": settings.rpc_timeout_ms,
            "rpc_queue_max": settings.rpc_queue_max,
            "rpc_queue": rpc_queue,
            "stream_max_payload_points": settings.stream_max_payload_points,
            "stream_max_record_events": settings.stream_max_record_events,
            "stream_max_keys": settings.stream_max_keys,
            "stream_key_ttl_s": settings.stream_key_ttl_s,
            "stream_state": stream_state,
            "telemetry_topics": list(settings.telemetry_topics),
            "log_topics": list(settings.log_topics),
            "stream_topics": list(settings.stream_topics),
            "stream_analysis_topics": list(settings.stream_analysis_topics),
            "api_origin": api_origin,
            "api_host": api_host,
            "host_ip_candidates": host_ip_candidates,
            "loopback_warning": loopback_warning,
            "loopback_warning_message": (
                "Configured router/PUB endpoints use loopback; remote clients should use LAN IP or hostname."
                if loopback_warning
                else ""
            ),
        },
    }


@app.get("/api/instance/runtime")
async def instance_runtime_view() -> dict[str, Any]:
    settings: GatewaySettings = app.state.settings
    router: RouterRpcClient | None = getattr(app.state, "router", None)
    status = await _fetch_instance_runtime_status(
        requested_instance_id=settings.instance_id,
        router=router,
    )
    return {"ok": True, "result": status}


@app.post("/api/instance/cleanup_orphans")
async def instance_cleanup_orphans(
    req: InstanceCleanupRequest | None = None,
) -> dict[str, Any]:
    params = {
        "dry_run": bool(req.dry_run) if req is not None else True,
        "stale_only": bool(req.stale_only) if req is not None else True,
        "timeout_s": float(req.timeout_s) if req is not None else 2.0,
    }
    payload = {"type": "manager.control.cleanup_orphans", "params": params}
    return await _route_request(payload)


@app.get("/api/devices")
async def list_devices() -> dict[str, Any]:
    payload = {"type": "device.list_status"}
    return await _route_request(payload)


@app.get("/api/snapshots/telemetry")
async def telemetry_snapshot() -> dict[str, Any]:
    payload = {"type": "manager.telemetry.snapshot"}
    return await _route_request(payload)


@app.get("/api/streams")
async def list_streams() -> dict[str, Any]:
    payload = {"type": "device.config.list"}
    resp = await app.state.router.request(payload)
    resp = _ensure_error_shape(resp)
    if not resp.get("ok"):
        return resp
    configs = resp.get("result")
    if not isinstance(configs, list):
        return {"ok": True, "result": []}

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in configs:
        if not isinstance(item, dict):
            continue
        device_id = str(item.get("device_id") or "").strip()
        if not device_id:
            continue
        stream_calls = item.get("stream_calls")
        if not isinstance(stream_calls, list):
            continue
        for call in stream_calls:
            if not isinstance(call, dict):
                continue
            outputs = call.get("outputs")
            if not isinstance(outputs, list):
                continue
            for output in outputs:
                if not isinstance(output, dict):
                    continue
                stream = str(output.get("stream") or "").strip()
                if not stream:
                    continue
                key = (device_id, stream)
                if key in seen:
                    continue
                seen.add(key)
                shape_raw = output.get("shape")
                shape = (
                    [int(v) for v in shape_raw if isinstance(v, (int, float))]
                    if isinstance(shape_raw, list)
                    else []
                )
                kind = str(output.get("kind") or "frame")
                out.append(
                    {
                        "device_id": device_id,
                        "stream": stream,
                        "kind": kind,
                        "dtype": str(output.get("dtype") or ""),
                        "shape": shape,
                        "fields": output.get("fields") if kind == "records" else [],
                        "units": output.get("units"),
                        "description": output.get("description"),
                    }
                )

    out.sort(key=lambda item: (str(item.get("device_id")), str(item.get("stream"))))
    return {"ok": True, "result": out}


@app.get("/api/devices/{device_id}/capabilities")
async def device_capabilities(device_id: str, request: Request) -> dict[str, Any]:
    payload = {
        "type": "command",
        "device_id": device_id,
        "action": "capabilities",
        "params": {},
        "request_id": uuid.uuid4().hex,
        **_command_source_fields(request),
    }
    return await _request_device_capabilities_with_retry(payload)


@app.post("/api/devices/{device_id}/call")
async def device_call(
    device_id: str, req: DeviceCommandRequest, request: Request
) -> dict[str, Any]:
    payload = {
        "type": "command",
        "device_id": device_id,
        "action": req.action,
        "params": req.params,
        "request_id": req.request_id or uuid.uuid4().hex,
        **_command_source_fields(
            request,
            source_kind=req.source_kind,
            source_id=req.source_id,
        ),
    }
    resp = await app.state.router.request(payload)
    return _normalize_command_response(resp)


@app.post("/api/devices/{device_id}/connect")
async def device_connect(device_id: str) -> dict[str, Any]:
    payload = {"type": "device.connect", "device_id": device_id}
    return await _route_request(payload)


@app.post("/api/devices/{device_id}/start")
async def device_start(device_id: str) -> dict[str, Any]:
    payload = {"type": "device.driver.start", "device_id": device_id}
    return await _route_request(payload)


@app.post("/api/devices/{device_id}/disconnect")
async def device_disconnect(device_id: str) -> dict[str, Any]:
    payload = {"type": "device.disconnect", "device_id": device_id}
    return await _route_request(payload)


@app.post("/api/devices/{device_id}/restart")
async def device_restart(
    device_id: str, req: DeviceRestartRequest | None = None
) -> dict[str, Any]:
    payload = {
        "type": "device.driver.restart",
        "device_id": device_id,
        "force": bool(req.force) if req is not None else False,
    }
    return await _route_request(payload)


@app.get("/api/processes")
async def list_processes() -> dict[str, Any]:
    payload = {"type": "manager.processes.list"}
    return await _route_request(payload)


@app.post("/api/processes/{process_id}/start")
async def process_start(process_id: str, request: Request) -> dict[str, Any]:
    payload = {
        "type": "manager.processes.start",
        "process_id": process_id,
        **_command_source_fields(request),
    }
    return await _route_request(payload)


@app.post("/api/processes/{process_id}/stop")
async def process_stop(process_id: str, request: Request) -> dict[str, Any]:
    payload = {
        "type": "manager.processes.stop",
        "process_id": process_id,
        **_command_source_fields(request),
    }
    return await _route_request(payload)


@app.post("/api/processes/{process_id}/restart")
async def process_restart(process_id: str, request: Request) -> dict[str, Any]:
    payload = {
        "type": "manager.processes.restart",
        "process_id": process_id,
        **_command_source_fields(request),
    }
    return await _route_request(payload)


@app.post("/api/processes/hdf_writer/writing/stop")
async def hdf_writer_writing_stop(request: Request) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    payload = {
        "type": "manager.processes.rpc",
        "request_id": request_id,
        "process_id": "hdf_writer",
        "request": {
            "type": "hdf.writing.stop",
            "params": {},
            "request_id": request_id,
        },
        **_command_source_fields(request),
    }
    return await _route_request(payload)


@app.post("/api/processes/hdf_writer/writing/start")
async def hdf_writer_writing_start(
    request: Request,
    req: HdfWritingStartRequest | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if req is not None:
        if req.filename is not None:
            params["filename"] = req.filename
        if req.disabled_devices is not None:
            params["disabled_devices"] = list(req.disabled_devices)
        if req.measurement_profile is not None:
            params["measurement_profile"] = req.measurement_profile
        if req.measurement_values is not None:
            params["measurement_values"] = dict(req.measurement_values)
    request_id = uuid.uuid4().hex
    payload = {
        "type": "manager.processes.rpc",
        "request_id": request_id,
        "process_id": "hdf_writer",
        "request": {
            "type": "hdf.writing.start",
            "params": params,
            "request_id": request_id,
        },
        **_command_source_fields(
            request,
            source_kind=req.source_kind if req is not None else None,
            source_id=req.source_id if req is not None else None,
        ),
    }
    return await _route_request(payload)


@app.get("/api/processes/{process_id}/cached-call")
async def process_cached_call(
    process_id: str,
    action: str,
    params: str = "{}",
) -> dict[str, Any]:
    # Cap the raw query-string length before json.loads to bound parse
    # cost for hostile / oversized inputs. 4 KiB is well above any
    # plausible legitimate cached-call params payload.
    if len(params) > 4096:
        return {
            "ok": False,
            "error": {
                "code": "invalid_params",
                "message": "params query string exceeds 4096 byte cap",
            },
        }
    try:
        raw_params = json.loads(params) if str(params or "").strip() else {}
    except Exception:
        return {
            "ok": False,
            "error": {"code": "invalid_params", "message": "params must be JSON"},
        }
    if not isinstance(raw_params, dict):
        return {
            "ok": False,
            "error": {"code": "invalid_params", "message": "params must be a JSON object"},
        }
    key = _process_cached_call_key(process_id, action, raw_params)
    cache: dict[str, dict[str, Any]] = getattr(app.state, "process_cached_calls", {})
    cached = cache.get(key)
    if isinstance(cached, dict):
        return dict(cached)
    targets: list[ProcessCachedCallTarget] = getattr(
        app.state, "process_cached_call_targets", []
    )
    configured = any(
        _process_cached_call_key(target.process_id, target.action, target.params) == key
        for target in targets
    )
    if configured:
        return {
            "ok": False,
            "cached": True,
            "updated_at": None,
            "error": {"code": "cache_pending", "message": "cached call has not updated yet"},
        }
    return {
        "ok": False,
        "cached": True,
        "updated_at": None,
        "error": {"code": "cached_call_not_configured"},
    }


@app.get("/api/processes/{process_id}/capabilities")
async def process_capabilities(process_id: str) -> dict[str, Any]:
    status = await _lookup_process_status(process_id)
    if isinstance(status, dict) and not _process_rpc_registered(status):
        return {"ok": False, "error": {"code": "process_rpc_not_ready"}}
    payload = {
        "type": "manager.processes.rpc",
        "process_id": process_id,
        "request": {
            "type": "process.capabilities",
            "params": {},
            "request_id": uuid.uuid4().hex,
        },
    }
    return await _route_request(payload)


@app.post("/api/processes/{process_id}/call")
async def process_call(
    process_id: str, req: ProcessCommandRequest, request: Request
) -> dict[str, Any]:
    if str(req.action or "").strip() == "process.capabilities":
        status = await _lookup_process_status(process_id)
        if isinstance(status, dict) and not _process_rpc_registered(status):
            return {"ok": False, "error": {"code": "process_rpc_not_ready"}}
    source_fields = _command_source_fields(
        request,
        source_kind=req.source_kind,
        source_id=req.source_id,
    )
    payload = {
        "type": "manager.processes.rpc",
        "process_id": process_id,
        "request": {
            "type": req.action,
            "params": req.params,
            "request_id": req.request_id or uuid.uuid4().hex,
        },
        **source_fields,
    }
    return await _route_request(payload)


@app.get("/api/interlocks/interceptor_routes")
async def list_interceptor_routes() -> dict[str, Any]:
    payload = {"type": "manager.interceptors.list"}
    resp = await app.state.router.request(payload)
    shaped = _ensure_error_shape(resp)
    if not shaped.get("ok"):
        return shaped
    result = shaped.get("result")
    routes_raw = (
        result.get("routes")
        if isinstance(result, dict)
        else None
    )
    routes = _normalize_command_interceptor_routes(routes_raw)
    return {"ok": True, "result": {"routes": routes}}


@app.get("/api/stream/operators")
async def stream_operator_catalog() -> dict[str, Any]:
    return await _stream_analysis_rpc("stream_analysis.operators", {})


@app.get("/api/stream/workspaces")
async def list_stream_workspaces() -> dict[str, Any]:
    return await _stream_analysis_rpc("stream_analysis.workspace.list", {})


@app.get("/api/stream/workspaces/{workspace_id}")
async def get_stream_workspace(workspace_id: str) -> dict[str, Any]:
    return await _stream_analysis_rpc(
        "stream_analysis.workspace.get",
        {"workspace_id": workspace_id},
    )


@app.get("/api/stream/workspaces/{workspace_id}/snapshot")
async def get_stream_workspace_snapshot(
    workspace_id: str, request: Request
) -> dict[str, Any]:
    params: dict[str, Any] = {"workspace_id": workspace_id}
    kinds = _parse_csv_query_list(request.query_params.get("kinds"))
    if kinds is not None:
        params["kinds"] = kinds
    output_ids = _parse_csv_query_list(request.query_params.get("output_ids"))
    if output_ids is not None:
        params["output_ids"] = output_ids
    max_trace_points = _parse_trace_max_points(
        request.query_params.get("max_trace_points")
    )
    if max_trace_points is not None:
        params["max_trace_points"] = int(max_trace_points)
    return await _stream_analysis_rpc(
        "stream_analysis.workspace.snapshot",
        params,
    )


@app.put("/api/stream/workspaces/{workspace_id}")
async def put_stream_workspace(
    workspace_id: str, req: StreamWorkspaceRequest
) -> dict[str, Any]:
    workspace = dict(req.workspace)
    workspace["workspace_id"] = workspace_id
    payload: dict[str, Any] = {"workspace": workspace}
    if req.expected_revision is not None:
        payload["expected_revision"] = int(req.expected_revision)
    return await _stream_analysis_rpc(
        "stream_analysis.workspace.put",
        payload,
    )


@app.post("/api/stream/workspaces/{workspace_id}/validate")
async def validate_stream_workspace(
    workspace_id: str, req: StreamWorkspaceValidateRequest | None = None
) -> dict[str, Any]:
    if req is not None and req.workspace is not None:
        workspace = dict(req.workspace)
        workspace["workspace_id"] = workspace_id
        return await _stream_analysis_rpc(
            "stream_analysis.workspace.validate",
            {"workspace": workspace},
        )
    # Validate current active workspace config when no body is supplied.
    existing = await _stream_analysis_rpc(
        "stream_analysis.workspace.get",
        {"workspace_id": workspace_id},
    )
    if not existing.get("ok"):
        return existing
    raw = (
        existing.get("result", {}).get("raw")
        if isinstance(existing.get("result"), dict)
        else None
    )
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "error": {
                "code": "invalid_response",
                "message": "workspace.get did not include raw workspace config",
            },
        }
    raw["workspace_id"] = workspace_id
    return await _stream_analysis_rpc(
        "stream_analysis.workspace.validate",
        {"workspace": raw},
    )


@app.post("/api/stream/workspaces/{workspace_id}/activate")
async def activate_stream_workspace(workspace_id: str) -> dict[str, Any]:
    # Activation is modeled as an enabled PUT of the current (or provided) workspace config.
    existing = await _stream_analysis_rpc(
        "stream_analysis.workspace.get",
        {"workspace_id": workspace_id},
    )
    if not existing.get("ok"):
        return existing
    raw = (
        existing.get("result", {}).get("raw")
        if isinstance(existing.get("result"), dict)
        else None
    )
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "error": {
                "code": "invalid_response",
                "message": "workspace.get did not include raw workspace config",
            },
        }
    raw["workspace_id"] = workspace_id
    raw["enabled"] = True
    return await _stream_analysis_rpc(
        "stream_analysis.workspace.put",
        {"workspace": raw},
    )


@app.post("/api/stream/workspaces/{workspace_id}/reset")
async def reset_stream_workspace(
    workspace_id: str, req: StreamWorkspaceResetRequest | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"workspace_id": workspace_id}
    node_id = (req.node_id if req is not None else None) or None
    if node_id is not None:
        payload["node_id"] = str(node_id).strip()
    return await _stream_analysis_rpc("stream_analysis.workspace.reset", payload)


@app.delete("/api/stream/workspaces/{workspace_id}")
async def delete_stream_workspace(
    workspace_id: str, expected_revision: int | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"workspace_id": workspace_id}
    if expected_revision is not None:
        payload["expected_revision"] = int(expected_revision)
    return await _stream_analysis_rpc(
        "stream_analysis.workspace.delete",
        payload,
    )


@app.post("/api/stream/workspaces/clear")
async def clear_stream_workspaces() -> dict[str, Any]:
    return await _stream_analysis_rpc("stream_analysis.workspace.clear", {})


@app.get("/api/stream/workspace_store/status")
async def stream_workspace_store_status() -> dict[str, Any]:
    return await _stream_analysis_rpc("stream_analysis.workspace_store.status", {})


@app.post("/api/stream/workspace_store/save")
async def stream_workspace_store_save(
    req: StreamWorkspaceStoreRequest | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if req is not None and req.path is not None:
        payload["path"] = str(req.path)
    return await _stream_analysis_rpc("stream_analysis.workspace_store.save", payload)


@app.post("/api/stream/workspace_store/reload")
async def stream_workspace_store_reload(
    req: StreamWorkspaceStoreRequest | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if req is not None and req.path is not None:
        payload["path"] = str(req.path)
    return await _stream_analysis_rpc("stream_analysis.workspace_store.reload", payload)


@app.post("/api/logs/tail")
async def logs_tail(req: LogTailRequest | None = None) -> dict[str, Any]:
    payload = {
        "type": "manager.logs.tail",
        "params": req.params if req is not None else {},
    }
    return await _route_request(payload)


@app.get("/api/commands/journal/status")
async def command_journal_status() -> dict[str, Any]:
    payload = {"type": "manager.commands.journal.status"}
    return await _route_request(payload)


@app.post("/api/commands/journal/tail")
async def command_journal_tail(
    req: CommandJournalTailRequest | None = None,
) -> dict[str, Any]:
    payload = {
        "type": "manager.commands.journal.tail",
        "params": req.params if req is not None else {},
    }
    return await _route_request(payload)


@app.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket) -> None:
    await ws.accept()
    q = app.state.telemetry_hub.subscribe()
    try:
        while True:
            # Hub queues pre-serialized JSON strings (see TelemetryHub) so
            # all N subscribers share the same `json.dumps()` work done
            # once in the hub's reader thread.
            payload = await q.get()
            await ws.send_text(payload)
    except WebSocketDisconnect:
        pass
    finally:
        app.state.telemetry_hub.unsubscribe(q)


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket) -> None:
    await ws.accept()
    q = app.state.logs_hub.subscribe(maxsize=300)
    try:
        while True:
            payload = await q.get()
            await ws.send_text(payload)
    except WebSocketDisconnect:
        pass
    finally:
        app.state.logs_hub.unsubscribe(q)


@app.websocket("/ws/streams")
async def ws_streams(ws: WebSocket) -> None:
    await ws.accept()
    q = app.state.stream_hub.subscribe(maxsize=20)
    try:
        while True:
            msg = await q.get()
            await _ws_send_json(ws, msg)
    except WebSocketDisconnect:
        pass
    finally:
        app.state.stream_hub.unsubscribe(q)


@app.websocket("/ws/raw_stream")
async def ws_raw_stream(ws: WebSocket) -> None:
    device_id = str(ws.query_params.get("device_id") or "").strip()
    stream = str(ws.query_params.get("stream") or "").strip()
    if not device_id or not stream:
        await ws.close(code=1008)
        return
    channel_index = _parse_channel_index(ws.query_params.get("channel_index"))
    trace_decimator = _parse_trace_decimator(ws.query_params.get("trace_decimator"))
    trace_max_points = _parse_trace_max_points(ws.query_params.get("trace_max_points"))
    trace_max_fps = _parse_trace_max_fps(ws.query_params.get("trace_max_fps"))
    rolling_window = _parse_trace_rolling_window(ws.query_params.get("rolling_window"))
    trace_average_mode = _parse_trace_average_mode(ws.query_params.get("trace_average_mode"))
    trace_interval_s = (1.0 / trace_max_fps) if trace_max_fps else 0.0
    next_send_at = 0.0
    pending_msg: dict[str, Any] | None = None
    rolling_buf: deque[np.ndarray] = deque()
    rolling_sum: np.ndarray | None = None
    block_sum: np.ndarray | None = None
    block_count = 0

    def _apply_trace_average(trace: np.ndarray) -> np.ndarray | None:
        nonlocal rolling_sum, block_sum, block_count
        if rolling_window <= 1 or trace.size <= 0:
            return trace
        if trace_average_mode == "rolling":
            if rolling_sum is None or int(rolling_sum.size) != int(trace.size):
                rolling_buf.clear()
                rolling_sum = np.zeros(int(trace.size), dtype=np.float64)
            incoming = trace.astype(np.float64, copy=True)
            if len(rolling_buf) >= int(rolling_window):
                oldest = rolling_buf.popleft()
                rolling_sum -= oldest
            rolling_buf.append(incoming)
            rolling_sum += incoming
            return rolling_sum / float(max(1, len(rolling_buf)))
        if block_sum is None or int(block_sum.size) != int(trace.size):
            block_sum = np.zeros(int(trace.size), dtype=np.float64)
            block_count = 0
        block_sum += trace.astype(np.float64, copy=False)
        block_count += 1
        if block_count < int(rolling_window):
            return None
        out = block_sum / float(block_count)
        block_sum.fill(0.0)
        block_count = 0
        return out

    async def _send_or_queue(msg: dict[str, Any]) -> None:
        nonlocal next_send_at, pending_msg
        if trace_interval_s <= 0:
            await _ws_send_json(ws, msg)
            return
        now = time.monotonic()
        if now >= next_send_at:
            await _ws_send_json(ws, msg)
            next_send_at = now + trace_interval_s
            pending_msg = None
            return
        pending_msg = msg

    await ws.accept()
    # Keep this queue small: each entry can contain large trace payloads.
    q = app.state.stream_hub.subscribe(
        maxsize=8,
        device_id=device_id,
        stream=stream,
    )
    try:
        while True:
            timeout_s = 0.05 if pending_msg is not None and trace_interval_s > 0 else None
            try:
                if timeout_s is None:
                    msg = await q.get()
                else:
                    msg = await asyncio.wait_for(q.get(), timeout=timeout_s)
            except asyncio.TimeoutError:
                if pending_msg is None:
                    continue
                now = time.monotonic()
                if now < next_send_at:
                    continue
                await _ws_send_json(ws, pending_msg)
                pending_msg = None
                next_send_at = now + trace_interval_s
                continue
            if not isinstance(msg, dict):
                continue
            if str(msg.get("topic") or "").strip() != "manager.stream_frame":
                continue
            payload = msg.get("payload")
            if not isinstance(payload, dict):
                continue
            msg_device_id = str(payload.get("device_id") or "").strip()
            msg_stream = str(payload.get("stream") or "").strip()
            if msg_device_id != device_id or msg_stream != stream:
                continue
            out_payload = _build_trace_frame_payload(
                payload,
                channel_index=channel_index,
                trace_decimator=trace_decimator,
                trace_max_points=trace_max_points,
                pre_decimate=_apply_trace_average,
            )
            if out_payload is None:
                continue
            await _send_or_queue({"topic": "manager.stream_frame", "payload": out_payload})
    except WebSocketDisconnect:
        pass
    finally:
        app.state.stream_hub.unsubscribe(q)


@app.get("/api/streams/raw_snapshot")
async def raw_stream_snapshot(request: Request) -> dict[str, Any]:
    device_id = str(request.query_params.get("device_id") or "").strip()
    stream = str(request.query_params.get("stream") or "").strip()
    if not device_id or not stream:
        return {
            "ok": False,
            "error": {
                "code": "invalid_params",
                "message": "device_id and stream are required",
            },
        }

    channel_index = _parse_channel_index(request.query_params.get("channel_index"))
    trace_decimator = _parse_trace_decimator(request.query_params.get("trace_decimator"))
    trace_max_points = _parse_trace_max_points(request.query_params.get("trace_max_points"))

    frame_msg = app.state.stream_hub.get_latest_frame(
        device_id=device_id,
        stream=stream,
    )
    if not isinstance(frame_msg, dict):
        return {"ok": True, "result": None}
    payload = frame_msg.get("payload")
    if not isinstance(payload, dict):
        return {"ok": True, "result": None}
    out_payload = _build_trace_frame_payload(
        payload,
        channel_index=channel_index,
        trace_decimator=trace_decimator,
        trace_max_points=trace_max_points,
    )
    if out_payload is None:
        return {"ok": True, "result": None}
    return {
        "ok": True,
        "result": {
            "topic": str(frame_msg.get("topic") or "manager.stream_frame"),
            "payload": out_payload,
        },
    }


_WORKSPACE_STREAM_ALLOWED_KINDS = {
    "scalar",
    "hist_agg",
    "hist2d",
    "trace",
    "params_map",
    "fit_1d",
}
_WORKSPACE_STREAM_ALLOWED_TOPICS = {
    "manager.stream_analysis.output",
    "manager.stream_analysis.trace_ready",
    "manager.stream_analysis.workspace_status",
    "manager.stream_analysis.error",
}


class _WorkspaceTraceWsState:
    def __init__(
        self,
        *,
        trace_decimator: str,
        trace_max_points: int | None,
        trace_interval_s: float,
        rolling_window: int,
        trace_average_mode: str,
    ) -> None:
        self.trace_decimator = trace_decimator
        self.trace_max_points = trace_max_points
        self.trace_interval_s = trace_interval_s
        self.rolling_window = rolling_window
        self.trace_average_mode = trace_average_mode
        self.next_trace_send_at: dict[str, float] = {}
        self.pending_trace_msgs: dict[str, dict[str, Any]] = {}
        self.trace_readers: dict[tuple[str, str], ShmRingReader] = {}
        self.rolling_buffers: dict[str, deque[np.ndarray]] = {}
        self.rolling_sums: dict[str, np.ndarray] = {}
        self.block_sums: dict[str, np.ndarray] = {}
        self.block_counts: dict[str, int] = {}


def _parse_workspace_allowed_output_kinds(raw: Any) -> set[str] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    parsed = {
        part.strip()
        for part in text.split(",")
        if part.strip() in _WORKSPACE_STREAM_ALLOWED_KINDS
    }
    return parsed or None


def _workspace_trace_key(workspace_id: str, output_id: str) -> str:
    return f"{workspace_id}:{output_id}"


def _workspace_close_trace_reader(
    state: _WorkspaceTraceWsState,
    key: tuple[str, str],
) -> None:
    reader = state.trace_readers.pop(key, None)
    if reader is None:
        return
    try:
        reader.close()
    except Exception:
        pass


def _workspace_apply_trace_average(
    state: _WorkspaceTraceWsState,
    *,
    output_key: str,
    trace: np.ndarray,
) -> np.ndarray | None:
    if state.rolling_window <= 1:
        return trace
    if trace.size <= 0:
        return trace
    if state.trace_average_mode == "rolling":
        buf = state.rolling_buffers.get(output_key)
        sum_trace = state.rolling_sums.get(output_key)
        if buf is None or sum_trace is None or int(sum_trace.size) != int(trace.size):
            buf = deque()
            sum_trace = np.zeros(int(trace.size), dtype=np.float64)
            state.rolling_buffers[output_key] = buf
            state.rolling_sums[output_key] = sum_trace
        incoming = trace.astype(np.float64, copy=True)
        if len(buf) >= int(state.rolling_window):
            oldest = buf.popleft()
            sum_trace -= oldest
        buf.append(incoming)
        sum_trace += incoming
        return sum_trace / float(max(1, len(buf)))
    sum_trace = state.block_sums.get(output_key)
    count = int(state.block_counts.get(output_key, 0))
    if sum_trace is None or int(sum_trace.size) != int(trace.size):
        sum_trace = np.zeros(int(trace.size), dtype=np.float64)
        count = 0
        state.block_sums[output_key] = sum_trace
    sum_trace += trace.astype(np.float64, copy=False)
    count += 1
    state.block_counts[output_key] = count
    if count < int(state.rolling_window):
        return None
    out = sum_trace / float(count)
    sum_trace.fill(0.0)
    state.block_counts[output_key] = 0
    return out


async def _workspace_send_trace_message(
    *,
    ws: WebSocket,
    state: _WorkspaceTraceWsState,
    msg_workspace: str,
    trace_payload: dict[str, Any],
    trace_msg: dict[str, Any],
) -> None:
    if state.trace_interval_s > 0:
        output_id = str(trace_payload.get("output_id") or "").strip()
        trace_key = _workspace_trace_key(msg_workspace, output_id)
        now = time.monotonic()
        next_at = state.next_trace_send_at.get(trace_key, 0.0)
        if now >= next_at:
            await _ws_send_json(ws, trace_msg)
            state.next_trace_send_at[trace_key] = now + state.trace_interval_s
        else:
            state.pending_trace_msgs[trace_key] = trace_msg
            if trace_key not in state.next_trace_send_at:
                state.next_trace_send_at[trace_key] = next_at
        return
    await _ws_send_json(ws, trace_msg)


async def _workspace_flush_pending_trace_messages(
    *,
    ws: WebSocket,
    state: _WorkspaceTraceWsState,
) -> None:
    if not state.pending_trace_msgs:
        return
    now = time.monotonic()
    due = [key for key, at in state.next_trace_send_at.items() if now >= at]
    for key in due:
        pending = state.pending_trace_msgs.pop(key, None)
        if pending is None:
            continue
        await _ws_send_json(ws, pending)
        if state.trace_interval_s > 0:
            state.next_trace_send_at[key] = now + state.trace_interval_s
        else:
            state.next_trace_send_at.pop(key, None)


def _workspace_filter_stream_message(
    *,
    msg: Any,
    workspace: str,
    allowed_output_kinds: set[str] | None,
) -> tuple[str, dict[str, Any], str] | None:
    if not isinstance(msg, dict):
        return None
    topic = str(msg.get("topic") or "").strip()
    payload = msg.get("payload")
    if not isinstance(payload, dict):
        return None
    msg_workspace = str(payload.get("workspace_id") or "").strip()
    if msg_workspace != workspace:
        return None
    if topic not in _WORKSPACE_STREAM_ALLOWED_TOPICS:
        return None
    if topic in {"manager.stream_analysis.output", "manager.stream_analysis.trace_ready"}:
        if allowed_output_kinds is not None:
            kind = str(payload.get("kind") or "").strip()
            if kind not in allowed_output_kinds:
                return None
    return topic, payload, msg_workspace


async def _workspace_handle_trace_ready_message(
    *,
    ws: WebSocket,
    state: _WorkspaceTraceWsState,
    payload: dict[str, Any],
    msg_workspace: str,
) -> bool:
    output_id = str(payload.get("output_id") or "").strip()
    node_id = str(payload.get("node_id") or "").strip()
    shm_name = str(payload.get("shm_name") or "").strip()
    seq_raw = payload.get("seq")
    if not output_id or not node_id or not shm_name:
        return False
    try:
        trace_seq = int(seq_raw)
    except Exception:
        return False
    reader_key = (msg_workspace, output_id)
    reader = state.trace_readers.get(reader_key)
    if reader is None or reader.name != shm_name:
        _workspace_close_trace_reader(state, reader_key)
        try:
            reader = ShmRingReader.attach(shm_name)
        except Exception:
            return False
        state.trace_readers[reader_key] = reader
    event = reader.read_event(trace_seq)
    if not isinstance(event, dict):
        return False
    payload_bytes = event.get("payload")
    if not isinstance(payload_bytes, (bytes, bytearray, memoryview)):
        return False
    try:
        trace_arr = np.frombuffer(payload_bytes, dtype=reader.layout.dtype).reshape(
            tuple(int(v) for v in reader.layout.shape)
        )
    except Exception:
        return False
    trace_key = _workspace_trace_key(msg_workspace, output_id)
    trace_flat = _workspace_apply_trace_average(
        state,
        output_key=trace_key,
        trace=trace_arr.reshape(-1),
    )
    if trace_flat is None:
        return True
    if state.trace_max_points is not None:
        trace_values = _decimate_trace_values(
            trace_flat,
            mode=state.trace_decimator,
            max_points=state.trace_max_points,
        )
    else:
        trace_values = trace_flat.tolist()
    trace_payload: dict[str, Any] = {
        "version": 1,
        "workspace_id": msg_workspace,
        "output_id": output_id,
        "node_id": node_id,
        "kind": "trace",
        "device_id": payload.get("device_id"),
        "stream": payload.get("stream"),
        "seq": int(event.get("seq") or trace_seq),
        "t0_mono_ns": event.get("t0_mono_ns"),
        "t0_wall_ns": event.get("t0_wall_ns"),
        "channel_index": payload.get("channel_index"),
        "channel_count": payload.get("channel_count"),
        "value": trace_values,
        "point_count": len(trace_values) if isinstance(trace_values, list) else 0,
    }
    if bool(payload.get("truncated")):
        trace_payload["truncated"] = True
    if payload.get("context_id") is not None:
        trace_payload["context_id"] = payload.get("context_id")
    if isinstance(payload.get("context_fields"), dict):
        trace_payload["context_fields"] = payload.get("context_fields")
    await _workspace_send_trace_message(
        ws=ws,
        state=state,
        msg_workspace=msg_workspace,
        trace_payload=trace_payload,
        trace_msg={"topic": "manager.stream_analysis.output", "payload": trace_payload},
    )
    return True


async def _workspace_handle_trace_output_message(
    *,
    ws: WebSocket,
    state: _WorkspaceTraceWsState,
    msg: dict[str, Any],
    payload: dict[str, Any],
    msg_workspace: str,
) -> bool:
    if str(payload.get("kind") or "").strip() != "trace":
        return False
    trace_payload = payload
    points = _coerce_trace_array(payload.get("value"))
    output_id = str(payload.get("output_id") or "").strip()
    if points is not None and output_id:
        trace_key = _workspace_trace_key(msg_workspace, output_id)
        rolled = _workspace_apply_trace_average(state, output_key=trace_key, trace=points)
        if rolled is None:
            return True
        decimated = (
            _decimate_trace_values(
                rolled,
                mode=state.trace_decimator,
                max_points=state.trace_max_points,
            )
            if state.trace_max_points is not None
            else rolled.tolist()
        )
        trace_payload = dict(payload)
        trace_payload["value"] = decimated
        trace_payload["point_count"] = len(decimated) if isinstance(decimated, list) else 0
        if state.trace_max_points is not None and len(decimated) < int(points.size):
            trace_payload["decimated"] = True
    trace_msg = msg if trace_payload is payload else {**msg, "payload": trace_payload}
    await _workspace_send_trace_message(
        ws=ws,
        state=state,
        msg_workspace=msg_workspace,
        trace_payload=trace_payload,
        trace_msg=trace_msg,
    )
    return True


@app.websocket("/ws/stream/{workspace_id}")
async def ws_stream_workspace(ws: WebSocket, workspace_id: str) -> None:
    workspace = str(workspace_id).strip()
    if not workspace:
        await ws.close(code=1008)
        return
    allowed_output_kinds = _parse_workspace_allowed_output_kinds(
        ws.query_params.get("kinds")
    )
    trace_decimator = _parse_trace_decimator(ws.query_params.get("trace_decimator"))
    trace_max_points = _parse_trace_max_points(ws.query_params.get("trace_max_points"))
    trace_max_fps = _parse_trace_max_fps(ws.query_params.get("trace_max_fps"))
    rolling_window = _parse_trace_rolling_window(ws.query_params.get("rolling_window"))
    trace_average_mode = _parse_trace_average_mode(ws.query_params.get("trace_average_mode"))
    trace_interval_s = (1.0 / trace_max_fps) if trace_max_fps else 0.0
    state = _WorkspaceTraceWsState(
        trace_decimator=trace_decimator,
        trace_max_points=trace_max_points,
        trace_interval_s=trace_interval_s,
        rolling_window=rolling_window,
        trace_average_mode=trace_average_mode,
    )

    await ws.accept()
    q = app.state.stream_analysis_hub.subscribe(maxsize=150)
    try:
        while True:
            timeout_s = 0.05 if state.pending_trace_msgs else None
            try:
                if timeout_s is None:
                    msg = await q.get()
                else:
                    msg = await asyncio.wait_for(q.get(), timeout=timeout_s)
            except asyncio.TimeoutError:
                await _workspace_flush_pending_trace_messages(ws=ws, state=state)
                continue
            filtered = _workspace_filter_stream_message(
                msg=msg,
                workspace=workspace,
                allowed_output_kinds=allowed_output_kinds,
            )
            if filtered is None:
                continue
            topic, payload, msg_workspace = filtered

            if topic == "manager.stream_analysis.trace_ready":
                handled = await _workspace_handle_trace_ready_message(
                    ws=ws,
                    state=state,
                    payload=payload,
                    msg_workspace=msg_workspace,
                )
                if handled:
                    continue

            if topic == "manager.stream_analysis.output":
                handled = await _workspace_handle_trace_output_message(
                    ws=ws,
                    state=state,
                    msg=msg,
                    payload=payload,
                    msg_workspace=msg_workspace,
                )
                if handled:
                    continue

            await _ws_send_json(ws, msg)
    except WebSocketDisconnect:
        pass
    finally:
        for key in list(state.trace_readers.keys()):
            _workspace_close_trace_reader(state, key)
        app.state.stream_analysis_hub.unsubscribe(q)


if _EXTRA_UI_SPECS:

    @app.get("/instance-ui/{slug}", include_in_schema=False)
    async def extra_ui_index_redirect(slug: str) -> RedirectResponse:
        spec = _EXTRA_UI_BY_SLUG.get(slug)
        if spec is None:
            raise HTTPException(status_code=404, detail="not found")
        return RedirectResponse(url=spec.href)

    @app.get("/instance-ui/{slug}/{full_path:path}", include_in_schema=False)
    async def extra_ui_spa_fallback(slug: str, full_path: str) -> FileResponse:
        spec = _EXTRA_UI_BY_SLUG.get(slug)
        if spec is None:
            raise HTTPException(status_code=404, detail="not found")
        norm = full_path.strip("/")
        if not norm:
            return FileResponse(spec.dist / "index.html")
        candidate = (spec.dist / norm).resolve()
        try:
            candidate.relative_to(spec.dist)
        except Exception as e:
            raise HTTPException(status_code=404, detail="not found") from e
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(spec.dist / "index.html")


if _UI_DIST_PATH is not None:

    @app.get("/", include_in_schema=False)
    async def ui_index() -> FileResponse:
        return FileResponse(_UI_DIST_PATH / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def ui_spa_fallback(full_path: str) -> FileResponse:
        norm = full_path.strip("/")
        if norm in {"api", "ws"} or norm.startswith("api/") or norm.startswith("ws/"):
            raise HTTPException(status_code=404, detail="not found")
        if not norm:
            return FileResponse(_UI_DIST_PATH / "index.html")

        candidate = (_UI_DIST_PATH / norm).resolve()
        try:
            candidate.relative_to(_UI_DIST_PATH)
        except Exception as e:
            raise HTTPException(status_code=404, detail="not found") from e
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_UI_DIST_PATH / "index.html")

