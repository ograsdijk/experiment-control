from __future__ import annotations

import time
from typing import Any

from . import process_supervision as _process_supervision


Json = dict[str, Any]


def _episode_generation(handle: Any) -> tuple[Any, Any]:
    """Return a cheap identity for the currently running driver generation."""
    return (
        getattr(handle, "driver_popen_pid", None),
        getattr(handle, "driver_running_since_mono", None),
    )


def _clear_stale_episode(handle: Any) -> None:
    handle.driver_heartbeat_stale_since_mono = None
    handle.driver_heartbeat_stale_max_age_s = None
    handle.driver_heartbeat_stale_generation = None


def _publish_soft_event(
    manager: Any,
    topic: str,
    *,
    handle: Any,
    age_s: float,
    timeout_s: float,
    hard_timeout_s: float,
    now_mono: float,
    extra: Json | None = None,
) -> None:
    payload: Json = {
        "version": 1,
        "device_id": handle.spec.device_id,
        "heartbeat_age_s": float(age_s),
        "heartbeat_timeout_s": float(timeout_s),
        "heartbeat_hard_timeout_s": float(hard_timeout_s),
        "pid": getattr(handle, "driver_pid", None),
        "popen_pid": getattr(handle, "driver_popen_pid", None),
        "heartbeat_pid": getattr(handle, "driver_heartbeat_pid", None),
        "driver_process_state": str(getattr(handle, "driver_process_state", "")),
        "ts": {"t_wall": time.time(), "t_mono": now_mono},
    }
    hb = getattr(handle, "last_hb", None)
    current_operation = getattr(hb, "current_operation", None)
    operation_started = getattr(hb, "current_operation_started_mono", None)
    if current_operation is not None:
        payload["current_operation"] = current_operation
    if isinstance(operation_started, (int, float)):
        payload["current_operation_started_mono"] = float(operation_started)
        payload["current_operation_age_s"] = max(
            0.0, now_mono - float(operation_started)
        )
    if extra:
        payload.update(extra)
    manager._publish_manager_event(topic, payload)


def _legacy_enforce_device_driver_heartbeat_timeout(
    manager: Any,
    handle: Any,
    now_mono: float,
) -> None:
    """Legacy fallback for lightweight callers without manager-event support.

    Production Manager always exposes ``_publish_manager_event``. A handful of
    tests and downstream utility stubs historically call the supervision helper
    directly with a minimal SimpleNamespace; keep their old one-threshold
    behavior instead of forcing every external stub to implement the new soft
    stale event contract at once.
    """
    if str(handle.driver_process_state) != "RUNNING":
        return
    hb = handle.last_hb_recv_mono
    running_since = handle.driver_running_since_mono
    heartbeat_from_current_run = hb is not None and (
        running_since is None or hb >= running_since
    )
    ref = hb if heartbeat_from_current_run else running_since
    if ref is None:
        return
    age = now_mono - ref
    timeout_s = float(manager._heartbeat_timeout_s)
    if age <= timeout_s:
        return
    hard_timeout_s = float(
        getattr(manager, "_heartbeat_hard_timeout_s", timeout_s * 3.0)
    )
    if age < hard_timeout_s and (
        _process_supervision._in_startup_grace(manager, now_mono)
        or _process_supervision._recent_manager_loop_stall(manager, now_mono)
    ):
        return

    handle.driver_process_state = _process_supervision._enum_member(
        handle.driver_process_state, "FAILED"
    )
    handle.driver_running_since_mono = None
    handle.driver_stop_requested_t_mono = now_mono
    handle.driver_last_error_kind = "heartbeat_stale"
    if not handle.driver_last_error:
        if not heartbeat_from_current_run:
            handle.driver_last_error = (
                f"driver RUNNING but no heartbeat {age:.1f}s after registering "
                f"(timeout {timeout_s:.1f}s)"
            )
        else:
            handle.driver_last_error = (
                f"driver heartbeat stale ({age:.1f}s > {timeout_s:.1f}s)"
            )
    proc = handle.process
    if proc is not None:
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
    manager._publish_driver_event("manager.driver.failed", handle)


def enforce_device_driver_heartbeat_timeout(
    manager: Any,
    handle: Any,
    now_mono: float,
) -> None:
    """Stage device-driver heartbeat handling into soft stale and hard failure.

    The driver heartbeat intentionally runs on the same loop as device I/O, so a
    stale heartbeat is useful evidence that communication or another driver
    operation is blocked. Crossing the normal heartbeat timeout therefore marks
    a *soft* stale episode but leaves the process RUNNING. Only the explicit
    manager/per-device hard timeout is allowed to terminate the wrapper.

    This keeps the diagnostic value of the heartbeat without turning a brief
    multi-second hardware call into an immediate driver crash.
    """
    if not hasattr(manager, "_publish_manager_event"):
        _legacy_enforce_device_driver_heartbeat_timeout(manager, handle, now_mono)
        return

    if str(handle.driver_process_state) != "RUNNING":
        return

    hb = handle.last_hb_recv_mono
    running_since = handle.driver_running_since_mono
    heartbeat_from_current_run = hb is not None and (
        running_since is None or hb >= running_since
    )
    ref = hb if heartbeat_from_current_run else running_since
    if ref is None:
        return

    generation = _episode_generation(handle)
    stale_since = getattr(handle, "driver_heartbeat_stale_since_mono", None)
    stale_generation = getattr(handle, "driver_heartbeat_stale_generation", None)
    if stale_since is not None and stale_generation != generation:
        # A restart/new registration began while the previous generation was
        # stale. Do not report that as a recovery of the old blocked call.
        _clear_stale_episode(handle)
        stale_since = None

    age = max(0.0, now_mono - ref)
    timeout_s = float(manager._heartbeat_timeout_s)
    configured_hard_timeout_s = getattr(
        handle.spec, "driver_heartbeat_hard_timeout_s", None
    )
    hard_timeout_s = float(
        configured_hard_timeout_s
        if configured_hard_timeout_s is not None
        else manager._heartbeat_hard_timeout_s
    )

    if age <= timeout_s:
        if stale_since is not None:
            max_age_s = getattr(handle, "driver_heartbeat_stale_max_age_s", None)
            _publish_soft_event(
                manager,
                "manager.driver.heartbeat_recovered",
                handle=handle,
                age_s=age,
                timeout_s=timeout_s,
                hard_timeout_s=hard_timeout_s,
                now_mono=now_mono,
                extra={
                    "stale_duration_s": max(0.0, now_mono - float(stale_since)),
                    "max_heartbeat_age_s": (
                        float(max_age_s) if max_age_s is not None else None
                    ),
                },
            )
            _clear_stale_episode(handle)
        return

    if stale_since is None:
        handle.driver_heartbeat_stale_since_mono = now_mono
        handle.driver_heartbeat_stale_max_age_s = float(age)
        handle.driver_heartbeat_stale_generation = generation
        _publish_soft_event(
            manager,
            "manager.driver.heartbeat_stale",
            handle=handle,
            age_s=age,
            timeout_s=timeout_s,
            hard_timeout_s=hard_timeout_s,
            now_mono=now_mono,
            extra={
                "heartbeat_received": bool(heartbeat_from_current_run),
                "recent_manager_loop_stall": bool(
                    _process_supervision._recent_manager_loop_stall(
                        manager, now_mono
                    )
                ),
                "in_startup_grace": bool(
                    _process_supervision._in_startup_grace(manager, now_mono)
                ),
            },
        )
    else:
        previous_max = getattr(handle, "driver_heartbeat_stale_max_age_s", None)
        handle.driver_heartbeat_stale_max_age_s = max(
            float(age), float(previous_max or 0.0)
        )

    if age < hard_timeout_s:
        # Soft stale: the wrapper/real driver still exists, and the whole point
        # of this interval is to let a blocked hardware call return on its own.
        return

    handle.driver_process_state = _process_supervision._enum_member(
        handle.driver_process_state, "FAILED"
    )
    handle.driver_running_since_mono = None
    handle.driver_stop_requested_t_mono = now_mono
    # Keep the historical error kind so update_device_driver_exit_state() and
    # stop-timeout escalation retain their existing behavior.
    handle.driver_last_error_kind = "heartbeat_stale"
    handle.driver_last_error = (
        f"driver heartbeat remained stale for {age:.1f}s "
        f"(soft timeout {timeout_s:.1f}s, hard timeout {hard_timeout_s:.1f}s)"
    )

    proc = handle.process
    if proc is not None:
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
    manager._publish_driver_event("manager.driver.failed", handle)
    if bool(getattr(handle.spec, "driver_restart_on_heartbeat_timeout", False)):
        handle.driver_next_restart_t_mono = (
            now_mono + float(handle.spec.driver_restart_backoff_s)
        )
        manager._publish_driver_event("manager.driver.restart_scheduled", handle)
