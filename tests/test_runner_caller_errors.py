from __future__ import annotations

import unittest
from types import SimpleNamespace

from experiment_control._driver.runner import DeviceRunner
from experiment_control.types import DeviceState


class _Dev:
    def set_freq(self, freq_hz: float, unit: str = "hz") -> float:
        return freq_hz

    def inner_type_error(self) -> None:
        raise TypeError("driver bug inside body")


def _runner() -> DeviceRunner:
    runner = object.__new__(DeviceRunner)
    runner.device_id = "d"
    runner._device = _Dev()
    runner._device_state = DeviceState.OK
    runner._device_reachable = True
    runner._last_error = None
    runner._action_failed_since_last_ok = False
    runner._last_ok_ts = None
    runner._stream_rpc = {}
    runner._members_cache = {}
    runner._now = lambda: SimpleNamespace(t_wall=1.0, t_mono=2.0)  # type: ignore[method-assign]
    return runner


class CallerErrorTests(unittest.TestCase):
    def _call(self, runner, action, params):
        return runner._handle_rpc_request(
            {"id": "1", "action": action, "params": params}
        )

    def _assert_healthy(self, runner):
        self.assertTrue(runner._device_reachable)
        self.assertEqual(runner._device_state, DeviceState.OK)
        self.assertFalse(runner._action_failed_since_last_ok)
        self.assertIsNone(runner._last_error)

    def test_unknown_command_does_not_demote(self) -> None:
        runner = _runner()
        resp = self._call(runner, "nope", {})
        self.assertEqual(resp["status"], "ERROR")
        self.assertIn("Unknown command", resp["error"])
        self._assert_healthy(runner)

    def test_bad_parameters_do_not_demote_and_list_accepted(self) -> None:
        runner = _runner()
        for params in ({"wrong": 1}, {}):
            resp = self._call(runner, "set_freq", params)
            self.assertEqual(resp["status"], "ERROR")
            self.assertIn("Bad parameters", resp["error"])
            self.assertIn("freq_hz, unit", resp["error"])
            self._assert_healthy(runner)

    def test_type_error_inside_driver_still_demotes(self) -> None:
        runner = _runner()
        resp = self._call(runner, "inner_type_error", {})
        self.assertEqual(resp["status"], "ERROR")
        self.assertFalse(runner._device_reachable)
        self.assertEqual(runner._device_state, DeviceState.DEGRADED)

    def test_valid_call_works(self) -> None:
        runner = _runner()
        resp = self._call(runner, "set_freq", {"freq_hz": 3.0})
        self.assertEqual(resp["result"], 3.0)


if __name__ == "__main__":
    unittest.main()
