from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import ctypes
from pathlib import Path
from typing import Any

from ..schemas.run_meta import run_meta_calls_to_json
from ..schemas.stream import stream_calls_to_json
from ..schemas.telemetry import telemetry_calls_to_json
from ..utils.exit_codes import derive_signal_name, describe_exit_code, exit_code_hex
from ..utils.manager_network import derive_local_connect_endpoint

Json = dict[str, Any]


# Cap on consecutive popen.kill() retries inside
# `enforce_managed_process_stop_timeout` before the handle is escalated
# to FAILED. Five attempts at the supervise-tick rate (~10 Hz) means
# the operator sees a clear failure within half a second of the OS
# refusing to clean up the zombie process, instead of the watchdog
# silently spamming kill() forever.
_MAX_KILL_ATTEMPTS = 5


# Topics emitted by `_publish_process_event` / `_publish_driver_event` that
# should carry the full diagnostic payload (tail logs, signal name, etc.).
FAILURE_PROCESS_TOPICS: frozenset[str] = frozenset(
    {
        "manager.process.failed",
        "manager.process.crashloop",
    }
)
FAILURE_DRIVER_TOPICS: frozenset[str] = frozenset(
    {
        "manager.driver.failed",
        "manager.driver.crashloop",
        "manager.driver.kill_timeout",
    }
)


def _read_process_rss_bytes_windows(pid: int) -> int | None:
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    process_query_information = 0x0400
    process_vm_read = 0x0010
    desired_access = process_query_limited_information | process_vm_read
    c_size_t = ctypes.c_size_t

    class _ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", c_size_t),
            ("WorkingSetSize", c_size_t),
            ("QuotaPeakPagedPoolUsage", c_size_t),
            ("QuotaPagedPoolUsage", c_size_t),
            ("QuotaPeakNonPagedPoolUsage", c_size_t),
            ("QuotaNonPagedPoolUsage", c_size_t),
            ("PagefileUsage", c_size_t),
            ("PeakPagefileUsage", c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(desired_access, False, int(pid))
    if not handle:
        desired_access = process_query_information | process_vm_read
        handle = kernel32.OpenProcess(desired_access, False, int(pid))
    if not handle:
        return None
    try:
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
        ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        if not ok:
            return None
        return int(counters.WorkingSetSize)
    except Exception:
        return None
    finally:
        kernel32.CloseHandle(handle)


def _read_process_rss_bytes_procfs(pid: int) -> int | None:
    statm = Path("/proc") / str(pid) / "statm"
    try:
        raw = statm.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if not raw:
        return None
    parts = raw.split()
    if len(parts) < 2:
        return None
    try:
        resident_pages = int(parts[1])
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
    except Exception:
        return None
    if resident_pages < 0 or page_size <= 0:
        return None
    return resident_pages * page_size


def _read_process_rss_bytes(pid: int) -> int | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        return _read_process_rss_bytes_windows(pid)
    return _read_process_rss_bytes_procfs(pid)


def _cached_process_rss_bytes(manager: Any, pid: int | None) -> int | None:
    if not isinstance(pid, int) or pid <= 0:
        return None
    # Production ``Manager`` initialises these via ``ManagerCaches``;
    # the hasattr guards are a safety net for SimpleNamespace stubs.
    if not hasattr(manager, "_process_rss_cache"):
        manager._process_rss_cache = {}
    if not hasattr(manager, "_process_rss_cache_ttl_s"):
        manager._process_rss_cache_ttl_s = 1.0
    cache = manager._process_rss_cache
    ttl_s = manager._process_rss_cache_ttl_s
    now = time.monotonic()
    entry = cache.get(pid)
    if (
        isinstance(entry, tuple)
        and len(entry) == 2
        and isinstance(entry[0], (int, float))
        and now - float(entry[0]) <= ttl_s
    ):
        cached_value = entry[1]
        return int(cached_value) if isinstance(cached_value, int) else None

    rss_bytes = _read_process_rss_bytes(pid)
    cache[pid] = (now, rss_bytes)

    # Keep cache bounded and drop stale entries opportunistically.
    if len(cache) > 256:
        stale_cutoff = now - max(ttl_s * 4.0, 4.0)
        stale_keys = [
            key
            for key, value in cache.items()
            if not isinstance(value, tuple)
            or len(value) != 2
            or not isinstance(value[0], (int, float))
            or float(value[0]) < stale_cutoff
        ]
        for key in stale_keys:
            cache.pop(key, None)

    return rss_bytes


def _enum_member(current: Any, name: str) -> Any:
    enum_cls = current if isinstance(current, type) else type(current)
    return getattr(enum_cls, name, name)


def start_driver(manager: Any, device_id: str) -> None:
    handle = manager._devices.get(device_id)
    if handle is None:
        raise KeyError(f"Unknown device_id {device_id!r}")
    if handle.process is not None and handle.process.poll() is None:
        return
    if str(handle.driver_process_state) == "CRASHLOOP":
        return

    cmd = manager._build_driver_cmd(handle.spec)
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["EXPERIMENT_CONTROL_ROUTER_RPC"] = derive_local_connect_endpoint(
        manager._external_rpc_bind, 6000
    )
    env["EXPERIMENT_CONTROL_MANAGER_PUB"] = manager._external_pub_connect_local
    env["EXPERIMENT_CONTROL_INSTANCE_ID"] = manager._instance_id
    try:
        handle.process = subprocess.Popen(
            cmd,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
    except Exception as exc:
        handle.process = None
        handle.driver_pid = None
        handle.driver_popen_pid = None
        handle.driver_process_state = _enum_member(handle.driver_process_state, "FAILED")
        handle.driver_last_error = str(exc)
        handle.driver_last_error_kind = "spawn_error"
        manager._publish_driver_event("manager.driver.failed", handle)
        manager._emit_log(
            severity="error",
            topic="manager.driver.spawn_error",
            message=str(exc),
            source_kind="driver",
            source_id=device_id,
            device_id=device_id,
            stream="event",
            payload={"device_id": device_id, "cmd": cmd},
        )
        raise
    manager._adopt_with_process_guard(
        handle.process,
        target_kind="driver",
        target_id=device_id,
    )
    handle.driver_popen_pid = handle.process.pid
    handle.driver_heartbeat_pid = None
    handle.driver_pid = handle.driver_popen_pid
    handle.driver_process_state = _enum_member(handle.driver_process_state, "STARTING")
    handle.driver_last_exit_code = None
    handle.driver_stop_requested_t_mono = None
    # Clear stale failure context before next attempt.
    handle.driver_last_error = None
    handle.driver_last_error_kind = None
    handle.driver_last_signal_name = None
    handle.driver_last_failure_pid = None
    handle.driver_next_restart_t_mono = None
    manager._start_child_log_readers(
        popen=handle.process,
        source_kind="driver",
        source_id=device_id,
        device_id=device_id,
        process_id=None,
    )

    manager._publish_driver_event("manager.driver.starting", handle)


def stop_driver(
    manager: Any,
    device_id: str,
    *,
    force: bool = False,
    offline_state: Any = "OFFLINE",
) -> None:
    handle = manager._devices.get(device_id)
    if handle is None:
        raise KeyError(f"Unknown device_id {device_id!r}")

    mark_device_offline(
        manager,
        device_id,
        handle,
        offline_state=offline_state,
    )

    if handle.process is None or handle.process.poll() is not None:
        handle.process = None
        handle.driver_pid = None
        handle.driver_process_state = _enum_member(handle.driver_process_state, "STOPPED")
        manager._close_device_rpc(handle)
        handle.rpc_endpoint = None
        handle.pub_endpoint = None
        manager._publish_driver_event("manager.driver.stopped", handle)
        return

    if handle.rpc_endpoint is not None:
        try:
            manager._call_device_rpc(
                device_id=device_id,
                action="shutdown",
                params={},
                timeout_ms=1000,
            )
        except Exception:
            pass

    if force:
        try:
            handle.process.terminate()
        except Exception as exc:
            handle.driver_last_error = str(exc)
        try:
            handle.process.kill()
        except Exception as exc:
            handle.driver_last_error = str(exc)

    handle.driver_process_state = _enum_member(handle.driver_process_state, "STOPPING")
    handle.driver_stop_requested_t_mono = time.monotonic()
    manager._close_device_rpc(handle)
    manager._publish_driver_event("manager.driver.stopping", handle)


def mark_device_offline(
    manager: Any,
    device_id: str,
    handle: Any,
    *,
    offline_state: Any,
) -> None:
    age = manager._heartbeat_timeout_s + 1.0
    handle.last_hb_recv_mono = time.monotonic() - age
    manager._last_liveness[device_id] = offline_state
    manager._publish_manager_event(
        "manager.liveness",
        {
            "device_id": device_id,
            "liveness": offline_state,
            "age_s": age,
        },
    )


def driver_is_started(handle: Any) -> bool:
    if handle.process is not None and handle.process.poll() is None:
        return True
    return str(handle.driver_process_state) in {
        "STARTING",
        "RUNNING",
        "STOPPING",
    }


def driver_is_stopped(handle: Any) -> bool:
    if handle.process is None or handle.process.poll() is not None:
        return True
    return str(handle.driver_process_state) in {
        "STOPPED",
        "EXITED",
        "FAILED",
    }


def restart_driver(manager: Any, device_id: str, *, force: bool = False) -> None:
    handle = manager._devices.get(device_id)
    if handle is None:
        raise KeyError(f"Unknown device_id {device_id!r}")

    manager._publish_driver_event("manager.driver.restart_requested", handle)
    try:
        manager.disconnect_device(device_id)
    except Exception:
        pass

    manager.stop_driver(device_id, force=force)
    handle.driver_next_restart_t_mono = (
        time.monotonic() + handle.spec.driver_restart_backoff_s
    )
    manager._publish_driver_event("manager.driver.restart_scheduled", handle)


def recover_device(
    manager: Any,
    device_id: str,
    *,
    reconnect: bool = True,
    force: bool = False,
) -> None:
    handle = manager._devices.get(device_id)
    if handle is None:
        raise KeyError(f"Unknown device_id {device_id!r}")

    try:
        manager.disconnect_device(device_id)
    except Exception:
        pass

    manager.restart_driver(device_id, force=force)
    manager._publish_manager_event(
        "manager.device.recover_sent",
        {
            "device_id": device_id,
            "reconnect": reconnect,
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        },
    )


def add_process(manager: Any, spec: Any, *, handle_cls: Any) -> None:
    if spec.process_id in manager._processes:
        raise ValueError(f"Duplicate process_id {spec.process_id!r}")
    hb_endpoint = manager._resolve_process_heartbeat_endpoint(spec)
    data_endpoint = manager._resolve_process_data_endpoint(spec)
    handle = handle_cls(
        spec=spec,
        heartbeat_endpoint=hb_endpoint,
        process_data_endpoint=data_endpoint,
    )
    manager._processes[spec.process_id] = handle
    manager._connect_process_heartbeat(hb_endpoint)
    manager._connect_process_data(data_endpoint)
    manager._publish_process_event(
        "manager.process.added",
        handle,
    )


def build_router_spec(
    manager: Any,
    *,
    process_spec_cls: Any,
    restart_policy_always: Any,
) -> Any:
    router_path = Path(__file__).resolve().parent / "processes" / "device_router.py"
    router_heartbeat_timeout_s = max(
        3.0,
        (float(manager._device_rpc_timeout_ms) / 1000.0) + 2.0,
    )
    init_kwargs = {
        "external_rpc_bind": manager._external_rpc_bind,
        "device_rpc_timeout_ms": manager._device_rpc_timeout_ms,
        "interceptor_rpc_timeout_ms": manager._interceptor_rpc_timeout_ms,
        "manager_worker_queue_max": manager._router_manager_worker_queue_max,
        "process_worker_queue_max": manager._router_process_worker_queue_max,
        "device_worker_queue_max": manager._router_device_worker_queue_max,
        "mirrored_worker_queue_max": manager._router_mirrored_worker_queue_max,
        "reply_queue_max": manager._router_reply_queue_max,
        "inflight_max": manager._router_inflight_max,
        "federation_mirrors": manager._federation_hub.mirror_route_entries(),
        "origin_instance_id": manager._instance_id,
    }
    argv = [
        sys.executable,
        "-m",
        "experiment_control.cli.start_process",
        "--process-class-path",
        str(router_path),
        "--process-class-name",
        "DeviceRouter",
        "--process-init-json",
        json.dumps(init_kwargs),
        "--manager-rpc",
        manager._internal_rpc_endpoint,
        "--manager-pub",
        manager._external_pub_connect_local,
    ]
    return process_spec_cls(
        process_id=manager._router_process_id,
        argv=argv,
        heartbeat_period_s=1.0,
        heartbeat_timeout_s=router_heartbeat_timeout_s,
        shutdown_timeout_s=3.0,
        restart_policy=restart_policy_always,
        restart_backoff_s=0.5,
        max_restarts=None,
    )


def ensure_router_handle(manager: Any) -> Any:
    handle = manager._processes.get(manager._router_process_id)
    if handle is None:
        spec = manager._build_router_spec()
        manager.add_process(spec)
        handle = manager._processes[manager._router_process_id]
    return handle


def ensure_router_running(manager: Any, *, timeout_s: float, poll_ms: int) -> None:
    handle = manager._ensure_router_handle()
    manager._start_process_handle(handle)
    deadline = time.monotonic() + timeout_s
    while str(handle.state) != "RUNNING":
        if time.monotonic() > deadline:
            manager._drain_supervisor_logs(max_items=5000)
            manager._flush_stale_supervisor_blocks(force=True)
            if str(handle.state) in {"FAILED", "EXITED", "CRASHLOOP"}:
                raise RuntimeError(manager._format_router_startup_failure(handle))
            if handle.popen is not None and handle.popen.poll() is not None:
                raise RuntimeError(manager._format_router_startup_failure(handle))
            raise TimeoutError("Timed out waiting for device_router RUNNING")
        manager._pump_once(poll_ms=poll_ms)


def build_driver_cmd(manager: Any, spec: Any) -> list[str]:
    stream_calls_json = json.dumps(stream_calls_to_json(list(spec.stream_calls or [])))
    run_meta_calls_json = json.dumps(run_meta_calls_to_json(list(spec.run_meta_calls or [])))
    return [
        sys.executable,
        "-m",
        "experiment_control.cli.start_driver",
        "--registry",
        manager._registry_bind,
        "--device-id",
        spec.device_id,
        "--device-class-path",
        str(spec.device_class_path),
        "--device-class-name",
        spec.device_class_name,
        "--device-init-json",
        json.dumps(spec.device_init_kwargs),
        "--telemetry-period-s",
        str(spec.telemetry_period_s),
        "--heartbeat-period-s",
        str(spec.heartbeat_period_s),
        "--command-poll-period-s",
        str(spec.command_poll_period_s),
        "--telemetry-calls-json",
        json.dumps(telemetry_calls_to_json(spec.telemetry_calls)),
        "--stream-calls-json",
        stream_calls_json,
        "--run-meta-calls-json",
        run_meta_calls_json,
        "--instance-id",
        manager._instance_id,
        "--parent-pid",
        str(os.getpid()),
    ]


def adopt_with_process_guard(
    manager: Any,
    popen: Any,
    *,
    target_kind: str,
    target_id: str,
) -> None:
    if popen is None:
        return
    # Production ``Manager`` initialises these in ``__init__``; the
    # hasattr fallbacks are a safety net for SimpleNamespace stubs that
    # don't seed the process-guard bookkeeping.
    if not hasattr(manager, "_process_guard_attach_failures"):
        manager._process_guard_attach_failures = 0
    if not hasattr(manager, "_process_guard_last_error"):
        manager._process_guard_last_error = None
    try:
        manager._process_guard.adopt_popen(popen)
    except Exception as exc:
        manager._process_guard_attach_failures += 1
        manager._process_guard_last_error = str(exc)
        manager._emit_log(
            severity="warning",
            topic="manager.process_guard.adopt_failed",
            message=str(exc),
            source_kind="manager",
            source_id="manager",
            stream="event",
            payload={
                "target_kind": str(target_kind or "unknown"),
                "target_id": str(target_id or ""),
                "pid": int(getattr(popen, "pid", -1) or -1),
                "attach_failures": int(manager._process_guard_attach_failures),
            },
        )


def require_process(manager: Any, process_id: str) -> Any:
    handle = manager._processes.get(process_id)
    if handle is None:
        raise KeyError(f"Unknown process_id {process_id!r}")
    return handle


def resolve_process_heartbeat_endpoint(manager: Any, spec: Any) -> str:
    if spec.heartbeat_endpoint is not None:
        return spec.heartbeat_endpoint

    scheme_host, _, port_str = manager._process_hb_bind_base.rpartition(":")
    if not scheme_host or not port_str.isdigit():
        raise ValueError(
            "process_hb_bind_base must be tcp://host:port, "
            f"got {manager._process_hb_bind_base!r}"
        )
    base_port = int(port_str)
    port = base_port + manager._process_hb_port_offset
    manager._process_hb_port_offset += 1
    return f"{scheme_host}:{port}"


def resolve_process_data_endpoint(manager: Any, spec: Any) -> str:
    if spec.process_data_endpoint is not None:
        return spec.process_data_endpoint

    scheme_host, _, port_str = manager._process_data_bind_base.rpartition(":")
    if not scheme_host or not port_str.isdigit():
        raise ValueError(
            "process_data_bind_base must be tcp://host:port, "
            f"got {manager._process_data_bind_base!r}"
        )
    base_port = int(port_str)
    port = base_port + manager._process_data_port_offset
    manager._process_data_port_offset += 1
    return f"{scheme_host}:{port}"


def connect_process_heartbeat(manager: Any, endpoint: str) -> None:
    if endpoint in manager._process_hb_connected:
        return
    manager._process_hb_sub.connect(endpoint)
    manager._process_hb_connected.add(endpoint)


def connect_process_data(manager: Any, endpoint: str) -> None:
    if endpoint in manager._process_data_connected:
        return
    manager._process_data_sub.connect(endpoint)
    manager._process_data_connected.add(endpoint)


def expand_process_argv(manager: Any, argv: list[str], handle: Any) -> list[str]:
    del manager
    out: list[str] = []
    for arg in argv:
        if isinstance(arg, str):
            arg = arg.replace("{process_id}", handle.spec.process_id)
            arg = arg.replace("{heartbeat_endpoint}", handle.heartbeat_endpoint)
            arg = arg.replace("{process_data_endpoint}", handle.process_data_endpoint)
        out.append(arg)
    return out


def start_process_handle(
    manager: Any,
    handle: Any,
    *,
    reset_collision_retry: bool = True,
) -> None:
    if handle.popen is not None and handle.popen.poll() is None:
        return
    if str(handle.state) == "CRASHLOOP":
        return

    if not handle.heartbeat_endpoint:
        handle.heartbeat_endpoint = manager._resolve_process_heartbeat_endpoint(handle.spec)
    manager._connect_process_heartbeat(handle.heartbeat_endpoint)
    if not handle.process_data_endpoint:
        handle.process_data_endpoint = manager._resolve_process_data_endpoint(handle.spec)
    manager._connect_process_data(handle.process_data_endpoint)

    argv = manager._expand_process_argv(list(handle.spec.argv), handle)
    argv += [
        "--process-id",
        handle.spec.process_id,
        "--heartbeat-endpoint",
        handle.heartbeat_endpoint,
        "--process-data-endpoint",
        handle.process_data_endpoint,
        "--instance-id",
        manager._instance_id,
        "--parent-pid",
        str(os.getpid()),
    ]

    env = os.environ.copy()
    if handle.spec.env:
        env.update(handle.spec.env)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["EXPERIMENT_CONTROL_INSTANCE_ID"] = manager._instance_id

    try:
        handle.popen = subprocess.Popen(
            argv,
            cwd=handle.spec.cwd,
            env=env,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Fresh process: reset the kill-attempt counter tracked by
        # enforce_managed_process_stop_timeout so a restarted handle
        # gets the full _MAX_KILL_ATTEMPTS budget before re-escalating.
        handle.kill_attempts = 0
    except Exception as exc:
        handle.popen = None
        handle.pid = None
        handle.popen_pid = None
        handle.state = _enum_member(handle.state, "FAILED")
        handle.last_error = str(exc)
        handle.last_error_kind = "spawn_error"
        manager._publish_process_event("manager.process.failed", handle)
        manager._emit_log(
            severity="error",
            topic="manager.process.spawn_error",
            message=str(exc),
            source_kind="process",
            source_id=handle.spec.process_id,
            process_id=handle.spec.process_id,
            stream="event",
            payload={"process_id": handle.spec.process_id, "argv": argv},
        )
        raise

    manager._adopt_with_process_guard(
        handle.popen,
        target_kind="process",
        target_id=handle.spec.process_id,
    )
    handle.popen_pid = handle.popen.pid
    handle.heartbeat_pid = None
    handle.pid = handle.popen_pid
    handle.state = _enum_member(handle.state, "STARTING")
    handle.rpc_endpoint = None
    manager._close_process_rpc(handle)
    handle.last_start_t_wall = time.time()
    handle.last_start_t_mono = time.monotonic()
    handle.last_hb_t_wall = None
    handle.last_hb_t_mono = None
    handle.last_hb_recv_mono = None
    handle.last_heartbeat_payload = None
    handle.last_exit_code = None
    handle.stop_requested_t_mono = None
    handle.next_restart_t_mono = None
    # Clear stale failure context before next attempt.
    handle.last_error = None
    handle.last_error_kind = None
    handle.last_signal_name = None
    handle.last_failure_pid = None
    handle.last_heartbeat_age_s = None
    handle.last_liveness_age_s = None
    handle.last_heartbeat_received = None
    handle.heartbeat_stale_strikes = 0
    handle.last_stale_detected_mono = None
    handle.terminated_by_manager = False
    handle.termination_reason = None
    handle.termination_method = None
    handle.termination_error = None
    handle.recent_manager_loop_stall = False
    handle.last_manager_loop_stall_duration_s = None
    if reset_collision_retry:
        handle.startup_collision_retry_done = False
    manager._start_child_log_readers(
        popen=handle.popen,
        source_kind="process",
        source_id=handle.spec.process_id,
        device_id=None,
        process_id=handle.spec.process_id,
    )
    manager._publish_process_event("manager.process.started", handle)


def stop_process_handle(manager: Any, handle: Any) -> None:
    if handle.popen is None:
        handle.state = _enum_member(handle.state, "STOPPED")
        return
    if handle.popen.poll() is not None:
        rc = handle.popen.poll()
        handle.last_exit_code = rc
        # Capture the pid BEFORE clearing popen so the FAILED branch
        # below can populate handle.last_failure_pid. Mirrors the same
        # capture-before-clear pattern in update_managed_process_exit_state
        # (line 1199); without it, consumers of manager.process.failed
        # (manager.py:3253 reads handle.last_failure_pid for the failure
        # report) see stale-or-None pid only on this stop-detected-crash
        # code path.
        exiting_pid = handle.popen_pid or handle.pid
        handle.popen = None
        handle.rpc_endpoint = None
        manager._close_process_rpc(handle)
        # A process we're "stopping" that has already died with a
        # non-zero exit code is a crashed process, not a clean exit;
        # mirror update_managed_process_exit_state's classification so
        # the manager.process.* event accurately reflects what
        # happened. Without this, an operator-initiated stop on an
        # already-crashed process showed up as ".exited" with no
        # diagnostic.
        if isinstance(rc, int) and rc != 0:
            handle.state = _enum_member(handle.state, "FAILED")
            handle.last_failure_pid = exiting_pid
            handle.last_signal_name = derive_signal_name(int(rc))
            handle.last_error_kind = (
                handle.last_error_kind or "nonzero_exit"
            )
            if not handle.last_error:
                description = describe_exit_code(int(rc)) or f"exit code {rc}"
                handle.last_error = f"process exited: {description}"
            manager._publish_process_event("manager.process.failed", handle)
        else:
            handle.state = _enum_member(handle.state, "EXITED")
            manager._publish_process_event("manager.process.exited", handle)
        return

    graceful_requested = False
    if handle.rpc_endpoint is not None:
        req: Json = {
            "type": "process.stop",
            "params": {},
            "request_id": f"mgr-stop-{int(time.time() * 1000)}",
        }
        timeout_ms = max(100, min(int(manager._device_rpc_timeout_ms), 500))
        try:
            resp = manager._call_process_rpc(
                process_id=handle.spec.process_id,
                request=req,
                timeout_ms=timeout_ms,
            )
            graceful_requested = bool(isinstance(resp, dict) and resp.get("ok", False))
        except Exception:
            graceful_requested = False

    if not graceful_requested:
        try:
            handle.popen.terminate()
        except Exception as exc:
            handle.last_error = str(exc)
    handle.state = _enum_member(handle.state, "STOPPING")
    handle.stop_requested_t_mono = time.monotonic()
    handle.rpc_endpoint = None
    manager._close_process_rpc(handle)
    manager._publish_process_event("manager.process.stopping", handle)


def maybe_schedule_restart(manager: Any, handle: Any, now_mono: float) -> None:
    if handle.next_restart_t_mono is not None:
        return

    policy = str(handle.spec.restart_policy).upper()
    if policy == "NEVER":
        return
    if policy == "ON_FAILURE":
        if handle.last_exit_code is None:
            return
        if int(handle.last_exit_code) == 0:
            return

    delay = max(float(handle.spec.restart_backoff_s), 0.0)
    handle.next_restart_t_mono = now_mono + delay
    manager._publish_process_event("manager.process.restart_scheduled", handle)


def try_restart_process(manager: Any, handle: Any) -> None:
    if (
        handle.spec.max_restarts is not None
        and handle.restart_count >= handle.spec.max_restarts
    ):
        handle.state = _enum_member(handle.state, "CRASHLOOP")
        handle.next_restart_t_mono = None
        manager._publish_process_event("manager.process.crashloop", handle)
        return

    handle.restart_count += 1
    handle.last_restart_t_mono = time.monotonic()
    handle.next_restart_t_mono = None
    handle.stop_requested_t_mono = None
    manager._start_process_handle(handle)


def process_snapshot(manager: Any, handle: Any) -> Json:
    hb_age_s: float | None = None
    now_mono = time.monotonic()
    # Compute age from the manager-side receive time so the displayed
    # age matches what the timeout check uses (which is what subscribers
    # actually care about: "is the manager seeing this process alive").
    # Fall back to sender time if recv time isn't yet populated.
    if handle.last_hb_recv_mono is not None:
        hb_age_s = now_mono - handle.last_hb_recv_mono
    elif handle.last_hb_t_mono is not None:
        hb_age_s = now_mono - handle.last_hb_t_mono
    rss_bytes = _cached_process_rss_bytes(manager, handle.pid)
    return {
        "process_id": handle.spec.process_id,
        "argv": handle.spec.argv,
        "cwd": handle.spec.cwd,
        "env": handle.spec.env,
        "heartbeat_period_s": handle.spec.heartbeat_period_s,
        "heartbeat_timeout_s": handle.spec.heartbeat_timeout_s,
        "shutdown_timeout_s": handle.spec.shutdown_timeout_s,
        "restart_policy": handle.spec.restart_policy,
        "restart_backoff_s": handle.spec.restart_backoff_s,
        "max_restarts": handle.spec.max_restarts,
        "state": handle.state,
        "pid": handle.pid,
        "popen_pid": handle.popen_pid,
        "heartbeat_pid": handle.heartbeat_pid,
        "rss_bytes": rss_bytes,
        "last_start_t_wall": handle.last_start_t_wall,
        "last_start_t_mono": handle.last_start_t_mono,
        "last_hb_t_wall": handle.last_hb_t_wall,
        "last_hb_t_mono": handle.last_hb_t_mono,
        "last_hb_recv_mono": handle.last_hb_recv_mono,
        "hb_age_s": hb_age_s,
        "last_exit_code": handle.last_exit_code,
        "exit_code_hex": exit_code_hex(handle.last_exit_code),
        "exit_code_description": describe_exit_code(handle.last_exit_code),
        "restart_count": handle.restart_count,
        "last_restart_t_mono": handle.last_restart_t_mono,
        "last_error": handle.last_error,
        "last_error_kind": handle.last_error_kind,
        "last_signal_name": handle.last_signal_name,
        "last_failure_pid": handle.last_failure_pid,
        "last_heartbeat_age_s": handle.last_heartbeat_age_s,
        "last_liveness_age_s": handle.last_liveness_age_s,
        "last_heartbeat_received": handle.last_heartbeat_received,
        "heartbeat_stale_strikes": handle.heartbeat_stale_strikes,
        "last_stale_detected_mono": handle.last_stale_detected_mono,
        "terminated_by_manager": handle.terminated_by_manager,
        "termination_reason": handle.termination_reason,
        "termination_method": handle.termination_method,
        "termination_error": handle.termination_error,
        "recent_manager_loop_stall": handle.recent_manager_loop_stall,
        "last_manager_loop_stall_duration_s": handle.last_manager_loop_stall_duration_s,
        "last_heartbeat_payload": handle.last_heartbeat_payload,
        "tail_stdout": list(handle.supervisor_stdout_tail),
        "tail_stderr": list(handle.supervisor_stderr_tail),
        "tail_supervisor_logs": list(handle.supervisor_log_tail),
        "stdout_log_path": handle.stdout_log_path,
        "stderr_log_path": handle.stderr_log_path,
        "heartbeat_endpoint": handle.heartbeat_endpoint,
        "process_data_endpoint": handle.process_data_endpoint,
        "rpc_endpoint": handle.rpc_endpoint,
        "registered": handle.rpc_endpoint is not None,
    }


def update_device_driver_exit_state(manager: Any, handle: Any, rc: int) -> None:
    rc_int = int(rc)
    handle.driver_last_exit_code = rc_int
    exiting_pid = handle.driver_popen_pid or handle.driver_pid
    handle.process = None
    handle.driver_pid = None
    if (
        str(handle.driver_process_state) == "STOPPING"
        and handle.driver_stop_requested_t_mono is not None
    ):
        handle.driver_process_state = _enum_member(handle.driver_process_state, "STOPPED")
        manager._publish_driver_event("manager.driver.stopped", handle)
        return
    if rc_int == 0:
        handle.driver_process_state = _enum_member(handle.driver_process_state, "STOPPED")
        manager._publish_driver_event("manager.driver.exited", handle)
        return
    handle.driver_process_state = _enum_member(handle.driver_process_state, "FAILED")
    handle.driver_last_failure_pid = exiting_pid
    handle.driver_last_signal_name = derive_signal_name(rc_int)
    handle.driver_last_error_kind = handle.driver_last_error_kind or "nonzero_exit"
    if not handle.driver_last_error:
        description = describe_exit_code(rc_int) or f"exit code {rc_int}"
        handle.driver_last_error = f"driver exited: {description}"
    manager._publish_driver_event("manager.driver.failed", handle)


def enforce_device_driver_stop_timeout(
    manager: Any,
    handle: Any,
    now_mono: float,
) -> None:
    if str(handle.driver_process_state) != "STOPPING":
        return
    if (
        handle.driver_stop_requested_t_mono is None
        or handle.process is None
        or handle.process.poll() is not None
    ):
        return
    if now_mono - handle.driver_stop_requested_t_mono > handle.spec.driver_stop_timeout_s:
        try:
            handle.process.kill()
            manager._publish_driver_event("manager.driver.killing", handle)
        except Exception as exc:
            handle.driver_last_error = str(exc)
    if (
        now_mono - handle.driver_stop_requested_t_mono
        > handle.spec.driver_stop_timeout_s + handle.spec.driver_kill_timeout_s
        and handle.process is not None
        and handle.process.poll() is None
    ):
        handle.driver_process_state = _enum_member(handle.driver_process_state, "FAILED")
        handle.driver_last_error = "kill timeout"
        handle.driver_last_error_kind = "kill_timeout"
        handle.driver_last_failure_pid = handle.driver_pid
        manager._publish_driver_event("manager.driver.kill_timeout", handle)


def maybe_restart_device_driver(
    manager: Any,
    device_id: str,
    handle: Any,
    now_mono: float,
) -> None:
    if (
        handle.driver_next_restart_t_mono is None
        or now_mono < handle.driver_next_restart_t_mono
    ):
        return
    if (
        handle.spec.driver_max_restarts is not None
        and handle.driver_restart_count >= handle.spec.driver_max_restarts
    ):
        handle.driver_process_state = _enum_member(handle.driver_process_state, "CRASHLOOP")
        handle.driver_next_restart_t_mono = None
        manager._publish_driver_event("manager.driver.crashloop", handle)
        return
    handle.driver_restart_count += 1
    handle.driver_last_restart_t_mono = now_mono
    handle.driver_next_restart_t_mono = None
    manager._publish_driver_event("manager.driver.restarting", handle)
    manager.start_driver(device_id)


def _auto_reconnect_status(handle: Any) -> Json:
    spec = handle.spec.auto_reconnect
    return {
        "enabled": bool(spec.enabled),
        "attempts": int(handle.auto_reconnect_attempts),
        "last_attempt_mono": handle.auto_reconnect_last_attempt_mono,
        "last_attempt_wall": handle.auto_reconnect_last_attempt_wall,
        "last_success_mono": handle.auto_reconnect_last_success_mono,
        "healthy_since_mono": handle.auto_reconnect_healthy_since_mono,
        "last_error": handle.auto_reconnect_last_error,
        "suppressed": bool(handle.auto_reconnect_suppressed),
        "cooldown_s": float(spec.cooldown_s),
        "max_attempts": spec.max_attempts,
        "on_telemetry_stale_s": spec.on_telemetry_stale_s,
        "reset_attempts_after_ok_s": float(spec.reset_attempts_after_ok_s),
    }


def _auto_reconnect_publish(manager: Any, topic: str, device_id: str, payload: Json) -> None:
    payload = dict(payload)
    payload.setdefault("device_id", device_id)
    payload.setdefault("ts", {"t_wall": time.time(), "t_mono": time.monotonic()})
    manager._publish_manager_event(topic, payload)


def _auto_reconnect_reset_if_healthy(
    manager: Any,
    device_id: str,
    handle: Any,
    now_mono: float,
) -> None:
    spec = handle.spec.auto_reconnect
    if not spec.enabled or handle.auto_reconnect_attempts <= 0:
        return
    latest_ts = manager._telemetry_last_bundle_ts.get(device_id)
    if latest_ts is None:
        handle.auto_reconnect_healthy_since_mono = None
        return
    age_s = now_mono - latest_ts.t_mono
    threshold = spec.on_telemetry_stale_s
    if threshold is None or age_s > threshold:
        handle.auto_reconnect_healthy_since_mono = None
        return
    if handle.auto_reconnect_healthy_since_mono is None:
        handle.auto_reconnect_healthy_since_mono = now_mono
        return
    if now_mono - handle.auto_reconnect_healthy_since_mono < spec.reset_attempts_after_ok_s:
        return
    handle.auto_reconnect_attempts = 0
    handle.auto_reconnect_suppressed = False
    handle.auto_reconnect_last_error = None
    _auto_reconnect_publish(
        manager,
        "manager.device.auto_reconnect.reset",
        device_id,
        {"auto_reconnect": _auto_reconnect_status(handle)},
    )


def _auto_reconnect_should_attempt(
    manager: Any,
    device_id: str,
    handle: Any,
    now_mono: float,
) -> tuple[bool, float | None, str | None]:
    spec = handle.spec.auto_reconnect
    if not spec.enabled:
        return False, None, None
    if str(handle.driver_process_state) not in {"RUNNING"}:
        return False, None, None
    if handle.rpc_endpoint is None:
        return False, None, None
    latest_ts = manager._telemetry_last_bundle_ts.get(device_id)
    if latest_ts is None:
        return False, None, None
    threshold = spec.on_telemetry_stale_s
    if threshold is None:
        return False, None, None
    age_s = now_mono - latest_ts.t_mono
    if age_s <= threshold:
        return False, age_s, None
    if (
        handle.auto_reconnect_last_attempt_mono is not None
        and now_mono - handle.auto_reconnect_last_attempt_mono < spec.cooldown_s
    ):
        return False, age_s, "cooldown"
    if spec.max_attempts is not None and handle.auto_reconnect_attempts >= spec.max_attempts:
        if not handle.auto_reconnect_suppressed:
            handle.auto_reconnect_suppressed = True
            _auto_reconnect_publish(
                manager,
                "manager.device.auto_reconnect.suppressed",
                device_id,
                {
                    "reason": "max_attempts",
                    "telemetry_age_s": age_s,
                    "auto_reconnect": _auto_reconnect_status(handle),
                },
            )
        return False, age_s, "max_attempts"
    return True, age_s, None


def _auto_reconnect_attempt(
    manager: Any,
    device_id: str,
    handle: Any,
    now_mono: float,
    age_s: float,
) -> None:
    spec = handle.spec.auto_reconnect
    handle.auto_reconnect_attempts += 1
    handle.auto_reconnect_last_attempt_mono = now_mono
    handle.auto_reconnect_last_attempt_wall = time.time()
    handle.auto_reconnect_last_error = None
    handle.auto_reconnect_suppressed = False
    attempt = int(handle.auto_reconnect_attempts)
    _auto_reconnect_publish(
        manager,
        "manager.device.auto_reconnect.attempt",
        device_id,
        {
            "attempt": attempt,
            "telemetry_age_s": age_s,
            "auto_reconnect": _auto_reconnect_status(handle),
        },
    )
    try:
        try:
            manager._call_device_rpc(
                device_id=device_id,
                action="disconnect_device",
                params={},
                timeout_ms=int(spec.disconnect_timeout_ms),
            )
        except Exception:
            pass
        connect_timeout_ms = spec.connect_timeout_ms or manager._device_rpc_timeout_ms
        resp = manager._call_device_rpc(
            device_id=device_id,
            action="connect_device",
            params={},
            timeout_ms=int(connect_timeout_ms),
        )
        if not manager._device_rpc_status_ok(resp):
            raise RuntimeError(manager._device_rpc_error_text(resp))
        handle.auto_reconnect_last_success_mono = time.monotonic()
        handle.auto_reconnect_last_error = None
        _auto_reconnect_publish(
            manager,
            "manager.device.auto_reconnect.success",
            device_id,
            {
                "attempt": attempt,
                "telemetry_age_s": age_s,
                "response": resp,
                "auto_reconnect": _auto_reconnect_status(handle),
            },
        )
    except Exception as exc:
        handle.auto_reconnect_last_error = str(exc)
        _auto_reconnect_publish(
            manager,
            "manager.device.auto_reconnect.failed",
            device_id,
            {
                "attempt": attempt,
                "telemetry_age_s": age_s,
                "error": str(exc),
                "auto_reconnect": _auto_reconnect_status(handle),
            },
        )


def _maybe_auto_reconnect_device(
    manager: Any,
    device_id: str,
    handle: Any,
    now_mono: float,
) -> None:
    _auto_reconnect_reset_if_healthy(manager, device_id, handle, now_mono)
    should_attempt, age_s, _reason = _auto_reconnect_should_attempt(
        manager,
        device_id,
        handle,
        now_mono,
    )
    if should_attempt and age_s is not None:
        _auto_reconnect_attempt(manager, device_id, handle, now_mono, age_s)


def supervise_device_drivers(manager: Any, now_mono: float) -> None:
    for device_id, handle in manager._devices.items():
        proc = handle.process
        if proc is not None:
            rc = proc.poll()
            if rc is not None:
                manager._update_device_driver_exit_state(handle, int(rc))
        manager._enforce_device_driver_stop_timeout(handle, now_mono)
        manager._maybe_restart_device_driver(device_id, handle, now_mono)
        _maybe_auto_reconnect_device(manager, device_id, handle, now_mono)


def update_managed_process_exit_state(manager: Any, handle: Any, rc: int) -> bool:
    rc_int = int(rc)
    handle.last_exit_code = rc_int
    # Capture the popen pid before we clear it; only published on the FAILED branch.
    exiting_pid = handle.popen_pid or handle.pid
    handle.popen = None
    handle.pid = None
    handle.rpc_endpoint = None
    manager._close_process_rpc(handle)
    if str(handle.state) == "STOPPING":
        handle.state = _enum_member(handle.state, "EXITED")
        manager._publish_process_event("manager.process.exited", handle)
        return False
    if rc_int == 0:
        handle.state = _enum_member(handle.state, "STOPPED")
        manager._publish_process_event("manager.process.exited", handle)
        return False
    if manager._maybe_recover_process_start_collision(handle):
        return True
    handle.state = _enum_member(handle.state, "FAILED")
    handle.last_failure_pid = exiting_pid
    handle.last_signal_name = derive_signal_name(rc_int)
    # Recovery paths may have already set a more specific kind; don't clobber.
    handle.last_error_kind = handle.last_error_kind or "nonzero_exit"
    if not handle.last_error:
        description = describe_exit_code(rc_int) or f"exit code {rc_int}"
        handle.last_error = f"process exited: {description}"
    manager._publish_process_event("manager.process.failed", handle)
    return False


def _recent_manager_loop_stall(manager: Any, now_mono: float) -> bool:
    last_stall = manager._last_loop_stall_mono
    if last_stall is None:
        return False
    return (now_mono - float(last_stall)) <= manager._manager_loop_stall_recent_s


def _heartbeat_age_s(handle: Any, now_mono: float) -> float | None:
    if handle.last_hb_recv_mono is not None:
        return now_mono - handle.last_hb_recv_mono
    if handle.last_start_t_mono is not None:
        return now_mono - handle.last_start_t_mono
    return None


def _publish_heartbeat_refresh_error(manager: Any, exc: Exception) -> None:
    now_mono = time.monotonic()
    # Production ``Manager`` initialises these unconditionally; the
    # hasattr fallbacks are purely safety nets for SimpleNamespace
    # test stubs that don't pre-populate the rate-limit bookkeeping.
    if not hasattr(manager, "_process_hb_refresh_error_period_s"):
        manager._process_hb_refresh_error_period_s = 10.0
    if not hasattr(manager, "_last_process_hb_refresh_error_mono"):
        manager._last_process_hb_refresh_error_mono = None
    if not hasattr(manager, "_process_hb_refresh_error_suppressed"):
        manager._process_hb_refresh_error_suppressed = 0
    period_s = manager._process_hb_refresh_error_period_s
    last_mono = manager._last_process_hb_refresh_error_mono
    if last_mono is not None and (now_mono - float(last_mono)) < period_s:
        manager._process_hb_refresh_error_suppressed += 1
        return
    suppressed = manager._process_hb_refresh_error_suppressed
    manager._last_process_hb_refresh_error_mono = now_mono
    manager._process_hb_refresh_error_suppressed = 0
    try:
        manager._publish_manager_event(
            "manager.process.heartbeat_refresh_failed",
            {
                "error": str(exc),
                "suppressed_count": suppressed,
                "ts": {"t_wall": time.time(), "t_mono": now_mono},
            },
        )
    except Exception:
        pass


def _refresh_pending_process_heartbeats(manager: Any) -> None:
    # Production ``Manager`` always provides ``_process_hb_sub`` (via
    # ``ManagerSockets``) and ``_handle_process_pub`` (instance method).
    # The hasattr guards remain as safety nets for SimpleNamespace test
    # stubs that exercise sibling helpers without a real socket.
    if not hasattr(manager, "_process_hb_sub") or not hasattr(manager, "_handle_process_pub"):
        return
    try:
        if manager._process_hb_sub.poll(0):
            manager._handle_process_pub()
    except Exception as exc:
        _publish_heartbeat_refresh_error(manager, exc)


def enforce_managed_process_heartbeat_timeout(
    manager: Any,
    handle: Any,
    now_mono: float,
) -> None:
    if str(handle.state) not in {"STARTING", "RUNNING"}:
        return
    # Use manager-side receive time (not the sender's t_mono) so manager
    # buffering / scheduling delay isn't blamed on the process. With
    # the SUB drain-all loop in handle_process_pub, recv time follows
    # send time within milliseconds in normal operation.
    hb_age = _heartbeat_age_s(handle, now_mono)
    if hb_age is None:
        return
    timeout_s = float(handle.spec.heartbeat_timeout_s)
    if hb_age > timeout_s:
        _refresh_pending_process_heartbeats(manager)
        hb_age = _heartbeat_age_s(handle, now_mono)
        if hb_age is None:
            return
    if hb_age <= timeout_s:
        handle.heartbeat_stale_strikes = 0
        handle.last_stale_detected_mono = None
        handle.recent_manager_loop_stall = False
        # Keep the age field fresh during healthy operation so dashboards
        # don't see a stale value left over from the last stale event.
        handle.last_heartbeat_age_s = (
            float(hb_age) if handle.last_hb_recv_mono is not None else None
        )
        return

    heartbeat_received = handle.last_hb_recv_mono is not None
    recent_stall = _recent_manager_loop_stall(manager, now_mono)
    hard_timeout_s = timeout_s * max(1.0, manager._heartbeat_hard_timeout_multiplier)
    strikes_to_fail = manager._heartbeat_stale_strikes_to_fail
    # Rate-limit strikes to one per heartbeat_period_s. The supervision
    # check runs every ~50 ms; without this rate limit, two consecutive
    # checks ~100 ms apart would race past strikes_to_fail=2 even when
    # the process is healthy (e.g. its HB is queued but not yet
    # drained). With the rate limit, strikes accumulate at the rate
    # the process is expected to publish HBs, giving a real margin.
    period_s = float(handle.spec.heartbeat_period_s)
    last_strike_mono = handle.last_stale_detected_mono
    if last_strike_mono is None or (now_mono - float(last_strike_mono)) >= period_s:
        handle.heartbeat_stale_strikes += 1
        handle.last_stale_detected_mono = now_mono
    handle.last_heartbeat_received = heartbeat_received
    handle.last_liveness_age_s = float(hb_age)
    handle.last_heartbeat_age_s = float(hb_age) if heartbeat_received else None
    handle.recent_manager_loop_stall = recent_stall
    handle.last_manager_loop_stall_duration_s = getattr(
        manager, "_last_loop_stall_duration_s", None
    )

    if recent_stall and handle.heartbeat_stale_strikes < strikes_to_fail and hb_age < hard_timeout_s:
        manager._publish_manager_event(
            "manager.process.heartbeat_stale_deferred",
            {
                "process_id": handle.spec.process_id,
                "heartbeat_age_s": float(hb_age),
                "heartbeat_timeout_s": timeout_s,
                "strikes": int(handle.heartbeat_stale_strikes),
                "strikes_to_fail": strikes_to_fail,
                "recent_manager_loop_stall": True,
                "last_manager_loop_stall_duration_s": handle.last_manager_loop_stall_duration_s,
                "ts": {"t_wall": time.time(), "t_mono": now_mono},
            },
        )
        return

    handle.state = _enum_member(handle.state, "FAILED")
    handle.last_error_kind = "heartbeat_stale"
    handle.last_failure_pid = handle.popen_pid or handle.pid
    if heartbeat_received:
        handle.last_error = f"heartbeat stale ({hb_age:.2f}s > {timeout_s:.2f}s)"
    else:
        handle.last_error = (
            f"no heartbeat received {hb_age:.2f}s after spawn "
            f"(timeout {timeout_s:.2f}s)"
        )
    handle.terminated_by_manager = False
    handle.termination_reason = "heartbeat_stale"
    handle.termination_method = None
    handle.termination_error = None
    if handle.popen is not None and handle.popen.poll() is None:
        try:
            handle.popen.terminate()
            handle.stop_requested_t_mono = now_mono
            handle.terminated_by_manager = True
            handle.termination_method = "terminate"
        except Exception as exc:
            handle.termination_error = str(exc)
            handle.last_error = f"{handle.last_error}; terminate failed: {exc}"
    manager._publish_process_event("manager.process.failed", handle)


def enforce_managed_process_stop_timeout(
    manager: Any,
    handle: Any,
    now_mono: float,
) -> None:
    if (
        str(handle.state) != "STOPPING"
        or handle.stop_requested_t_mono is None
        or handle.popen is None
        or handle.popen.poll() is not None
    ):
        return
    if now_mono - handle.stop_requested_t_mono <= handle.spec.shutdown_timeout_s:
        return
    # The process has ignored the graceful-stop window. Kill it. Without
    # a cap this used to fire kill() on every supervise tick (typically
    # tens of times per second) producing log spam, and never escalating
    # the state out of STOPPING if the OS held the pid in zombie state.
    # Track the kill attempts on the handle: after N tries, mark FAILED
    # so the operator (and the manager's recovery logic at
    # _maybe_schedule_restart) can take over instead of waiting forever.
    handle.kill_attempts = int(getattr(handle, "kill_attempts", 0)) + 1
    try:
        handle.popen.kill()
    except Exception as exc:
        handle.last_error = str(exc)
    if handle.kill_attempts >= _MAX_KILL_ATTEMPTS:
        handle.state = _enum_member(handle.state, "FAILED")
        handle.last_error_kind = handle.last_error_kind or "kill_escalated"
        if not handle.last_error:
            handle.last_error = (
                f"process refused to exit after {handle.kill_attempts} "
                f"kill() attempts (last poll() = None)"
            )
        manager._publish_process_event("manager.process.failed", handle)


def maybe_restart_managed_process(manager: Any, handle: Any, now_mono: float) -> None:
    if str(handle.state) in {"FAILED", "EXITED"}:
        if handle.stop_requested_t_mono is None:
            manager._maybe_schedule_restart(handle, now_mono)
    if handle.next_restart_t_mono is not None and now_mono >= handle.next_restart_t_mono:
        manager._try_restart_process(handle)


def supervise_managed_processes(manager: Any, now_mono: float) -> None:
    for _process_id, handle in manager._processes.items():
        popen = handle.popen
        if popen is not None:
            rc = popen.poll()
            if rc is not None and manager._update_managed_process_exit_state(handle, int(rc)):
                continue
        manager._enforce_managed_process_heartbeat_timeout(handle, now_mono)
        manager._enforce_managed_process_stop_timeout(handle, now_mono)
        manager._maybe_restart_managed_process(handle, now_mono)


class ProcessSupervisionMixin:
    """Thin mixin exposing process-supervision entry points used by Manager.

    Phase 8.2.16: ``manager_process_supervision.py`` is the largest
    helper module (1450 LOC, 35+ ``manager``-taking functions).
    Most are internal to the device-driver / managed-process
    supervision state machines and are not called from Manager
    directly. The mixin wraps only the ~24 functions Manager actually
    forwarded — every wrapper is one line and Manager's forwarder
    methods can be deleted, letting MRO take over.

    Functions that need Manager-side state-enum classes (``Liveness``,
    ``ProcessHandle``) stay as Manager forwarders because the mixin
    can't import those without a circular dependency:
    - ``stop_driver`` / ``_mark_device_offline`` pass ``Liveness.OFFLINE``
    - ``add_process`` passes ``handle_cls=ProcessHandle``
    """

    # -- driver-side (start/restart are PUBLIC on Manager) -------------

    def start_driver(self, device_id: str) -> None:
        start_driver(self, device_id)

    def restart_driver(self, device_id: str, *, force: bool = False) -> None:
        restart_driver(self, device_id, force=force)

    def _driver_is_started(self, handle: Any) -> bool:
        return driver_is_started(handle)

    def _driver_is_stopped(self, handle: Any) -> bool:
        return driver_is_stopped(handle)

    def _build_driver_cmd(self, spec: Any) -> list[str]:
        return build_driver_cmd(self, spec)

    # -- router supervision --------------------------------------------

    # ``_build_router_spec`` stays a Manager-side wrapper because it
    # passes ``process_spec_cls=ProcessSpec`` and
    # ``restart_policy_always=RestartPolicy.ALWAYS`` (Manager-module
    # enums the mixin can't reach without a circular import).

    def _ensure_router_handle(self) -> Any:
        return ensure_router_handle(self)

    def _ensure_router_running(self, *, timeout_s: float, poll_ms: int) -> None:
        ensure_router_running(self, timeout_s=timeout_s, poll_ms=poll_ms)

    # -- process-handle lifecycle --------------------------------------

    def _require_process(self, process_id: str) -> Any:
        return require_process(self, process_id)

    def _resolve_process_heartbeat_endpoint(self, spec: Any) -> str:
        return resolve_process_heartbeat_endpoint(self, spec)

    def _resolve_process_data_endpoint(self, spec: Any) -> str:
        return resolve_process_data_endpoint(self, spec)

    def _connect_process_heartbeat(self, endpoint: str) -> None:
        connect_process_heartbeat(self, endpoint)

    def _connect_process_data(self, endpoint: str) -> None:
        connect_process_data(self, endpoint)

    def _expand_process_argv(self, argv: list[str], handle: Any) -> list[str]:
        return expand_process_argv(self, argv, handle)

    def _start_process_handle(
        self, handle: Any, *, reset_collision_retry: bool = True
    ) -> None:
        start_process_handle(
            self, handle, reset_collision_retry=reset_collision_retry
        )

    def _stop_process_handle(self, handle: Any) -> None:
        stop_process_handle(self, handle)

    def _maybe_schedule_restart(self, handle: Any, now_mono: float) -> None:
        maybe_schedule_restart(self, handle, now_mono)

    def _try_restart_process(self, handle: Any) -> None:
        try_restart_process(self, handle)

    def _process_snapshot(self, handle: Any) -> Json:
        return process_snapshot(self, handle)

    # -- supervisor tick ------------------------------------------------

    def _update_device_driver_exit_state(self, handle: Any, rc: int) -> None:
        update_device_driver_exit_state(self, handle, rc)

    def _enforce_device_driver_stop_timeout(
        self, handle: Any, now_mono: float
    ) -> None:
        enforce_device_driver_stop_timeout(self, handle, now_mono)

    def _maybe_restart_device_driver(
        self, device_id: str, handle: Any, now_mono: float
    ) -> None:
        maybe_restart_device_driver(self, device_id, handle, now_mono)

    def _supervise_device_drivers(self, now_mono: float) -> None:
        supervise_device_drivers(self, now_mono)

    def _update_managed_process_exit_state(self, handle: Any, rc: int) -> bool:
        return update_managed_process_exit_state(self, handle, rc)

    def _enforce_managed_process_heartbeat_timeout(
        self, handle: Any, now_mono: float
    ) -> None:
        enforce_managed_process_heartbeat_timeout(self, handle, now_mono)

    def _enforce_managed_process_stop_timeout(
        self, handle: Any, now_mono: float
    ) -> None:
        enforce_managed_process_stop_timeout(self, handle, now_mono)

    def _maybe_restart_managed_process(
        self, handle: Any, now_mono: float
    ) -> None:
        maybe_restart_managed_process(self, handle, now_mono)

    def _supervise_managed_processes(self, now_mono: float) -> None:
        supervise_managed_processes(self, now_mono)
