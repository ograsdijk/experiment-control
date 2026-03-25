from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .schemas.run_meta import run_meta_calls_to_json
from .schemas.stream import stream_calls_to_json
from .schemas.telemetry import telemetry_calls_to_json
from .utils.manager_network import derive_local_connect_endpoint

Json = dict[str, Any]


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
        handle.driver_process_state = _enum_member(handle.driver_process_state, "FAILED")
        handle.driver_last_error = str(exc)
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
    handle.driver_pid = handle.process.pid
    handle.driver_process_state = _enum_member(handle.driver_process_state, "STARTING")
    handle.driver_last_exit_code = None
    handle.driver_stop_requested_t_mono = None
    handle.driver_last_error = None
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
    except Exception as exc:
        handle.popen = None
        handle.pid = None
        handle.state = _enum_member(handle.state, "FAILED")
        handle.last_error = str(exc)
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
    handle.pid = handle.popen.pid
    handle.state = _enum_member(handle.state, "STARTING")
    handle.rpc_endpoint = None
    manager._close_process_rpc(handle)
    handle.last_start_t_wall = time.time()
    handle.last_start_t_mono = time.monotonic()
    handle.last_hb_t_wall = None
    handle.last_hb_t_mono = None
    handle.last_exit_code = None
    handle.stop_requested_t_mono = None
    handle.next_restart_t_mono = None
    handle.last_error = None
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
        handle.state = _enum_member(handle.state, "EXITED")
        handle.last_exit_code = handle.popen.poll()
        handle.popen = None
        handle.rpc_endpoint = None
        manager._close_process_rpc(handle)
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
    if handle.last_hb_t_mono is not None:
        hb_age_s = now_mono - handle.last_hb_t_mono
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
        "last_start_t_wall": handle.last_start_t_wall,
        "last_start_t_mono": handle.last_start_t_mono,
        "last_hb_t_wall": handle.last_hb_t_wall,
        "last_hb_t_mono": handle.last_hb_t_mono,
        "hb_age_s": hb_age_s,
        "last_exit_code": handle.last_exit_code,
        "restart_count": handle.restart_count,
        "last_restart_t_mono": handle.last_restart_t_mono,
        "last_error": handle.last_error,
        "heartbeat_endpoint": handle.heartbeat_endpoint,
        "process_data_endpoint": handle.process_data_endpoint,
        "rpc_endpoint": handle.rpc_endpoint,
        "registered": handle.rpc_endpoint is not None,
    }


def update_device_driver_exit_state(manager: Any, handle: Any, rc: int) -> None:
    handle.driver_last_exit_code = int(rc)
    handle.process = None
    handle.driver_pid = None
    if (
        str(handle.driver_process_state) == "STOPPING"
        and handle.driver_stop_requested_t_mono is not None
    ):
        handle.driver_process_state = _enum_member(handle.driver_process_state, "STOPPED")
        manager._publish_driver_event("manager.driver.stopped", handle)
        return
    if rc == 0:
        handle.driver_process_state = _enum_member(handle.driver_process_state, "STOPPED")
        manager._publish_driver_event("manager.driver.exited", handle)
        return
    handle.driver_process_state = _enum_member(handle.driver_process_state, "FAILED")
    handle.driver_last_error = handle.driver_last_error or "driver exited"
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


def supervise_device_drivers(manager: Any, now_mono: float) -> None:
    for device_id, handle in manager._devices.items():
        proc = handle.process
        if proc is not None:
            rc = proc.poll()
            if rc is not None:
                manager._update_device_driver_exit_state(handle, int(rc))
        manager._enforce_device_driver_stop_timeout(handle, now_mono)
        manager._maybe_restart_device_driver(device_id, handle, now_mono)


def update_managed_process_exit_state(manager: Any, handle: Any, rc: int) -> bool:
    handle.last_exit_code = int(rc)
    handle.popen = None
    handle.pid = None
    handle.rpc_endpoint = None
    manager._close_process_rpc(handle)
    if str(handle.state) == "STOPPING":
        handle.state = _enum_member(handle.state, "EXITED")
        manager._publish_process_event("manager.process.exited", handle)
        return False
    if rc == 0:
        handle.state = _enum_member(handle.state, "STOPPED")
        manager._publish_process_event("manager.process.exited", handle)
        return False
    if manager._maybe_recover_process_start_collision(handle):
        return True
    handle.state = _enum_member(handle.state, "FAILED")
    handle.last_error = handle.last_error or "process exited"
    manager._publish_process_event("manager.process.failed", handle)
    return False


def enforce_managed_process_heartbeat_timeout(
    manager: Any,
    handle: Any,
    now_mono: float,
) -> None:
    if str(handle.state) not in {"STARTING", "RUNNING"}:
        return
    hb_age: float | None = None
    if handle.last_hb_t_mono is not None:
        hb_age = now_mono - handle.last_hb_t_mono
    elif handle.last_start_t_mono is not None:
        hb_age = now_mono - handle.last_start_t_mono
    if hb_age is None or hb_age <= handle.spec.heartbeat_timeout_s:
        return
    handle.state = _enum_member(handle.state, "FAILED")
    handle.last_error = "heartbeat stale"
    if handle.popen is not None and handle.popen.poll() is None:
        try:
            handle.popen.terminate()
            handle.stop_requested_t_mono = now_mono
        except Exception as exc:
            handle.last_error = f"heartbeat stale; terminate failed: {exc}"
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
    try:
        handle.popen.kill()
    except Exception as exc:
        handle.last_error = str(exc)


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
