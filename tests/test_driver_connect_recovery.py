# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.driver import DeviceRunner
from experiment_control.types import DeviceState, Timestamp


class DriverConnectRecoveryTests(unittest.TestCase):
    """Connecting a device that dropped unexpectedly (state DEGRADED/FAULT,
    never DISCONNECTED) must actually re-establish the link instead of
    short-circuiting with an `already_connected` no-op.
    """

    def _runner(self) -> DeviceRunner:
        runner = object.__new__(DeviceRunner)
        runner._device_state = DeviceState.OK
        runner._device_reachable = True
        runner._last_error = None
        runner._last_ok_ts = None
        runner._connect_called = False
        runner._action_failed_since_last_ok = False
        runner._now = lambda: Timestamp(t_wall=1.0, t_mono=2.0)  # type: ignore[method-assign]
        runner._refresh_capabilities_cache = lambda: None  # type: ignore[method-assign]
        # Record the order of connect/disconnect hardware calls.
        runner._calls = []  # type: ignore[attr-defined]
        runner.connect_device = lambda: runner._calls.append("connect")  # type: ignore[method-assign]
        runner.disconnect_device = lambda: runner._calls.append("disconnect")  # type: ignore[method-assign]
        return runner

    def test_connect_from_degraded_reconnects(self) -> None:
        runner = self._runner()
        runner._device_state = DeviceState.DEGRADED
        runner._device_reachable = False
        runner._action_failed_since_last_ok = True

        resp = runner._rpc_route_connect_device({"id": 1})

        self.assertEqual(resp["status"], "OK")
        # Best-effort disconnect before the real connect.
        self.assertEqual(runner._calls, ["disconnect", "connect"])
        self.assertEqual(runner._device_state, DeviceState.OK)
        self.assertTrue(runner._device_reachable)
        self.assertIsNone(runner._last_error)
        # Action-failure latch cleared so the next telemetry tick can promote.
        self.assertFalse(runner._action_failed_since_last_ok)

    def test_connect_from_fault_reconnects(self) -> None:
        runner = self._runner()
        runner._device_state = DeviceState.FAULT
        runner._device_reachable = False

        resp = runner._rpc_route_connect_device({"id": 2})

        self.assertEqual(resp["status"], "OK")
        self.assertEqual(runner._calls, ["disconnect", "connect"])
        self.assertEqual(runner._device_state, DeviceState.OK)

    def test_connect_from_disconnected_does_not_disconnect_first(self) -> None:
        runner = self._runner()
        runner._device_state = DeviceState.DISCONNECTED
        runner._device_reachable = False

        resp = runner._rpc_route_connect_device({"id": 3})

        self.assertEqual(resp["status"], "OK")
        # Nothing to close on a clean disconnect — connect only.
        self.assertEqual(runner._calls, ["connect"])
        self.assertEqual(runner._device_state, DeviceState.OK)

    def test_connect_when_healthy_reports_already_connected(self) -> None:
        runner = self._runner()
        runner._device_state = DeviceState.OK
        runner._device_reachable = True

        resp = runner._rpc_route_connect_device({"id": 4})

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "already_connected")
        # No hardware calls for a genuinely-connected device.
        self.assertEqual(runner._calls, [])

    def test_connect_when_ok_but_unreachable_reconnects(self) -> None:
        # OK state but reachability lost should still re-establish, not no-op.
        runner = self._runner()
        runner._device_state = DeviceState.OK
        runner._device_reachable = False

        resp = runner._rpc_route_connect_device({"id": 5})

        self.assertEqual(resp["status"], "OK")
        self.assertEqual(runner._calls, ["disconnect", "connect"])
        self.assertTrue(runner._device_reachable)

    def test_failing_disconnect_does_not_block_connect(self) -> None:
        runner = self._runner()
        runner._device_state = DeviceState.DEGRADED
        runner._device_reachable = False

        def _boom() -> None:
            runner._calls.append("disconnect-raised")
            raise RuntimeError("transport gone")

        runner.disconnect_device = _boom  # type: ignore[method-assign]

        resp = runner._rpc_route_connect_device({"id": 6})

        self.assertEqual(resp["status"], "OK")
        self.assertEqual(runner._calls, ["disconnect-raised", "connect"])
        self.assertEqual(runner._device_state, DeviceState.OK)


if __name__ == "__main__":
    unittest.main()
