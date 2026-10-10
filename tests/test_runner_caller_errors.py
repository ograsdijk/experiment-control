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

    def test_unexpected_parameter_is_named_with_suggestion(self) -> None:
        runner = _runner()
        resp = self._call(runner, "set_freq", {"freq": 1.0})
        self.assertEqual(resp["error_code"], "bad_parameters")
        self.assertIn("unexpected parameter 'freq' (did you mean 'freq_hz'?)", resp["error"])
        self.assertIn("accepted parameters: freq_hz, unit", resp["error"])
        self._assert_healthy(runner)

    def test_missing_parameter_keeps_bind_message(self) -> None:
        resp = self._call(_runner(), "set_freq", {})
        self.assertIn("missing a required argument: 'freq_hz'", resp["error"])

    def test_caller_errors_carry_codes(self) -> None:
        resp = self._call(_runner(), "nope", {})
        self.assertEqual(resp["error_code"], "unknown_command")
        resp = self._call(_runner(), "_private", {})
        self.assertEqual(resp["error_code"], "unknown_command")

    def test_connect_over_rpc_is_a_caller_error(self) -> None:
        runner = _runner()
        for action in ("connect", "disconnect"):
            resp = self._call(runner, action, {})
            self.assertEqual(resp["error_code"], "command_not_allowed")
            self.assertIn("not allowed via RPC", resp["error"])
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
