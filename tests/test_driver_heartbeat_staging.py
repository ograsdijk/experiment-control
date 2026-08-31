from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from experiment_control._manager.process_supervision import (
    enforce_device_driver_heartbeat_timeout,
)


class _FakePopen:
    def __init__(self) -> None:
        self.terminated = False

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True


def _handle(*, last_hb_recv_mono: float, process: _FakePopen | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        spec=SimpleNamespace(
            device_id="dev",
            driver_heartbeat_hard_timeout_s=None,
            driver_restart_on_heartbeat_timeout=False,
            driver_restart_backoff_s=0.5,
        ),
        process=process,
        driver_process_state="RUNNING",
        driver_pid=123,
        driver_popen_pid=456,
        driver_heartbeat_pid=123,
        driver_running_since_mono=80.0,
        driver_stop_requested_t_mono=None,
        driver_last_error=None,
        driver_last_error_kind=None,
        last_hb_recv_mono=last_hb_recv_mono,
    )


def _manager() -> SimpleNamespace:
    return SimpleNamespace(
        _heartbeat_timeout_s=3.0,
        _heartbeat_hard_timeout_s=10.0,
        _last_loop_stall_mono=None,
        _manager_loop_stall_recent_s=3.0,
        _startup_sequence_active=False,
        _startup_sequence_complete_mono=None,
        _publish_manager_event=mock.Mock(),
        _publish_driver_event=mock.Mock(),
    )


def test_soft_stale_keeps_driver_running_and_does_not_terminate() -> None:
    popen = _FakePopen()
    handle = _handle(last_hb_recv_mono=96.5, process=popen)  # age 3.5 s
    manager = _manager()

    enforce_device_driver_heartbeat_timeout(manager, handle, 100.0)

    assert handle.driver_process_state == "RUNNING"
    assert popen.terminated is False
    assert handle.driver_last_error_kind is None
    manager._publish_driver_event.assert_not_called()
    manager._publish_manager_event.assert_called_once()
    topic, payload = manager._publish_manager_event.call_args.args
    assert topic == "manager.driver.heartbeat_stale"
    assert payload["heartbeat_timeout_s"] == 3.0
    assert payload["heartbeat_hard_timeout_s"] == 10.0


def test_soft_stale_event_is_emitted_once_per_episode() -> None:
    handle = _handle(last_hb_recv_mono=96.5, process=_FakePopen())
    manager = _manager()

    enforce_device_driver_heartbeat_timeout(manager, handle, 100.0)
    enforce_device_driver_heartbeat_timeout(manager, handle, 101.0)
    enforce_device_driver_heartbeat_timeout(manager, handle, 102.0)

    topics = [call.args[0] for call in manager._publish_manager_event.call_args_list]
    assert topics == ["manager.driver.heartbeat_stale"]
    assert handle.driver_process_state == "RUNNING"


def test_stale_driver_recovery_is_reported_without_restart() -> None:
    popen = _FakePopen()
    handle = _handle(last_hb_recv_mono=96.5, process=popen)
    manager = _manager()

    enforce_device_driver_heartbeat_timeout(manager, handle, 100.0)
    handle.last_hb_recv_mono = 100.2
    enforce_device_driver_heartbeat_timeout(manager, handle, 100.5)

    topics = [call.args[0] for call in manager._publish_manager_event.call_args_list]
    assert topics == [
        "manager.driver.heartbeat_stale",
        "manager.driver.heartbeat_recovered",
    ]
    recovery = manager._publish_manager_event.call_args_list[-1].args[1]
    assert recovery["max_heartbeat_age_s"] == 3.5
    assert handle.driver_process_state == "RUNNING"
    assert popen.terminated is False
    assert handle.driver_heartbeat_stale_since_mono is None


def test_hard_timeout_fails_and_terminates_driver() -> None:
    popen = _FakePopen()
    handle = _handle(last_hb_recv_mono=89.5, process=popen)  # age 10.5 s
    manager = _manager()

    enforce_device_driver_heartbeat_timeout(manager, handle, 100.0)

    assert handle.driver_process_state == "FAILED"
    assert handle.driver_last_error_kind == "heartbeat_stale"
    assert "hard timeout 10.0s" in handle.driver_last_error
    assert popen.terminated is True
    manager._publish_driver_event.assert_called_once_with(
        "manager.driver.failed", handle
    )


def test_stale_episode_does_not_leak_across_driver_generation() -> None:
    handle = _handle(last_hb_recv_mono=96.5, process=_FakePopen())
    manager = _manager()

    enforce_device_driver_heartbeat_timeout(manager, handle, 100.0)

    # Simulate a new driver generation before the next heartbeat check.
    handle.driver_popen_pid = 789
    handle.driver_running_since_mono = 101.0
    handle.last_hb_recv_mono = 101.2
    enforce_device_driver_heartbeat_timeout(manager, handle, 101.5)

    topics = [call.args[0] for call in manager._publish_manager_event.call_args_list]
    assert topics == ["manager.driver.heartbeat_stale"]
    assert handle.driver_heartbeat_stale_since_mono is None


def test_hard_timeout_can_schedule_automatic_restart() -> None:
    popen = _FakePopen()
    handle = _handle(last_hb_recv_mono=89.5, process=popen)
    handle.spec.driver_restart_on_heartbeat_timeout = True
    handle.spec.driver_restart_backoff_s = 2.0
    manager = _manager()

    enforce_device_driver_heartbeat_timeout(manager, handle, 100.0)

    assert handle.driver_process_state == "FAILED"
    assert handle.driver_next_restart_t_mono == 102.0
    topics = [call.args[0] for call in manager._publish_driver_event.call_args_list]
    assert topics == ["manager.driver.failed", "manager.driver.restart_scheduled"]


def test_soft_stale_event_reports_blocked_operation() -> None:
    handle = _handle(last_hb_recv_mono=96.5, process=_FakePopen())
    handle.last_hb = SimpleNamespace(
        current_operation="telemetry:read_flow_signal_sccm",
        current_operation_started_mono=96.4,
    )
    manager = _manager()

    enforce_device_driver_heartbeat_timeout(manager, handle, 100.0)

    payload = manager._publish_manager_event.call_args.args[1]
    assert payload["current_operation"] == "telemetry:read_flow_signal_sccm"
    assert abs(payload["current_operation_age_s"] - 3.6) < 1e-9
