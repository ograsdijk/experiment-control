from __future__ import annotations

import math
import unittest
from types import SimpleNamespace

from experiment_control._driver.runner import DeviceRunner
from experiment_control.types import DeviceState


class InvalidCommandParameterTests(unittest.TestCase):
    def _runner(self) -> tuple[DeviceRunner, list[tuple[str, dict[str, object]]]]:
        runner = object.__new__(DeviceRunner)
        runner.device_id = "healthy"
        runner._device = object()
        runner._device_state = DeviceState.OK
        runner._device_reachable = True
        runner._last_error = None
        runner._action_failed_since_last_ok = False
        runner._last_ok_ts = None
        runner._stream_rpc = {}
        runner._members_cache = {
            "set_power": SimpleNamespace(
                kind="method",
                params=[SimpleNamespace(name="dbm", annotation="float")],
            )
        }
        calls: list[tuple[str, dict[str, object]]] = []

        def handle_command(action: str, params: dict[str, object]) -> None:
            calls.append((action, params))

        runner.handle_command = handle_command  # type: ignore[method-assign]
        runner._now = lambda: SimpleNamespace(t_wall=1.0, t_mono=2.0)  # type: ignore[method-assign]
        return runner, calls

    def test_non_finite_float_rejected_before_device_health_boundary(self) -> None:
        for raw in ("nan", "inf", "-inf", math.nan, math.inf, -math.inf):
            with self.subTest(raw=raw):
                runner, calls = self._runner()
                with self.assertRaisesRegex(TypeError, "non-finite"):
                    runner._rpc_dispatch_device_command(
                        {"id": "bad", "action": "set_power", "params": {"dbm": raw}}
                    )
                self.assertEqual(calls, [])
                self.assertTrue(runner._device_reachable)
                self.assertEqual(runner._device_state, DeviceState.OK)
                self.assertFalse(runner._action_failed_since_last_ok)

    def test_finite_float_still_dispatches(self) -> None:
        runner, calls = self._runner()
        response = runner._rpc_dispatch_device_command(
            {"id": "ok", "action": "set_power", "params": {"dbm": "-12.5"}}
        )
        self.assertEqual(response["status"], "OK")
        self.assertEqual(calls, [("set_power", {"dbm": -12.5})])
        self.assertTrue(runner._device_reachable)
        self.assertEqual(runner._device_state, DeviceState.OK)


if __name__ == "__main__":
    unittest.main()
