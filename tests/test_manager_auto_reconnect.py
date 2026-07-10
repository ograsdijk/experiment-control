# ruff: noqa: E402

import queue
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager import (
    AutoReconnectSpec,
    DeviceHandle,
    DeviceSpec,
    ManagedProcessState,
    Manager,
    device_spec_from_yaml,
)
from experiment_control._manager.process_supervision import (
    _maybe_auto_reconnect_device,
    _run_auto_reconnect,
)
from experiment_control.types import Timestamp


class ManagerAutoReconnectTests(unittest.TestCase):
    def test_device_spec_parses_auto_reconnect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "device.yaml"
            path.write_text(
                textwrap.dedent(
                    """
                    version: 1
                    device_id: pt415
                    driver:
                      file: devices/drivers/cpa1110_driver.py
                      class_name: CPA1110Device
                    init_kwargs: {}
                    auto_reconnect:
                      enabled: true
                      on_telemetry_stale_s: 5.0
                      cooldown_s: 30.0
                      max_attempts: 3
                      reset_attempts_after_ok_s: 120.0
                      disconnect_timeout_ms: 1000
                    telemetry_calls: []
                    """
                ),
                encoding="utf-8",
            )

            spec = device_spec_from_yaml(path)

        self.assertTrue(spec.auto_reconnect.enabled)
        self.assertEqual(spec.auto_reconnect.on_telemetry_stale_s, 5.0)
        self.assertEqual(spec.auto_reconnect.max_attempts, 3)
        self.assertEqual(spec.auto_reconnect.disconnect_timeout_ms, 1000)

    def test_auto_reconnect_attempts_disconnect_then_connect_on_stale_telemetry(self) -> None:
        spec = DeviceSpec(
            device_id="pt415",
            device_class_path="driver.py",
            device_class_name="Driver",
            device_init_kwargs={},
            telemetry_calls=[],
            auto_reconnect=device_spec_from_yaml(
                self._write_auto_reconnect_device_yaml()
            ).auto_reconnect,
        )
        handle = DeviceHandle(spec=spec)
        handle.driver_process_state = ManagedProcessState.RUNNING
        handle.rpc_endpoint = "tcp://127.0.0.1:1"
        manager = mock.Mock()
        manager._devices = {"pt415": handle}
        manager._telemetry_last_bundle_ts = {"pt415": Timestamp(t_wall=1.0, t_mono=10.0)}
        manager._device_rpc_timeout_ms = 2000
        manager._call_device_rpc.return_value = {"status": "OK"}
        manager._device_rpc_status_ok = Manager._device_rpc_status_ok
        manager._device_rpc_error_text = Manager._device_rpc_error_text
        # The reconnect I/O is dispatched to the lifecycle executor (F4) so
        # it can't block the manager's main poll loop; run it inline here
        # (synchronously, on this thread) so the outcome assertions below
        # keep exercising the disconnect->connect->success behavior. A
        # dedicated test class further down asserts the dispatch itself is
        # non-blocking / off-thread.
        manager._lifecycle_executor.submit.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
        manager._lifecycle_device_locks = {}

        _maybe_auto_reconnect_device(manager, "pt415", handle, 20.0)

        self.assertEqual(handle.auto_reconnect_attempts, 1)
        self.assertEqual(
            [call.kwargs["action"] for call in manager._call_device_rpc.call_args_list],
            ["disconnect_device", "connect_device"],
        )
        manager._publish_manager_event.assert_any_call(
            "manager.device.auto_reconnect.attempt",
            mock.ANY,
        )
        manager._publish_manager_event.assert_any_call(
            "manager.device.auto_reconnect.success",
            mock.ANY,
        )

    def test_auto_reconnect_suppresses_after_max_attempts(self) -> None:
        spec = DeviceSpec(
            device_id="pt415",
            device_class_path="driver.py",
            device_class_name="Driver",
            device_init_kwargs={},
            telemetry_calls=[],
            auto_reconnect=device_spec_from_yaml(
                self._write_auto_reconnect_device_yaml()
            ).auto_reconnect,
        )
        handle = DeviceHandle(spec=spec)
        handle.driver_process_state = ManagedProcessState.RUNNING
        handle.rpc_endpoint = "tcp://127.0.0.1:1"
        handle.auto_reconnect_attempts = 3
        manager = mock.Mock()
        manager._telemetry_last_bundle_ts = {"pt415": Timestamp(t_wall=1.0, t_mono=10.0)}

        _maybe_auto_reconnect_device(manager, "pt415", handle, 20.0)

        manager._call_device_rpc.assert_not_called()
        self.assertTrue(handle.auto_reconnect_suppressed)
        manager._publish_manager_event.assert_called_once()
        self.assertEqual(
            manager._publish_manager_event.call_args.args[0],
            "manager.device.auto_reconnect.suppressed",
        )

    def test_auto_reconnect_resets_after_healthy_period(self) -> None:
        spec = DeviceSpec(
            device_id="pt415",
            device_class_path="driver.py",
            device_class_name="Driver",
            device_init_kwargs={},
            telemetry_calls=[],
            auto_reconnect=device_spec_from_yaml(
                self._write_auto_reconnect_device_yaml()
            ).auto_reconnect,
        )
        handle = DeviceHandle(spec=spec)
        handle.auto_reconnect_attempts = 2
        handle.auto_reconnect_healthy_since_mono = 100.0
        handle.auto_reconnect_suppressed = True
        handle.auto_reconnect_last_error = "failed"
        manager = mock.Mock()
        manager._telemetry_last_bundle_ts = {"pt415": Timestamp(t_wall=1.0, t_mono=219.0)}

        _maybe_auto_reconnect_device(manager, "pt415", handle, 220.0)

        self.assertEqual(handle.auto_reconnect_attempts, 0)
        self.assertFalse(handle.auto_reconnect_suppressed)
        self.assertIsNone(handle.auto_reconnect_last_error)
        manager._publish_manager_event.assert_called_once()
        self.assertEqual(
            manager._publish_manager_event.call_args.args[0],
            "manager.device.auto_reconnect.reset",
        )

    @staticmethod
    def _write_auto_reconnect_device_yaml() -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
        path = Path(tmp.name)
        with tmp:
            tmp.write(
                textwrap.dedent(
                    """
                    version: 1
                    device_id: pt415
                    driver:
                      file: driver.py
                      class_name: Driver
                    init_kwargs: {}
                    auto_reconnect:
                      enabled: true
                      on_telemetry_stale_s: 5.0
                      cooldown_s: 30.0
                      max_attempts: 3
                      reset_attempts_after_ok_s: 120.0
                      disconnect_timeout_ms: 1000
                    telemetry_calls: []
                    """
                )
            )
        return path


def _make_reconnect_handle(*, disconnect_timeout_ms: int = 1000) -> DeviceHandle:
    spec = DeviceSpec(
        device_id="pt415",
        device_class_path="driver.py",
        device_class_name="Driver",
        device_init_kwargs={},
        telemetry_calls=[],
        auto_reconnect=AutoReconnectSpec(
            enabled=True,
            on_telemetry_stale_s=5.0,
            cooldown_s=30.0,
            max_attempts=None,
            reset_attempts_after_ok_s=120.0,
            disconnect_timeout_ms=disconnect_timeout_ms,
        ),
    )
    handle = DeviceHandle(spec=spec)
    handle.driver_process_state = ManagedProcessState.RUNNING
    handle.rpc_endpoint = "tcp://127.0.0.1:1"
    return handle


class ManagerAutoReconnectOffLoopTests(unittest.TestCase):
    """F4: auto-reconnect's disconnect+connect I/O must run on the lifecycle
    executor, not inline on the manager's main poll loop, so a slow/stale
    device can't stall `_pump_once` for the ~2.5s worst case (1000ms
    disconnect timeout + 1500ms connect timeout)."""

    def test_maybe_auto_reconnect_device_does_not_call_rpc_inline(self) -> None:
        """The calling thread (the main poll loop, in production) must
        return from `_maybe_auto_reconnect_device` without itself having
        invoked any device RPC — the work has to be handed to the executor
        instead of executed synchronously."""
        handle = _make_reconnect_handle()
        manager = mock.Mock()
        manager._devices = {"pt415": handle}
        manager._telemetry_last_bundle_ts = {"pt415": Timestamp(t_wall=1.0, t_mono=10.0)}
        manager._device_rpc_timeout_ms = 2000
        # Deliberately do NOT wire submit.side_effect to run inline — a
        # plain Mock().submit(...) just records the call, proving nothing
        # ran on this thread.

        _maybe_auto_reconnect_device(manager, "pt415", handle, 20.0)

        manager._call_device_rpc.assert_not_called()
        manager._lifecycle_executor.submit.assert_called_once()
        submitted_fn = manager._lifecycle_executor.submit.call_args.args[0]
        self.assertIs(submitted_fn, _run_auto_reconnect)
        # Bookkeeping (cooldown-relevant state) still happens synchronously
        # on the caller's thread, so a second tick during cooldown won't
        # double-dispatch.
        self.assertEqual(handle.auto_reconnect_attempts, 1)
        self.assertEqual(handle.auto_reconnect_last_attempt_mono, 20.0)

    def test_dispatch_does_not_block_on_slow_device_io(self) -> None:
        """End-to-end with a real ThreadPoolExecutor: a slow fake
        disconnect/connect RPC must not block the thread that calls
        `_maybe_auto_reconnect_device` (the manager main loop stand-in)."""
        handle = _make_reconnect_handle()
        rpc_delay_s = 0.3
        started = threading.Event()

        def _slow_call_device_rpc(*, device_id, action, params, timeout_ms):
            del device_id, params, timeout_ms
            if action == "connect_device":
                started.set()
                time.sleep(rpc_delay_s)
            return {"status": "OK"}

        manager = mock.Mock()
        manager._devices = {"pt415": handle}
        manager._telemetry_last_bundle_ts = {"pt415": Timestamp(t_wall=1.0, t_mono=10.0)}
        manager._device_rpc_timeout_ms = 2000
        manager._call_device_rpc = _slow_call_device_rpc
        manager._device_rpc_status_ok = Manager._device_rpc_status_ok
        manager._device_rpc_error_text = Manager._device_rpc_error_text
        manager._lifecycle_device_locks = {}
        manager._lifecycle_executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="test-auto-reconnect"
        )
        events: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        manager._publish_manager_event = lambda topic, payload: events.put((topic, payload))

        try:
            t0 = time.monotonic()
            _maybe_auto_reconnect_device(manager, "pt415", handle, 20.0)
            call_elapsed_s = time.monotonic() - t0

            self.assertLess(
                call_elapsed_s,
                rpc_delay_s / 2,
                msg=(
                    f"_maybe_auto_reconnect_device blocked for "
                    f"{call_elapsed_s:.3f}s; expected to return well "
                    f"before the {rpc_delay_s:.3f}s fake connect RPC completes"
                ),
            )
            self.assertTrue(
                started.wait(timeout=2.0), msg="worker never started the connect RPC"
            )

            topics: list[str] = []
            deadline = time.monotonic() + 5.0
            while "manager.device.auto_reconnect.success" not in topics:
                if time.monotonic() > deadline:
                    self.fail(f"reconnect success event never arrived; saw {topics}")
                try:
                    topic, _payload = events.get(timeout=0.1)
                except queue.Empty:
                    continue
                topics.append(topic)
            self.assertIn("manager.device.auto_reconnect.success", topics)
            self.assertEqual(handle.auto_reconnect_attempts, 1)
        finally:
            manager._lifecycle_executor.shutdown(wait=True, cancel_futures=True)

    def test_reconnect_serialises_with_concurrent_operator_lifecycle_op(self) -> None:
        """The per-device lifecycle lock must still serialise an
        auto-reconnect attempt against a concurrent operator-initiated
        lifecycle op on the same device (e.g. a manual device.connect run
        via `Manager._run_lifecycle`), preventing overlapping connect
        attempts / double-connect races."""
        handle = _make_reconnect_handle()
        device_id = "pt415"
        lock = threading.Lock()
        lifecycle_device_locks = {device_id: lock}

        hold_s = 0.2
        overlap_detected = threading.Event()
        active = {"count": 0}
        active_lock = threading.Lock()

        def _tracked_call_device_rpc(*, device_id, action, params, timeout_ms):
            del device_id, action, params, timeout_ms
            with active_lock:
                active["count"] += 1
                if active["count"] > 1:
                    overlap_detected.set()
            time.sleep(0.05)
            with active_lock:
                active["count"] -= 1
            return {"status": "OK"}

        manager = mock.Mock()
        manager._devices = {device_id: handle}
        manager._device_rpc_timeout_ms = 2000
        manager._call_device_rpc = _tracked_call_device_rpc
        manager._device_rpc_status_ok = Manager._device_rpc_status_ok
        manager._device_rpc_error_text = Manager._device_rpc_error_text
        manager._lifecycle_device_locks = lifecycle_device_locks
        manager._publish_manager_event = mock.Mock()

        def _operator_lifecycle_op() -> None:
            # Mirrors Manager._run_lifecycle's locking: same per-device
            # lock, held for the duration of the (simulated) operator RPC.
            with lifecycle_device_locks.setdefault(device_id, threading.Lock()):
                with active_lock:
                    active["count"] += 1
                    if active["count"] > 1:
                        overlap_detected.set()
                time.sleep(hold_s)
                with active_lock:
                    active["count"] -= 1

        operator_thread = threading.Thread(target=_operator_lifecycle_op)
        executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-reconnect-lock")
        try:
            operator_thread.start()
            time.sleep(0.02)  # let the operator op grab the lock first
            future = executor.submit(
                _run_auto_reconnect, manager, device_id, handle, 1, 99.0
            )
            operator_thread.join(timeout=5.0)
            future.result(timeout=5.0)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        self.assertFalse(
            operator_thread.is_alive(), msg="operator lifecycle thread never finished"
        )
        self.assertFalse(
            overlap_detected.is_set(),
            msg="auto-reconnect ran concurrently with the operator lifecycle op "
            "despite sharing the per-device lifecycle lock",
        )
        self.assertEqual(handle.auto_reconnect_last_error, None)


if __name__ == "__main__":
    unittest.main()
