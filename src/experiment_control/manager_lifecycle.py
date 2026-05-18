from __future__ import annotations

import time
from typing import Any


def _not_running_process_ids(manager: Any, running_state: Any) -> list[str]:
    return [pid for pid, h in manager._processes.items() if h.state != running_state]


def _missing_registered_devices(manager: Any) -> list[str]:
    return [k for k, h in manager._devices.items() if h.rpc_endpoint is None]


def _not_online_device_ids(manager: Any, *, driver_state_ok: Any) -> list[str]:
    not_online: list[str] = []
    for device_id, h in manager._devices.items():
        if h.last_hb is None:
            not_online.append(device_id)
            continue
        if (not h.last_hb.device_reachable) or (h.last_hb.driver_state != driver_state_ok):
            not_online.append(device_id)
    return not_online


def _all_online(manager: Any, *, driver_state_ok: Any) -> bool:
    for h in manager._devices.values():
        if h.last_hb is None:
            return False
        if (not h.last_hb.device_reachable) or (h.last_hb.driver_state != driver_state_ok):
            return False
    return True


def _wait_processes_running(
    manager: Any,
    *,
    deadline: float,
    poll_ms: int,
    managed_process_running: Any,
) -> None:
    while True:
        if time.monotonic() > deadline:
            not_running = _not_running_process_ids(manager, managed_process_running)
            manager._emit_log(
                severity="error",
                topic="manager.startup.process_timeout",
                message="Timed out waiting for processes RUNNING",
                source_kind="manager",
                source_id="manager",
                stream="event",
                payload={"not_running": not_running},
            )
            raise TimeoutError(f"Timed out waiting for processes RUNNING: {not_running}")
        manager._pump_once(poll_ms=poll_ms)
        all_running = all(
            h.state == managed_process_running for h in manager._processes.values()
        )
        if all_running:
            return


def _wait_registered(
    manager: Any,
    *,
    deadline: float,
    poll_ms: int,
) -> None:
    while not all(h.rpc_endpoint is not None for h in manager._devices.values()):
        if time.monotonic() > deadline:
            missing = _missing_registered_devices(manager)
            manager._emit_log(
                severity="error",
                topic="manager.startup.registration_timeout",
                message="Timed out waiting for registration",
                source_kind="manager",
                source_id="manager",
                stream="event",
                payload={"missing": missing},
            )
            raise TimeoutError(f"Timed out waiting for registration: {missing}")
        manager._pump_once(poll_ms=poll_ms)


def _wait_online(
    manager: Any,
    *,
    deadline: float,
    poll_ms: int,
    driver_state_ok: Any,
) -> None:
    while True:
        if time.monotonic() > deadline:
            not_online = _not_online_device_ids(manager, driver_state_ok=driver_state_ok)
            manager._emit_log(
                severity="error",
                topic="manager.startup.online_timeout",
                message="Timed out waiting for devices ONLINE",
                source_kind="manager",
                source_id="manager",
                stream="event",
                payload={"not_online": not_online},
            )
            raise TimeoutError(f"Timed out waiting for ONLINE devices: {not_online}")
        manager._pump_once(poll_ms=poll_ms)
        if _all_online(manager, driver_state_ok=driver_state_ok):
            return


def startup_sequence(
    manager: Any,
    *,
    start_drivers: bool = True,
    start_processes: bool = True,
    wait_processes_running: bool | None = None,
    connect: bool | None = None,
    wait_for_registered: bool = True,
    wait_for_online: bool = True,
    timeout_s: float = 10.0,
    poll_ms: int = 50,
    managed_process_running: Any,
    driver_state_ok: Any,
) -> None:
    manager._ensure_router_running(timeout_s=timeout_s, poll_ms=poll_ms)
    manager._federation_hub.activate()
    if wait_processes_running is None:
        wait_processes_running = start_processes
    if start_processes:
        manager.start_all_processes()
    deadline = time.monotonic() + timeout_s
    if wait_processes_running:
        _wait_processes_running(
            manager,
            deadline=deadline,
            poll_ms=poll_ms,
            managed_process_running=managed_process_running,
        )
    if start_drivers:
        manager.start_all_drivers()
    if wait_for_registered:
        _wait_registered(manager, deadline=deadline, poll_ms=poll_ms)
    do_connect = bool(connect) if connect is not None else False
    if do_connect:
        manager.connect_all_devices()
    if wait_for_online:
        _wait_online(
            manager,
            deadline=deadline,
            poll_ms=poll_ms,
            driver_state_ok=driver_state_ok,
        )


def _safe_call(cb) -> None:
    try:
        cb()
    except Exception:
        pass


def _safe_close_socket(sock: Any) -> None:
    try:
        sock.close(0)
    except Exception:
        pass


def shutdown_cleanup(manager: Any) -> None:
    # Stop lifecycle workers BEFORE we tear down sockets — any in-flight
    # worker may still call _publish_manager_event (queued) or have a
    # pending reply to push. Drain both queues afterwards so anything
    # produced during the cancel/wait window goes out on the still-open
    # sockets.
    executor = getattr(manager, "_lifecycle_executor", None)
    if executor is not None:
        _safe_call(lambda: executor.shutdown(wait=True, cancel_futures=True))
    _safe_call(lambda: manager._drain_lifecycle_replies())
    _safe_call(lambda: manager._drain_lifecycle_events())

    manager._federation_hub.close()
    for handle in manager._devices.values():
        _safe_call(lambda handle=handle: manager.stop_driver(handle.spec.device_id))
    for handle in manager._processes.values():
        _safe_call(lambda handle=handle: manager._stop_process_handle(handle))
    manager._drain_supervisor_logs(max_items=5000)
    manager._flush_stale_supervisor_blocks(force=True)
    journal = manager._command_journal
    if journal is not None:
        _safe_call(lambda: journal.close(timeout_s=2.0))
    manager._close_manager_log_sink_file()

    _safe_close_socket(manager._registry_rep)
    _safe_close_socket(manager._sub)
    _safe_close_socket(manager._process_hb_sub)
    _safe_close_socket(manager._process_data_sub)
    _safe_close_socket(manager._internal_rpc)
    _safe_close_socket(manager._external_pub)
    _safe_call(lambda: manager._ctx.term())
    _safe_call(lambda: manager._process_guard.close())
