# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.driver import DeviceRunner
from experiment_control.types import DeviceState, TelemetryQuality, Timestamp


class _FakePub:
    def __init__(self) -> None:
        self.sent: list[object] = []

    def send_multipart(self, parts: object) -> None:
        self.sent.append(parts)


class DriverTelemetryQualityStateTests(unittest.TestCase):
    def _runner(self) -> DeviceRunner:
        runner = object.__new__(DeviceRunner)
        runner._device_state = DeviceState.OK
        runner._device_reachable = True
        runner._last_error = None
        return runner

    def test_all_ok_sets_ok_and_clears_error(self) -> None:
        runner = self._runner()
        runner._device_state = DeviceState.DEGRADED

        runner._apply_telemetry_quality_state(
            {"x": {"quality": TelemetryQuality.OK}, "y": {"quality": "OK"}}
        )

        self.assertTrue(runner._device_reachable)
        self.assertEqual(runner._device_state, DeviceState.OK)
        self.assertIsNone(runner._last_error)

    def test_partial_bad_degrades_but_keeps_reachable(self) -> None:
        runner = self._runner()

        runner._apply_telemetry_quality_state(
            {"x": {"quality": TelemetryQuality.OK}, "y": {"quality": TelemetryQuality.BAD}}
        )

        self.assertTrue(runner._device_reachable)
        self.assertEqual(runner._device_state, DeviceState.DEGRADED)
        self.assertIn("partially degraded", str(runner._last_error))

    def test_all_bad_degrades_and_marks_unreachable(self) -> None:
        runner = self._runner()

        runner._apply_telemetry_quality_state(
            {"x": {"quality": TelemetryQuality.BAD}, "y": {"quality": TelemetryQuality.MISSING}}
        )

        self.assertFalse(runner._device_reachable)
        self.assertEqual(runner._device_state, DeviceState.DEGRADED)
        self.assertIn("no OK signals", str(runner._last_error))

    def test_empty_degrades_and_marks_unreachable(self) -> None:
        runner = self._runner()

        runner._apply_telemetry_quality_state({})

        self.assertFalse(runner._device_reachable)
        self.assertEqual(runner._device_state, DeviceState.DEGRADED)
        self.assertIn("no OK signals", str(runner._last_error))

    def test_empty_telemetry_is_ok_when_no_signals_configured(self) -> None:
        runner = self._runner()
        runner.device_id = "dev"
        runner._telemetry_seq = 0
        runner._device_state = DeviceState.DEGRADED
        runner._telemetry_last_call_errors = {}
        runner._now = lambda: Timestamp(t_wall=1.0, t_mono=2.0)  # type: ignore[method-assign]
        runner.read_telemetry = lambda: {}  # type: ignore[method-assign]
        runner.telemetry_signal_names = lambda: []  # type: ignore[method-assign]
        runner._ts_dict = lambda ts: {"t_wall": ts.t_wall, "t_mono": ts.t_mono}  # type: ignore[method-assign]
        runner._serialize_signals = lambda signals, *, bundle_ts: signals  # type: ignore[method-assign]
        runner.pub = _FakePub()

        runner._publish_telemetry()

        self.assertTrue(runner._device_reachable)
        self.assertEqual(runner._device_state, DeviceState.DEGRADED)
        self.assertIsNone(runner._last_error)
        self.assertEqual(len(runner.pub.sent), 1)


if __name__ == "__main__":
    unittest.main()
