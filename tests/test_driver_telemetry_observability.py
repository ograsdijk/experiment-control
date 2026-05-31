# ruff: noqa: E402
"""Driver telemetry observability — surfacing per-call exceptions.

Regression test for the CTC100 case documented in ISSUES.md:
DeviceRunner.read_telemetry caught exceptions from individual telemetry
calls and marked all of that call's outputs as quality=BAD, but the real
exception text was only ever stored on self._last_error (overwritten on
the next tick). The published telemetry bundle contained no diagnostic,
so devices looked partially-healthy in the UI while a bulk-temperature
call was failing every tick.

These tests pin the new behaviour:
* read_telemetry populates _telemetry_last_call_errors keyed by the
  failing call's method name, and exposes per-signal error text via
  each signal's `error` field in the returned dict.
* _publish_telemetry threads both into the published payload
  (call_errors at the bundle level, error per signal).
* Repeated identical exceptions are logged to stderr at most once per
  _telemetry_log_period_s seconds.
"""

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.driver import DeviceRunner, _TelemetryCallPlan, _TelemetryOutPlan
from experiment_control.types import DeviceState, TelemetryQuality, Timestamp


class _FakePub:
    def __init__(self) -> None:
        self.sent: list[list[bytes]] = []

    def send_multipart(self, parts: list[bytes]) -> None:
        self.sent.append(list(parts))


def _runner_with_plan(plan: list[_TelemetryCallPlan]) -> DeviceRunner:
    runner = object.__new__(DeviceRunner)
    runner.device_id = "test_dev"
    runner._telemetry_plan = plan
    runner._telemetry_last_call_errors = {}
    runner._telemetry_log_last_mono = {}
    runner._telemetry_log_period_s = 30.0
    runner._device_state = DeviceState.OK
    runner._device_reachable = True
    runner._last_error = None
    return runner


class ReadTelemetryCapturesCallErrorsTests(unittest.TestCase):
    def test_call_exception_is_bound_and_recorded(self) -> None:
        def boom(**kwargs: object) -> dict[str, float]:
            raise RuntimeError("hardware nack")

        out_plan = _TelemetryOutPlan(
            signal="temp_a", units="K", dtype="f8", extractor=lambda r: r["temp_a"]
        )
        plan = [
            _TelemetryCallPlan(
                func=boom,
                attr_name=None,
                kwargs={},
                outputs=[out_plan],
                method="read_temperatures",
            )
        ]
        runner = _runner_with_plan(plan)

        with patch("sys.stderr", new_callable=io.StringIO):
            signals = runner.read_telemetry()

        self.assertEqual(signals["temp_a"]["quality"], TelemetryQuality.BAD)
        # New: per-signal error captured in the signal dict.
        self.assertIn("error", signals["temp_a"])
        self.assertIn("hardware nack", signals["temp_a"]["error"])
        # New: bundle-level call_errors keyed by the method name.
        self.assertIn("read_temperatures", runner._telemetry_last_call_errors)
        self.assertIn(
            "hardware nack", runner._telemetry_last_call_errors["read_temperatures"]
        )

    def test_extractor_exception_is_recorded_per_signal_only(self) -> None:
        def good_call(**kwargs: object) -> dict[str, float]:
            return {"temp_a": 1.0}

        def broken_extractor(_r: object) -> float:
            raise KeyError("temp_b")

        out_a = _TelemetryOutPlan(
            signal="temp_a", units="K", dtype="f8", extractor=lambda r: r["temp_a"]
        )
        out_b = _TelemetryOutPlan(
            signal="temp_b", units="K", dtype="f8", extractor=broken_extractor
        )
        plan = [
            _TelemetryCallPlan(
                func=good_call,
                attr_name=None,
                kwargs={},
                outputs=[out_a, out_b],
                method="read_temperatures",
            )
        ]
        runner = _runner_with_plan(plan)

        signals = runner.read_telemetry()

        self.assertEqual(signals["temp_a"]["quality"], TelemetryQuality.OK)
        self.assertNotIn("error", signals["temp_a"])
        self.assertEqual(signals["temp_b"]["quality"], TelemetryQuality.BAD)
        self.assertIn("error", signals["temp_b"])
        self.assertIn("temp_b", signals["temp_b"]["error"])
        # Bulk call succeeded, so no call-level error; per-signal extractor
        # failures are exposed only via each signal's `error` field above.
        self.assertEqual(runner._telemetry_last_call_errors, {})

    def test_each_tick_resets_error_dicts(self) -> None:
        # Tick 1: failing call -> error captured. Tick 2: succeeding call
        # -> error dict cleared.
        calls = {"n": 0}

        def maybe_boom(**kwargs: object) -> dict[str, float]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first tick fails")
            return {"temp_a": 1.0}

        out_plan = _TelemetryOutPlan(
            signal="temp_a", units="K", dtype="f8", extractor=lambda r: r["temp_a"]
        )
        plan = [
            _TelemetryCallPlan(
                func=maybe_boom,
                attr_name=None,
                kwargs={},
                outputs=[out_plan],
                method="read_temperatures",
            )
        ]
        runner = _runner_with_plan(plan)

        with patch("sys.stderr", new_callable=io.StringIO):
            signals_first = runner.read_telemetry()
        self.assertIn("read_temperatures", runner._telemetry_last_call_errors)
        self.assertIn("error", signals_first["temp_a"])

        signals_second = runner.read_telemetry()
        self.assertEqual(runner._telemetry_last_call_errors, {})
        # Successful tick produces an OK signal with no `error` field.
        self.assertNotIn("error", signals_second["temp_a"])
        self.assertEqual(signals_second["temp_a"]["quality"], TelemetryQuality.OK)


class TelemetryStderrLoggingTests(unittest.TestCase):
    def test_repeated_identical_errors_logged_once_per_window(self) -> None:
        def boom(**kwargs: object) -> dict[str, float]:
            raise RuntimeError("nope")

        plan = [
            _TelemetryCallPlan(
                func=boom,
                attr_name=None,
                kwargs={},
                outputs=[
                    _TelemetryOutPlan(
                        signal="x",
                        units=None,
                        dtype="f8",
                        extractor=lambda r: r["x"],
                    )
                ],
                method="read_x",
            )
        ]
        runner = _runner_with_plan(plan)
        # Use a generous window; the second call within the window should
        # not log.
        runner._telemetry_log_period_s = 1000.0

        with patch("sys.stderr", new_callable=io.StringIO) as buf:
            runner.read_telemetry()
            runner.read_telemetry()
            runner.read_telemetry()
            captured = buf.getvalue()

        self.assertEqual(captured.count("telemetry call 'read_x' raised"), 1)
        self.assertIn("RuntimeError", captured)

    def test_different_exception_types_logged_independently(self) -> None:
        sequence: list[type[BaseException]] = [RuntimeError, ValueError, RuntimeError]
        seen = iter(sequence)

        def boom(**kwargs: object) -> dict[str, float]:
            raise next(seen)("boom")

        plan = [
            _TelemetryCallPlan(
                func=boom,
                attr_name=None,
                kwargs={},
                outputs=[
                    _TelemetryOutPlan(
                        signal="x", units=None, dtype="f8", extractor=lambda r: r["x"]
                    )
                ],
                method="read_x",
            )
        ]
        runner = _runner_with_plan(plan)
        runner._telemetry_log_period_s = 1000.0

        with patch("sys.stderr", new_callable=io.StringIO) as buf:
            runner.read_telemetry()  # RuntimeError -> log
            runner.read_telemetry()  # ValueError -> log (different key)
            runner.read_telemetry()  # RuntimeError again -> rate-limited
            captured = buf.getvalue()

        self.assertEqual(captured.count("read_x"), 2)
        self.assertIn("RuntimeError", captured)
        self.assertIn("ValueError", captured)


class PublishTelemetryThreadsErrorsTests(unittest.TestCase):
    def _publish_ready_runner(self) -> DeviceRunner:
        runner = object.__new__(DeviceRunner)
        runner.device_id = "dev"
        runner._telemetry_seq = 0
        runner._device_state = DeviceState.OK
        runner._device_reachable = True
        runner._last_error = None
        runner._last_ok_ts = None
        runner._telemetry_last_call_errors = {}
        runner._telemetry_log_last_mono = {}
        runner._telemetry_log_period_s = 30.0
        runner._now = lambda: Timestamp(t_wall=1.0, t_mono=2.0)  # type: ignore[method-assign]
        runner._ts_dict = lambda ts: {"t_wall": ts.t_wall, "t_mono": ts.t_mono}  # type: ignore[method-assign]
        runner.pub = _FakePub()
        return runner

    def test_payload_includes_call_errors_when_present(self) -> None:
        runner = self._publish_ready_runner()

        def fake_read() -> dict[str, dict[str, object]]:
            runner._telemetry_last_call_errors = {"read_x": "RuntimeError('boom')"}
            return {
                "x": {
                    "value": None,
                    "units": None,
                    "quality": TelemetryQuality.BAD,
                    "ts": None,
                    "error": "RuntimeError('boom')",
                }
            }

        runner.read_telemetry = fake_read  # type: ignore[method-assign]
        runner.telemetry_signal_names = lambda: ["x"]  # type: ignore[method-assign]
        # Don't degrade the test runner state via the quality applier; it
        # would force-set _last_error, which is unrelated to this test.
        runner._apply_telemetry_quality_state = lambda _s: None  # type: ignore[method-assign]

        runner._publish_telemetry()

        import json as _json
        self.assertEqual(len(runner.pub.sent), 1)
        body = _json.loads(runner.pub.sent[0][1].decode())
        self.assertEqual(body["call_errors"], {"read_x": "RuntimeError('boom')"})
        self.assertEqual(body["signals"]["x"]["error"], "RuntimeError('boom')")
        self.assertEqual(body["signals"]["x"]["quality"], "BAD")

    def test_payload_omits_call_errors_when_clean(self) -> None:
        runner = self._publish_ready_runner()
        runner.read_telemetry = lambda: {  # type: ignore[method-assign]
            "x": {
                "value": 1.0,
                "units": "V",
                "quality": TelemetryQuality.OK,
                "ts": None,
            }
        }
        runner.telemetry_signal_names = lambda: ["x"]  # type: ignore[method-assign]
        runner._apply_telemetry_quality_state = lambda _s: None  # type: ignore[method-assign]

        runner._publish_telemetry()

        import json as _json
        body = _json.loads(runner.pub.sent[0][1].decode())
        self.assertNotIn("call_errors", body)
        self.assertNotIn("error", body["signals"]["x"])

    def test_outer_exception_is_recorded_under_synthetic_key(self) -> None:
        runner = self._publish_ready_runner()

        def explode() -> dict[str, dict[str, object]]:
            raise RuntimeError("read_telemetry blew up")

        runner.read_telemetry = explode  # type: ignore[method-assign]
        runner.telemetry_signal_names = lambda: ["x"]  # type: ignore[method-assign]

        with patch("sys.stderr", new_callable=io.StringIO):
            runner._publish_telemetry()

        import json as _json
        body = _json.loads(runner.pub.sent[0][1].decode())
        self.assertIn("call_errors", body)
        self.assertIn("<read_telemetry>", body["call_errors"])
        self.assertIn("read_telemetry blew up", body["call_errors"]["<read_telemetry>"])


class SerializeSignalsTruncatesErrorTests(unittest.TestCase):
    def test_long_error_is_truncated(self) -> None:
        runner = object.__new__(DeviceRunner)
        runner._ts_dict = lambda ts: {"t_wall": ts.t_wall, "t_mono": ts.t_mono}  # type: ignore[method-assign]
        long_err = "x" * 5000
        out = runner._serialize_signals(
            {"sig": {"value": None, "units": None, "quality": "BAD", "ts": None, "error": long_err}},
            bundle_ts=Timestamp(t_wall=0.0, t_mono=0.0),
        )
        err_text = out["sig"]["error"]
        self.assertLessEqual(len(err_text), 200)
        self.assertTrue(err_text.endswith("..."))


if __name__ == "__main__":
    unittest.main()
