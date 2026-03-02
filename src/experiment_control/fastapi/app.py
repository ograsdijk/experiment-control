from __future__ import annotations

import asyncio
from collections import deque
import ipaddress
import math
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .gateway import GatewaySettings, RouterRpcClient, StreamFrameHub, TelemetryHub
from ..shm.shm_ring import ShmRingReader


class DeviceCommandRequest(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class DeviceRestartRequest(BaseModel):
    force: bool = False


class ProcessCommandRequest(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class LogTailRequest(BaseModel):
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
    payload = {"type": "manager.identity", "request_id": request_id}
    try:
        resp = await asyncio.to_thread(router.request, payload)
    except Exception:
        return None
    shaped = _ensure_error_shape(resp)
    if not shaped.get("ok"):
        return None
    result = shaped.get("result")
    if not isinstance(result, dict):
        return None
    return result


async def _process_rpc(
    process_id: str, action: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    payload = {
        "type": "process.rpc",
        "request_id": request_id,
        "process_id": process_id,
        "request": {
            "type": str(action),
            "params": dict(params or {}),
            "request_id": request_id,
        },
    }
    resp = await asyncio.to_thread(app.state.router.request, payload)
    shaped = _ensure_error_shape(resp)
    if shaped.get("ok") is True:
        got = shaped.get("request_id")
        if isinstance(got, str) and got and got != request_id:
            return {
                "ok": False,
                "error": {
                    "code": "rpc_request_id_mismatch",
                    "message": f"process.rpc request_id mismatch: expected {request_id}, got {got}",
                },
            }
    return shaped


async def _stream_analysis_rpc(
    action: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    return await _process_rpc(STREAM_ANALYSIS_PROCESS_ID, action, params)


def _is_loopback_host(raw_host: str | None) -> bool:
    if not raw_host:
        return False
    host = str(raw_host).strip().lower().strip("[]")
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return bool(ipaddress.ip_address(host).is_loopback)
    except Exception:
        return False


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
    candidates: set[str] = set()
    try:
        _host, _aliases, ips = socket.gethostbyname_ex(socket.gethostname())
        for ip in ips:
            try:
                parsed = ipaddress.ip_address(ip)
            except Exception:
                continue
            if parsed.version == 4 and not parsed.is_loopback:
                candidates.add(str(parsed))
    except Exception:
        pass
    try:
        infos = socket.getaddrinfo(
            socket.gethostname(),
            None,
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        )
        for info in infos:
            addr = info[4][0]
            try:
                parsed = ipaddress.ip_address(addr)
            except Exception:
                continue
            if parsed.version == 4 and not parsed.is_loopback:
                candidates.add(str(parsed))
    except Exception:
        pass
    return sorted(candidates)


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


def _parse_trace_decimator(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"stride", "mean", "m4"}:
        return value
    return "minmax"


def _parse_trace_max_points(raw: Any) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = int(float(text))
    except Exception:
        return None
    return max(32, min(20000, int(value)))


def _parse_trace_max_fps(raw: Any) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except Exception:
        return None
    if not math.isfinite(value):
        return None
    return max(0.5, min(120.0, float(value)))


def _parse_trace_rolling_window(raw: Any) -> int:
    text = str(raw or "").strip()
    if not text:
        return 1
    try:
        value = int(float(text))
    except Exception:
        return 1
    return max(1, min(200, int(value)))


def _parse_trace_average_mode(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value == "rolling":
        return "rolling"
    return "block"


def _parse_channel_index(raw: Any) -> int:
    text = str(raw or "").strip()
    if not text:
        return 0
    try:
        value = int(float(text))
    except Exception:
        return 0
    return max(0, int(value))


def _normalize_shape(raw: Any) -> list[int]:
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for value in raw:
        try:
            parsed = int(value)
        except Exception:
            continue
        if parsed <= 0:
            continue
        out.append(parsed)
    return out


def _coerce_stream_values_array(values: Any, shape: list[int]) -> np.ndarray | None:
    if isinstance(values, np.ndarray):
        arr = values
    elif isinstance(values, list):
        try:
            arr = np.asarray(values, dtype=np.float64)
        except Exception:
            return None
    else:
        return None
    if arr.ndim == 0:
        arr = arr.reshape(1)
    if shape:
        expected = 1
        for dim in shape:
            expected *= int(dim)
        if expected > 0 and int(arr.size) == int(expected):
            try:
                arr = arr.reshape(tuple(shape))
            except Exception:
                pass
    try:
        arr = arr.astype(np.float64, copy=False)
    except Exception:
        return None
    if arr.size > 0 and not np.isfinite(arr).all():
        return None
    return arr


def _select_trace_from_array(array: np.ndarray, channel_index: int) -> np.ndarray:
    arr = np.asarray(array)
    if arr.ndim == 0:
        return arr.reshape(1).astype(np.float64, copy=False)
    if arr.ndim == 1:
        return arr.astype(np.float64, copy=False)
    if arr.ndim == 2:
        rows, cols = int(arr.shape[0]), int(arr.shape[1])
        if rows <= 1 or cols <= 1:
            return arr.reshape(-1).astype(np.float64, copy=False)
        if rows <= cols:
            idx = max(0, min(int(channel_index), rows - 1))
            return arr[idx, :].astype(np.float64, copy=False)
        idx = max(0, min(int(channel_index), cols - 1))
        return arr[:, idx].astype(np.float64, copy=False)
    return arr.reshape(-1).astype(np.float64, copy=False)


def _coerce_trace_array(raw: Any) -> np.ndarray | None:
    if isinstance(raw, np.ndarray):
        arr = raw.reshape(-1)
    elif isinstance(raw, list):
        try:
            arr = np.asarray(raw, dtype=np.float64).reshape(-1)
        except Exception:
            return None
    else:
        return None
    if arr.size <= 0:
        return np.asarray([], dtype=np.float64)
    if not np.isfinite(arr).all():
        return None
    return arr.astype(np.float64, copy=False)


def _bucket_ranges(n: int, bucket_count: int) -> list[tuple[int, int]]:
    if n <= 0 or bucket_count <= 0:
        return []
    out: list[tuple[int, int]] = []
    for i in range(bucket_count):
        start = (i * n) // bucket_count
        stop = ((i + 1) * n) // bucket_count
        if stop <= start:
            stop = min(n, start + 1)
        out.append((start, stop))
    return out


def _decimate_trace_values(values: Any, *, mode: str, max_points: int) -> Any:
    points = _coerce_trace_array(values)
    if points is None:
        return values
    n = int(points.size)
    if max_points <= 0 or n <= max_points:
        return points.tolist()

    decimator = _parse_trace_decimator(mode)

    if decimator == "stride":
        step = max(1, int(math.ceil(float(n) / float(max_points))))
        out = points[::step]
        if out.size > 0 and float(out[-1]) != float(points[-1]):
            out = np.concatenate([out, points[-1:]])
        return out[:max_points].tolist()

    if decimator == "mean":
        bucket_count = max(1, min(max_points, n))
        out: list[float] = []
        for start, stop in _bucket_ranges(n, bucket_count):
            chunk = points[start:stop]
            if chunk.size <= 0:
                continue
            out.append(float(np.mean(chunk, dtype=np.float64)))
        return out[:max_points]

    if decimator == "m4":
        bucket_count = max(1, min(max_points // 4, n))
        out: list[float] = []
        for start, stop in _bucket_ranges(n, bucket_count):
            if stop <= start:
                continue
            first_i = start
            last_i = stop - 1
            min_i = start
            max_i = start
            min_v = float(points[start])
            max_v = float(points[start])
            for idx in range(start + 1, stop):
                value = float(points[idx])
                if value < min_v:
                    min_v = value
                    min_i = idx
                if value > max_v:
                    max_v = value
                    max_i = idx
            for idx in sorted({first_i, min_i, max_i, last_i}):
                out.append(float(points[idx]))
                if len(out) >= max_points:
                    return out[:max_points]
        return out[:max_points]

    # default: minmax
    bucket_count = max(1, min(max_points // 2, n))
    out: list[float] = []
    for start, stop in _bucket_ranges(n, bucket_count):
        if stop <= start:
            continue
        min_i = start
        max_i = start
        min_v = float(points[start])
        max_v = float(points[start])
        for idx in range(start + 1, stop):
            value = float(points[idx])
            if value < min_v:
                min_v = value
                min_i = idx
            if value > max_v:
                max_v = value
                max_i = idx
        if min_i <= max_i:
            out.append(float(points[min_i]))
            if max_i != min_i and len(out) < max_points:
                out.append(float(points[max_i]))
        else:
            out.append(float(points[max_i]))
            if max_i != min_i and len(out) < max_points:
                out.append(float(points[min_i]))
        if len(out) >= max_points:
            return out[:max_points]
    return out[:max_points]


app = FastAPI(title="Experiment Control Gateway", version="0.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_UI_DIST_PATH: Path | None = _resolve_ui_dist_path()


@app.on_event("startup")
async def _startup() -> None:
    settings = _load_settings()
    router = RouterRpcClient(settings.router_rpc, timeout_ms=settings.rpc_timeout_ms)
    router.start()
    manager_identity = await _fetch_manager_identity(router)
    telemetry_hub = TelemetryHub(settings.manager_pub, topics=settings.telemetry_topics)
    telemetry_hub.start(asyncio.get_running_loop())
    logs_hub = TelemetryHub(settings.manager_pub, topics=settings.log_topics)
    logs_hub.start(asyncio.get_running_loop())
    stream_hub = StreamFrameHub(settings.manager_pub, topics=settings.stream_topics)
    stream_hub.start(asyncio.get_running_loop())
    stream_analysis_hub = TelemetryHub(
        settings.manager_pub, topics=settings.stream_analysis_topics
    )
    stream_analysis_hub.start(asyncio.get_running_loop())
    app.state.settings = settings
    app.state.manager_identity = manager_identity
    app.state.router = router
    app.state.telemetry_hub = telemetry_hub
    app.state.logs_hub = logs_hub
    app.state.stream_hub = stream_hub
    app.state.stream_analysis_hub = stream_analysis_hub


@app.on_event("shutdown")
async def _shutdown() -> None:
    router: RouterRpcClient | None = getattr(app.state, "router", None)
    telemetry_hub: TelemetryHub | None = getattr(app.state, "telemetry_hub", None)
    logs_hub: TelemetryHub | None = getattr(app.state, "logs_hub", None)
    stream_hub: StreamFrameHub | None = getattr(app.state, "stream_hub", None)
    stream_analysis_hub: TelemetryHub | None = getattr(
        app.state, "stream_analysis_hub", None
    )
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

    return {
        "ok": True,
        "result": {
            "router_rpc": settings.router_rpc,
            "manager_pub": settings.manager_pub,
            "instance_id": instance_id,
            "router_rpc_hint": router_rpc_hint,
            "manager_pub_hint": manager_pub_hint,
            "rpc_timeout_ms": settings.rpc_timeout_ms,
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


@app.get("/api/devices")
async def list_devices() -> dict[str, Any]:
    payload = {"type": "device.list_status"}
    resp = await asyncio.to_thread(app.state.router.request, payload)
    return _ensure_error_shape(resp)


@app.get("/api/streams")
async def list_streams() -> dict[str, Any]:
    payload = {"type": "device.config.list"}
    resp = await asyncio.to_thread(app.state.router.request, payload)
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
                out.append(
                    {
                        "device_id": device_id,
                        "stream": stream,
                        "dtype": str(output.get("dtype") or ""),
                        "shape": shape,
                        "units": output.get("units"),
                        "description": output.get("description"),
                        "attrs": output.get("attrs")
                        if isinstance(output.get("attrs"), dict)
                        else {},
                    }
                )

    out.sort(key=lambda item: (str(item.get("device_id")), str(item.get("stream"))))
    return {"ok": True, "result": out}


@app.get("/api/devices/{device_id}/capabilities")
async def device_capabilities(device_id: str) -> dict[str, Any]:
    payload = {
        "type": "command",
        "device_id": device_id,
        "action": "capabilities",
        "params": {},
        "request_id": uuid.uuid4().hex,
    }
    resp = await asyncio.to_thread(app.state.router.request, payload)
    return _normalize_command_response(resp)


@app.post("/api/devices/{device_id}/call")
async def device_call(device_id: str, req: DeviceCommandRequest) -> dict[str, Any]:
    payload = {
        "type": "command",
        "device_id": device_id,
        "action": req.action,
        "params": req.params,
        "request_id": req.request_id or uuid.uuid4().hex,
    }
    resp = await asyncio.to_thread(app.state.router.request, payload)
    return _normalize_command_response(resp)


@app.post("/api/devices/{device_id}/connect")
async def device_connect(device_id: str) -> dict[str, Any]:
    payload = {"type": "device.connect", "device_id": device_id}
    resp = await asyncio.to_thread(app.state.router.request, payload)
    return _ensure_error_shape(resp)


@app.post("/api/devices/{device_id}/start")
async def device_start(device_id: str) -> dict[str, Any]:
    payload = {"type": "device.driver.start", "device_id": device_id}
    resp = await asyncio.to_thread(app.state.router.request, payload)
    return _ensure_error_shape(resp)


@app.post("/api/devices/{device_id}/disconnect")
async def device_disconnect(device_id: str) -> dict[str, Any]:
    payload = {"type": "device.disconnect", "device_id": device_id}
    resp = await asyncio.to_thread(app.state.router.request, payload)
    return _ensure_error_shape(resp)


@app.post("/api/devices/{device_id}/restart")
async def device_restart(
    device_id: str, req: DeviceRestartRequest | None = None
) -> dict[str, Any]:
    payload = {
        "type": "device.driver.restart",
        "device_id": device_id,
        "force": bool(req.force) if req is not None else False,
    }
    resp = await asyncio.to_thread(app.state.router.request, payload)
    return _ensure_error_shape(resp)


@app.get("/api/processes")
async def list_processes() -> dict[str, Any]:
    payload = {"type": "process.list_status"}
    resp = await asyncio.to_thread(app.state.router.request, payload)
    return _ensure_error_shape(resp)


@app.post("/api/processes/{process_id}/start")
async def process_start(process_id: str) -> dict[str, Any]:
    payload = {"type": "process.start", "process_id": process_id}
    resp = await asyncio.to_thread(app.state.router.request, payload)
    return _ensure_error_shape(resp)


@app.post("/api/processes/{process_id}/stop")
async def process_stop(process_id: str) -> dict[str, Any]:
    payload = {"type": "process.stop", "process_id": process_id}
    resp = await asyncio.to_thread(app.state.router.request, payload)
    return _ensure_error_shape(resp)


@app.post("/api/processes/{process_id}/restart")
async def process_restart(process_id: str) -> dict[str, Any]:
    payload = {"type": "process.restart", "process_id": process_id}
    resp = await asyncio.to_thread(app.state.router.request, payload)
    return _ensure_error_shape(resp)


@app.get("/api/processes/{process_id}/capabilities")
async def process_capabilities(process_id: str) -> dict[str, Any]:
    payload = {
        "type": "process.rpc",
        "process_id": process_id,
        "request": {
            "type": "process.capabilities",
            "params": {},
            "request_id": uuid.uuid4().hex,
        },
    }
    resp = await asyncio.to_thread(app.state.router.request, payload)
    return _ensure_error_shape(resp)


@app.post("/api/processes/{process_id}/call")
async def process_call(
    process_id: str, req: ProcessCommandRequest
) -> dict[str, Any]:
    payload = {
        "type": "process.rpc",
        "process_id": process_id,
        "request": {
            "type": req.action,
            "params": req.params,
            "request_id": req.request_id or uuid.uuid4().hex,
        },
    }
    resp = await asyncio.to_thread(app.state.router.request, payload)
    return _ensure_error_shape(resp)


@app.get("/api/interlocks/interceptor_routes")
async def list_interceptor_routes() -> dict[str, Any]:
    payload = {"type": "command_interceptor.list"}
    resp = await asyncio.to_thread(app.state.router.request, payload)
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


@app.patch("/api/stream/workspaces/{workspace_id}")
async def patch_stream_workspace(
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
        "type": "manager.log.tail",
        "params": req.params if req is not None else {},
    }
    resp = await asyncio.to_thread(app.state.router.request, payload)
    return _ensure_error_shape(resp)


@app.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket) -> None:
    await ws.accept()
    q = app.state.telemetry_hub.subscribe()
    try:
        while True:
            msg = await q.get()
            await ws.send_json(msg)
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
            msg = await q.get()
            await ws.send_json(msg)
    except WebSocketDisconnect:
        pass
    finally:
        app.state.logs_hub.unsubscribe(q)


@app.websocket("/ws/streams")
async def ws_streams(ws: WebSocket) -> None:
    await ws.accept()
    q = app.state.stream_hub.subscribe(maxsize=80)
    try:
        while True:
            msg = await q.get()
            await ws.send_json(msg)
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
            await ws.send_json(msg)
            return
        now = time.monotonic()
        if now >= next_send_at:
            await ws.send_json(msg)
            next_send_at = now + trace_interval_s
            pending_msg = None
            return
        pending_msg = msg

    await ws.accept()
    q = app.state.stream_hub.subscribe(maxsize=120)
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
                await ws.send_json(pending_msg)
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
            shape = _normalize_shape(payload.get("shape"))
            arr = _coerce_stream_values_array(payload.get("values"), shape)
            if arr is None:
                continue
            trace = _select_trace_from_array(arr, channel_index)
            trace = _apply_trace_average(trace)
            if trace is None:
                continue
            if trace_max_points is not None:
                trace_values = _decimate_trace_values(
                    trace,
                    mode=trace_decimator,
                    max_points=trace_max_points,
                )
            else:
                trace_values = trace.tolist()
            if not isinstance(trace_values, list):
                continue
            out_payload: dict[str, Any] = dict(payload)
            out_payload["shape"] = [len(trace_values)]
            out_payload["values"] = trace_values
            out_payload["channel_index"] = int(channel_index)
            out_payload["point_count"] = len(trace_values)
            if trace_max_points is not None and len(trace_values) < int(trace.size):
                out_payload["decimated"] = True
            await _send_or_queue({"topic": "manager.stream_frame", "payload": out_payload})
    except WebSocketDisconnect:
        pass
    finally:
        app.state.stream_hub.unsubscribe(q)


@app.websocket("/ws/stream/{workspace_id}")
async def ws_stream_workspace(ws: WebSocket, workspace_id: str) -> None:
    workspace = str(workspace_id).strip()
    if not workspace:
        await ws.close(code=1008)
        return
    kinds_raw = str(ws.query_params.get("kinds") or "").strip()
    allowed_output_kinds: set[str] | None = None
    if kinds_raw:
        parsed = {
            part.strip()
            for part in kinds_raw.split(",")
            if part.strip()
            in {"scalar", "hist_agg", "hist2d", "trace", "params_map", "fit_1d"}
        }
        if parsed:
            allowed_output_kinds = parsed
    trace_decimator = _parse_trace_decimator(ws.query_params.get("trace_decimator"))
    trace_max_points = _parse_trace_max_points(ws.query_params.get("trace_max_points"))
    trace_max_fps = _parse_trace_max_fps(ws.query_params.get("trace_max_fps"))
    rolling_window = _parse_trace_rolling_window(ws.query_params.get("rolling_window"))
    trace_average_mode = _parse_trace_average_mode(ws.query_params.get("trace_average_mode"))
    trace_interval_s = (1.0 / trace_max_fps) if trace_max_fps else 0.0
    next_trace_send_at: dict[str, float] = {}
    pending_trace_msgs: dict[str, dict[str, Any]] = {}
    trace_readers: dict[tuple[str, str], ShmRingReader] = {}
    rolling_buffers: dict[str, deque[np.ndarray]] = {}
    rolling_sums: dict[str, np.ndarray] = {}
    block_sums: dict[str, np.ndarray] = {}
    block_counts: dict[str, int] = {}

    def _close_trace_reader(key: tuple[str, str]) -> None:
        reader = trace_readers.pop(key, None)
        if reader is None:
            return
        try:
            reader.close()
        except Exception:
            pass

    async def _send_trace_message(
        *,
        msg_workspace: str,
        trace_payload: dict[str, Any],
        trace_msg: dict[str, Any],
    ) -> None:
        if trace_interval_s > 0:
            output_id = str(trace_payload.get("output_id") or "").strip()
            trace_key = f"{msg_workspace}:{output_id}"
            now = time.monotonic()
            next_at = next_trace_send_at.get(trace_key, 0.0)
            if now >= next_at:
                await ws.send_json(trace_msg)
                next_trace_send_at[trace_key] = now + trace_interval_s
            else:
                pending_trace_msgs[trace_key] = trace_msg
                if trace_key not in next_trace_send_at:
                    next_trace_send_at[trace_key] = next_at
            return
        await ws.send_json(trace_msg)

    def _apply_trace_average(output_key: str, trace: np.ndarray) -> np.ndarray | None:
        if rolling_window <= 1:
            return trace
        if trace.size <= 0:
            return trace
        if trace_average_mode == "rolling":
            buf = rolling_buffers.get(output_key)
            sum_trace = rolling_sums.get(output_key)
            if buf is None or sum_trace is None or int(sum_trace.size) != int(trace.size):
                buf = deque()
                sum_trace = np.zeros(int(trace.size), dtype=np.float64)
                rolling_buffers[output_key] = buf
                rolling_sums[output_key] = sum_trace
            incoming = trace.astype(np.float64, copy=True)
            if len(buf) >= int(rolling_window):
                oldest = buf.popleft()
                sum_trace -= oldest
            buf.append(incoming)
            sum_trace += incoming
            return sum_trace / float(max(1, len(buf)))
        sum_trace = block_sums.get(output_key)
        count = int(block_counts.get(output_key, 0))
        if sum_trace is None or int(sum_trace.size) != int(trace.size):
            sum_trace = np.zeros(int(trace.size), dtype=np.float64)
            count = 0
            block_sums[output_key] = sum_trace
        sum_trace += trace.astype(np.float64, copy=False)
        count += 1
        block_counts[output_key] = count
        if count < int(rolling_window):
            return None
        out = sum_trace / float(count)
        sum_trace.fill(0.0)
        block_counts[output_key] = 0
        return out

    await ws.accept()
    q = app.state.stream_analysis_hub.subscribe(maxsize=150)
    try:
        while True:
            timeout_s = 0.05 if pending_trace_msgs else None
            try:
                if timeout_s is None:
                    msg = await q.get()
                else:
                    msg = await asyncio.wait_for(q.get(), timeout=timeout_s)
            except asyncio.TimeoutError:
                if not pending_trace_msgs:
                    continue
                now = time.monotonic()
                due = [k for k, at in next_trace_send_at.items() if now >= at]
                for key in due:
                    pending = pending_trace_msgs.pop(key, None)
                    if pending is None:
                        continue
                    await ws.send_json(pending)
                    if trace_interval_s > 0:
                        next_trace_send_at[key] = now + trace_interval_s
                    else:
                        next_trace_send_at.pop(key, None)
                continue
            if not isinstance(msg, dict):
                continue
            topic = str(msg.get("topic") or "").strip()
            payload = msg.get("payload")
            if not isinstance(payload, dict):
                continue
            msg_workspace = str(payload.get("workspace_id") or "").strip()
            if msg_workspace != workspace:
                continue
            if topic not in {
                "manager.stream_analysis.output",
                "manager.stream_analysis.trace_ready",
                "manager.stream_analysis.workspace_status",
                "manager.stream_analysis.error",
            }:
                continue
            if (
                topic in {"manager.stream_analysis.output", "manager.stream_analysis.trace_ready"}
                and allowed_output_kinds is not None
            ):
                kind = str(payload.get("kind") or "").strip()
                if kind not in allowed_output_kinds:
                    continue

            if topic == "manager.stream_analysis.trace_ready":
                output_id = str(payload.get("output_id") or "").strip()
                node_id = str(payload.get("node_id") or "").strip()
                shm_name = str(payload.get("shm_name") or "").strip()
                seq_raw = payload.get("seq")
                if not output_id or not node_id or not shm_name:
                    continue
                try:
                    trace_seq = int(seq_raw)
                except Exception:
                    continue
                reader_key = (msg_workspace, output_id)
                reader = trace_readers.get(reader_key)
                if reader is None or reader.name != shm_name:
                    _close_trace_reader(reader_key)
                    try:
                        reader = ShmRingReader.attach(shm_name)
                    except Exception:
                        continue
                    trace_readers[reader_key] = reader
                event = reader.read_event(trace_seq)
                if not isinstance(event, dict):
                    continue
                payload_bytes = event.get("payload")
                if not isinstance(payload_bytes, (bytes, bytearray, memoryview)):
                    continue
                try:
                    trace_arr = np.frombuffer(payload_bytes, dtype=reader.layout.dtype).reshape(
                        tuple(int(v) for v in reader.layout.shape)
                    )
                except Exception:
                    continue
                trace_key = f"{msg_workspace}:{output_id}"
                trace_flat = _apply_trace_average(trace_key, trace_arr.reshape(-1))
                if trace_flat is None:
                    continue
                if trace_max_points is not None:
                    trace_values = _decimate_trace_values(
                        trace_flat,
                        mode=trace_decimator,
                        max_points=trace_max_points,
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
                await _send_trace_message(
                    msg_workspace=msg_workspace,
                    trace_payload=trace_payload,
                    trace_msg={"topic": "manager.stream_analysis.output", "payload": trace_payload},
                )
                continue

            if (
                topic == "manager.stream_analysis.output"
                and str(payload.get("kind") or "").strip() == "trace"
            ):
                trace_payload = payload
                points = _coerce_trace_array(payload.get("value"))
                output_id = str(payload.get("output_id") or "").strip()
                if points is not None and output_id:
                    trace_key = f"{msg_workspace}:{output_id}"
                    rolled = _apply_trace_average(trace_key, points)
                    if rolled is None:
                        continue
                    decimated = (
                        _decimate_trace_values(
                            rolled,
                            mode=trace_decimator,
                            max_points=trace_max_points,
                        )
                        if trace_max_points is not None
                        else rolled.tolist()
                    )
                    trace_payload = dict(payload)
                    trace_payload["value"] = decimated
                    trace_payload["point_count"] = (
                        len(decimated) if isinstance(decimated, list) else 0
                    )
                    if trace_max_points is not None and len(decimated) < int(points.size):
                        trace_payload["decimated"] = True
                trace_msg = msg if trace_payload is payload else {**msg, "payload": trace_payload}
                await _send_trace_message(
                    msg_workspace=msg_workspace,
                    trace_payload=trace_payload,
                    trace_msg=trace_msg,
                )
                continue
            await ws.send_json(msg)
    except WebSocketDisconnect:
        pass
    finally:
        for key in list(trace_readers.keys()):
            _close_trace_reader(key)
        app.state.stream_analysis_hub.unsubscribe(q)


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
