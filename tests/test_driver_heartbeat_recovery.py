from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from experiment_control._manager.driver_heartbeat_policy import (
    enforce_device_driver_heartbeat_timeout,
)
from experiment_control._manager.process_supervision import (
    maybe_restart_device_driver,
    reset_driver_restart_budget_if_healthy,
    stop_driver,
)
from experiment_control.manager import Manager


class _LiveProcess:
    def __init__(self, pid: int = 200) -> None:
        self.pid = pid
        self.terminated = False

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True


def _heartbeat_manager() -> SimpleNamespace:
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


def _heartbeat_handle(*, reachable: bool) -> SimpleNamespace:
    return SimpleNamespace(
        spec=SimpleNamespace(
            device_id="dev",
            driver_heartbeat_hard_timeout_s=None,
            driver_restart_on_heartbeat_timeout=True,
            driver_restart_backoff_s=2.0,
        ),
        process=_LiveProcess(),
        driver_process_state="RUNNING",
        driver_pid=200,
        driver_popen_pid=200,
        driver_heartbeat_pid=200,
        driver_running_since_mono=80.0,
        driver_stop_requested_t_mono=None,
        driver_last_error=None,
        driver_last_error_kind=None,
        last_hb_recv_mono=89.0,
        last_hb=SimpleNamespace(
            device_reachable=reachable,
            current_operation=None,
            current_operation_started_mono=None,
        ),
    )


def _registration_manager(*, registration_generation: int) -> tuple[Manager, SimpleNamespace]:
    manager = object.__new__(Manager)
    handle = SimpleNamespace(
        process=_LiveProcess(pid=222),
        driver_generation=2,
        driver_registered_generation=None,
        driver_reconnect_generation=2,
        driver_reconnect_after_restart=True,
        driver_process_state="STARTING",
        driver_pid=None,
        driver_running_since_mono=None,
        last_hb_recv_mono=None,
        rpc_endpoint="tcp://127.0.0.1:6000",
        pub_endpoint="tcp://127.0.0.1:6001",
        capabilities=None,
        config_published=True,
    )
    manager._devices = {"dev": handle}
    manager._registry_rep = object()
    manager._sub = mock.Mock()
    manager._heartbeat_timeout_s = 3.0
    manager._auto_connect_on_register = False
    manager._recv_json = mock.Mock(
        return_value={
            "type": "register",
            "device_id": "dev",
            "rpc_endpoint": f"tcp://127.0.0.1:700{registration_generation}",
            "pub_endpoint": f"tcp://127.0.0.1:710{registration_generation}",
            "capabilities": {},
            "generation": registration_generation,
        }
    )
    manager._send_json = mock.Mock()
    manager._close_device_rpc = mock.Mock()
    manager._publish_manager_event = mock.Mock()
    manager._publish_driver_event = mock.Mock()
    manager._dispatch_auto_connect = mock.Mock()
    return manager, handle


class HeartbeatRecoveryTests(unittest.TestCase):
    def test_hard_timeout_remembers_only_previously_connected_device(self) -> None:
        for reachable in (True, False):
            with self.subTest(reachable=reachable):
                handle = _heartbeat_handle(reachable=reachable)
                enforce_device_driver_heartbeat_timeout(
                    _heartbeat_manager(), handle, 100.0
                )
                self.assertIs(handle.driver_reconnect_after_restart, reachable)
                self.assertTrue(handle.driver_restart_is_automatic)
                self.assertEqual(handle.driver_next_restart_t_mono, 102.0)

    def test_repeated_quick_automatic_failures_enter_crashloop(self) -> None:
        handle = SimpleNamespace(
            process=None,
            driver_process_state="FAILED",
            driver_next_restart_t_mono=1.0,
            driver_restart_is_automatic=True,
            driver_restart_count=0,
            driver_last_restart_t_mono=None,
            driver_restart_healthy_since_mono=None,
            spec=SimpleNamespace(driver_max_restarts=3),
        )
        manager = SimpleNamespace(
            _publish_driver_event=mock.Mock(), start_driver=mock.Mock()
        )

        for attempt in range(1, 4):
            maybe_restart_device_driver(manager, "dev", handle, float(attempt))
            self.assertEqual(handle.driver_restart_count, attempt)
            handle.driver_next_restart_t_mono = float(attempt + 1)

        maybe_restart_device_driver(manager, "dev", handle, 4.0)

        self.assertEqual(handle.driver_process_state, "CRASHLOOP")
        self.assertEqual(manager.start_driver.call_count, 3)

    def test_continuous_healthy_run_resets_automatic_failure_budget(self) -> None:
        handle = SimpleNamespace(
            driver_restart_count=2,
            driver_restart_healthy_since_mono=None,
            driver_process_state="RUNNING",
            driver_running_since_mono=90.0,
            last_hb_recv_mono=99.5,
            spec=SimpleNamespace(driver_restart_healthy_reset_s=300.0),
        )
        manager = SimpleNamespace(
            _heartbeat_timeout_s=3.0, _publish_driver_event=mock.Mock()
        )

        reset_driver_restart_budget_if_healthy(manager, handle, 100.0)
        handle.last_hb_recv_mono = 199.0
        reset_driver_restart_budget_if_healthy(manager, handle, 203.0)
        self.assertIsNone(handle.driver_restart_healthy_since_mono)

        handle.last_hb_recv_mono = 204.0
        reset_driver_restart_budget_if_healthy(manager, handle, 204.0)
        handle.last_hb_recv_mono = 503.5
        reset_driver_restart_budget_if_healthy(manager, handle, 504.0)

        self.assertEqual(handle.driver_restart_count, 0)
        manager._publish_driver_event.assert_called_once_with(
            "manager.driver.restart_budget_reset", handle
        )

    def test_manual_restart_does_not_consume_automatic_budget(self) -> None:
        handle = SimpleNamespace(
            process=None,
            driver_process_state="FAILED",
            driver_next_restart_t_mono=1.0,
            driver_restart_is_automatic=False,
            driver_restart_count=2,
            driver_last_restart_t_mono=None,
            driver_restart_healthy_since_mono=None,
            spec=SimpleNamespace(driver_max_restarts=3),
        )
        manager = SimpleNamespace(
            _publish_driver_event=mock.Mock(), start_driver=mock.Mock()
        )

        maybe_restart_device_driver(manager, "dev", handle, 2.0)

        self.assertEqual(handle.driver_restart_count, 2)
        manager.start_driver.assert_called_once_with("dev")

    def test_stop_cancels_restart_and_reconnect_intent(self) -> None:
        handle = SimpleNamespace(
            process=None,
            driver_pid=None,
            driver_process_state="FAILED",
            driver_next_restart_t_mono=50.0,
            driver_restart_is_automatic=True,
            driver_reconnect_after_restart=True,
            driver_reconnect_generation=4,
            rpc_endpoint=None,
            pub_endpoint=None,
            spec=SimpleNamespace(device_id="dev"),
        )
        manager = SimpleNamespace(
            _devices={"dev": handle},
            _heartbeat_timeout_s=3.0,
            _last_liveness={},
            _publish_manager_event=mock.Mock(),
            _publish_driver_event=mock.Mock(),
            _close_device_rpc=mock.Mock(),
        )

        stop_driver(manager, "dev")

        self.assertIsNone(handle.driver_next_restart_t_mono)
        self.assertFalse(handle.driver_restart_is_automatic)
        self.assertFalse(handle.driver_reconnect_after_restart)
        self.assertIsNone(handle.driver_reconnect_generation)

    def test_delayed_reconnect_task_cannot_connect_new_generation(self) -> None:
        manager = object.__new__(Manager)
        handle = SimpleNamespace(
            driver_generation=2,
            driver_registered_generation=2,
            driver_reconnect_generation=2,
            driver_reconnect_after_restart=True,
        )
        manager._devices = {"dev": handle}
        manager._lifecycle_device_locks = {"dev": threading.Lock()}
        manager.connect_device = mock.Mock(return_value={"status": "OK"})
        manager._publish_manager_event = mock.Mock()

        Manager._run_auto_connect(manager, "dev", 1, True)

        manager.connect_device.assert_not_called()
        self.assertTrue(handle.driver_reconnect_after_restart)

    def test_matching_recovery_generation_reconnects_once(self) -> None:
        manager = object.__new__(Manager)
        handle = SimpleNamespace(
            driver_generation=2,
            driver_registered_generation=2,
            driver_reconnect_generation=2,
            driver_reconnect_after_restart=True,
        )
        manager._devices = {"dev": handle}
        manager._lifecycle_device_locks = {"dev": threading.Lock()}
        manager.connect_device = mock.Mock(return_value={"status": "OK"})
        manager._publish_manager_event = mock.Mock()

        Manager._run_auto_connect(manager, "dev", 2, True)

        manager.connect_device.assert_called_once_with("dev")
        self.assertFalse(handle.driver_reconnect_after_restart)
        self.assertIsNone(handle.driver_reconnect_generation)

    def test_replacement_registration_dispatches_generation_guarded_reconnect(self) -> None:
        manager, handle = _registration_manager(registration_generation=2)

        Manager._handle_registry(manager)

        self.assertEqual(handle.driver_registered_generation, 2)
        manager._dispatch_auto_connect.assert_called_once_with(
            "dev", expected_generation=2, heartbeat_recovery=True
        )

    def test_old_registration_is_ignored_without_replacing_endpoints(self) -> None:
        manager, handle = _registration_manager(registration_generation=1)
        original_endpoints = (handle.rpc_endpoint, handle.pub_endpoint)

        Manager._handle_registry(manager)

        self.assertEqual((handle.rpc_endpoint, handle.pub_endpoint), original_endpoints)
        manager._dispatch_auto_connect.assert_not_called()
        manager._sub.connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
