# ruff: noqa: E402
"""Regression tests for the misc-review-followups PR.

Four independent fixes pinned here:

#48 - DeviceRunner._rpc_route_get / _rpc_route_set / _rpc_dispatch_device_command
       demote device health (_device_reachable=False, _device_state=DEGRADED,
       _last_error populated) when the underlying device call raises.
       Previously the exception was caught at the outermost RPC wrapper
       which only set _last_error — devices that consistently failed
       set_property looked healthy in the manager's status snapshot.

#20a - InfluxWriterProcess._build_telemetry_fields tracks per-signal
        drops via _signals_skipped_invalid and emits a one-line
        stderr entry for the first occurrence per (device, signal).
        Pre-fix these drops were silent.

#24 - DeviceRunner.connect_ipc caps inbound REP message size via
       zmq.MAXMSGSIZE so a misbehaving / malicious client sending
       oversize JSON can't wedge the driver loop on a giant recv.

#47 - sequencer.ranges.generate_from_gen with `range:` validates the
       step sign and uses a float-error-resistant count instead of
       np.arange. Sign mismatch raises (vs pre-fix silent empty
       array); float-edge cases like np.arange(0.0, 0.3, 0.1)
       producing 4 elements instead of 3 are pinned.
"""

from __future__ import annotations

import io
import sys
import threading
import unittest
import zmq
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.driver import DeviceRunner, _DRIVER_RPC_MAX_MSG_BYTES
from experiment_control.processes.influx_writer import InfluxWriterProcess
from experiment_control.sequencer.ranges import generate_from_gen
from experiment_control.types import DeviceState


# ---------------------------------------------------------------------------
# #48 — device health demotion on get/set/command failure
# ---------------------------------------------------------------------------


class _BrokenDevice:
    """Stand-in device whose attribute access always raises."""

    def __getattr__(self, name: str):
        if name == "good_attr":
            return 42
        raise RuntimeError(f"VISA timeout reading {name!r}")

    def __setattr__(self, name: str, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        raise RuntimeError(f"VISA write timeout setting {name!r}={value!r}")


def _runner_with_broken_device() -> DeviceRunner:
    """Build a DeviceRunner stub with the broken device wired in."""
    runner = object.__new__(DeviceRunner)
    runner.device_id = "broken"
    runner._device = _BrokenDevice()
    runner._device_state = DeviceState.OK
    runner._device_reachable = True
    runner._last_error = None
    runner._action_failed_since_last_ok = False
    # _members_cache pre-populated so _rpc_route_get/set skip the
    # _refresh_capabilities_cache path (which would hit a different
    # codepath in real use).
    spec = SimpleNamespace(
        settable=True, params=[], value_annotation=None, kind="property"
    )
    runner._members_cache = {  # type: ignore[attr-defined]
        "good_attr": spec,
        "bad_attr": spec,
    }
    return runner


class DeviceHealthDemotionTests(unittest.TestCase):
    def test_get_failure_demotes_device(self) -> None:
        runner = _runner_with_broken_device()
        resp = runner._rpc_route_get(
            {"id": "r1", "params": {"name": "bad_attr"}}
        )
        # RPC returns an error.
        self.assertEqual(resp.get("status"), "ERROR")
        # And critically: the runner is now DEGRADED / unreachable.
        self.assertFalse(runner._device_reachable)
        self.assertEqual(runner._device_state, DeviceState.DEGRADED)
        self.assertIsNotNone(runner._last_error)
        self.assertIn("bad_attr", runner._last_error)
        self.assertIn("VISA timeout", runner._last_error)

    def test_set_failure_demotes_device(self) -> None:
        runner = _runner_with_broken_device()
        resp = runner._rpc_route_set(
            {"id": "r2", "params": {"name": "bad_attr", "value": 3.14}}
        )
        self.assertEqual(resp.get("status"), "ERROR")
        self.assertFalse(runner._device_reachable)
        self.assertEqual(runner._device_state, DeviceState.DEGRADED)
        self.assertIsNotNone(runner._last_error)
        self.assertIn("bad_attr", runner._last_error)
        self.assertIn("VISA write timeout", runner._last_error)

    def test_get_success_does_not_demote(self) -> None:
        runner = _runner_with_broken_device()
        resp = runner._rpc_route_get(
            {"id": "r3", "params": {"name": "good_attr"}}
        )
        self.assertEqual(resp.get("status"), "OK")
        # Health unchanged.
        self.assertTrue(runner._device_reachable)
        self.assertEqual(runner._device_state, DeviceState.OK)

    def test_command_failure_demotes_device(self) -> None:
        runner = object.__new__(DeviceRunner)
        runner.device_id = "broken"
        runner._device = _BrokenDevice()
        runner._device_state = DeviceState.OK
        runner._device_reachable = True
        runner._last_error = None
        runner._action_failed_since_last_ok = False
        runner._members_cache = {}
        runner._stream_rpc = {}
        runner._last_ok_ts = None
        runner._now = lambda: SimpleNamespace(t_wall=1.0, t_mono=2.0)  # type: ignore[method-assign]

        def _failing_handle_command(action, params):
            raise RuntimeError(f"hardware error in {action!r}")

        runner.handle_command = _failing_handle_command  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError):
            runner._rpc_dispatch_device_command(
                {"id": "r4", "action": "some_action", "params": {}}
            )
        self.assertFalse(runner._device_reachable)
        self.assertEqual(runner._device_state, DeviceState.DEGRADED)
        self.assertIn("some_action", runner._last_error)

    def test_long_error_reason_is_truncated(self) -> None:
        runner = _runner_with_broken_device()
        runner._mark_device_unreachable("x" * 500)
        self.assertLessEqual(len(runner._last_error), 200)
        self.assertTrue(runner._last_error.endswith("..."))

    def test_latch_survives_next_telemetry_tick(self) -> None:
        """Local review caught that the pre-fix _mark_device_unreachable
        was undone by the very next telemetry tick because
        _apply_telemetry_quality_state unconditionally promoted to OK
        when telemetry signals were healthy. Telemetry runs on a
        different code path from get/set/command and CAN succeed
        while every action call fails (e.g. driver caches the last
        telemetry values but every set_property to the hardware
        raises). The _action_failed_since_last_ok latch prevents
        the silent recovery.
        """
        runner = _runner_with_broken_device()
        # Simulate a failed action — sets the latch + demotes.
        runner._mark_device_unreachable("set 'amp' failed: VISA timeout")
        self.assertFalse(runner._device_reachable)
        self.assertEqual(runner._device_state, DeviceState.DEGRADED)
        self.assertTrue(runner._action_failed_since_last_ok)
        # Now simulate the next telemetry tick: telemetry signals all
        # OK (e.g. driver reads its cached values, no hardware call).
        ok_signals = {
            "temp": {
                "value": 1.0,
                "units": "K",
                "quality": "OK",
                "ts": None,
            }
        }
        runner._apply_telemetry_quality_state(ok_signals)
        # CRITICAL: must NOT silently promote back to OK.
        self.assertFalse(
            runner._device_reachable,
            "telemetry tick with OK signals must NOT clear the "
            "action-failure demotion while the latch is set — that "
            "was the bug the local review caught",
        )
        self.assertEqual(runner._device_state, DeviceState.DEGRADED)
        # _last_error must still identify the failing action, not be
        # replaced by a generic "telemetry partially degraded" message.
        self.assertIn("set 'amp'", runner._last_error)
        self.assertIn("VISA timeout", runner._last_error)

    def test_latch_clears_after_successful_action_then_telemetry_promotes(
        self,
    ) -> None:
        """Once the operator's retry succeeds, the latch clears and
        the next telemetry tick is free to promote normally."""
        runner = _runner_with_broken_device()
        runner._mark_device_unreachable("set 'amp' failed: timeout")
        self.assertTrue(runner._action_failed_since_last_ok)

        # Successful set — clears the latch (via _mark_action_succeeded
        # invoked from _rpc_route_set's success path; here we simulate
        # the helper directly).
        runner._mark_action_succeeded()
        self.assertFalse(runner._action_failed_since_last_ok)
        # _device_reachable / _device_state are NOT cleared by
        # _mark_action_succeeded itself — they're cleared by the next
        # telemetry promote pass.
        self.assertFalse(runner._device_reachable)

        ok_signals = {
            "temp": {
                "value": 1.0,
                "units": "K",
                "quality": "OK",
                "ts": None,
            }
        }
        runner._apply_telemetry_quality_state(ok_signals)
        # NOW telemetry can promote back to OK because the latch is
        # clear.
        self.assertTrue(runner._device_reachable)
        self.assertEqual(runner._device_state, DeviceState.OK)
        self.assertIsNone(runner._last_error)

    def test_latch_survives_no_telemetry_signals_tick(self) -> None:
        """The no-telemetry-signals branch of _publish_telemetry has
        its own promote-to-OK path; verify the latch gates that too.
        """
        runner = _runner_with_broken_device()
        runner._telemetry_seq = 0
        runner._now = lambda: SimpleNamespace(t_wall=1.0, t_mono=2.0)  # type: ignore[method-assign]
        runner.read_telemetry = lambda: {}  # type: ignore[method-assign]
        runner.telemetry_signal_names = lambda: []  # type: ignore[method-assign]
        runner._ts_dict = lambda ts: {"t_wall": ts.t_wall, "t_mono": ts.t_mono}  # type: ignore[method-assign]
        runner._serialize_signals = lambda signals, *, bundle_ts: signals  # type: ignore[method-assign]
        runner.pub = SimpleNamespace(  # type: ignore[attr-defined]
            send_multipart=lambda parts: None
        )
        runner._mark_device_unreachable("set 'amp' failed: timeout")
        runner._publish_telemetry()
        # Latch keeps the demotion in place.
        self.assertFalse(runner._device_reachable)
        self.assertEqual(runner._device_state, DeviceState.DEGRADED)


# ---------------------------------------------------------------------------
# #20a — Influx per-signal drop tracking
# ---------------------------------------------------------------------------


def _influx_proc_for_field_test() -> InfluxWriterProcess:
    proc = object.__new__(InfluxWriterProcess)
    proc._signals_skipped_invalid = 0  # type: ignore[attr-defined]
    proc._signals_skipped_invalid_seen = set()  # type: ignore[attr-defined]
    proc._counters_lock = threading.Lock()  # type: ignore[attr-defined]
    proc._include_quality_fields = False  # type: ignore[attr-defined]
    proc._include_unit_fields = False  # type: ignore[attr-defined]
    return proc


class InfluxSignalDropTrackingTests(unittest.TestCase):
    def test_unwritable_value_is_counted(self) -> None:
        proc = _influx_proc_for_field_test()
        # numpy array is rejected by _coerce_signal_field_value.
        import numpy as np

        signals = {
            "good_signal": {"value": 1.0, "quality": "OK"},
            "bad_signal": {"value": np.array([1.0, 2.0]), "quality": "OK"},
        }
        with patch("sys.stderr", new_callable=io.StringIO) as buf:
            fields = proc._build_telemetry_fields(
                signals, device_id="dev1"
            )
            captured = buf.getvalue()
        # Good signal made it; bad signal didn't.
        self.assertIn("good_signal", fields)
        self.assertNotIn("bad_signal", fields)
        # Counter bumped.
        self.assertEqual(proc._signals_skipped_invalid, 1)
        # First-occurrence stderr log fired and names the signal.
        self.assertIn("bad_signal", captured)
        self.assertIn("dev1", captured)
        self.assertIn("ndarray", captured)

    def test_repeated_drops_only_log_once_per_pair(self) -> None:
        proc = _influx_proc_for_field_test()
        import numpy as np

        signals = {
            "bad_signal": {"value": np.array([1.0]), "quality": "OK"},
        }
        with patch("sys.stderr", new_callable=io.StringIO) as buf:
            for _ in range(5):
                proc._build_telemetry_fields(signals, device_id="dev1")
            captured = buf.getvalue()
        self.assertEqual(proc._signals_skipped_invalid, 5)
        # Only ONE log line for the same (device, signal) pair.
        self.assertEqual(captured.count("bad_signal"), 1)

    def test_distinct_signals_logged_independently(self) -> None:
        proc = _influx_proc_for_field_test()
        with patch("sys.stderr", new_callable=io.StringIO) as buf:
            proc._build_telemetry_fields(
                {"a": {"value": [1, 2], "quality": "OK"}},
                device_id="dev1",
            )
            proc._build_telemetry_fields(
                {"b": {"value": {"x": 1}, "quality": "OK"}},
                device_id="dev1",
            )
            proc._build_telemetry_fields(
                {"a": {"value": [1, 2], "quality": "OK"}},
                device_id="dev2",
            )
            captured = buf.getvalue()
        # 3 distinct (device, signal) pairs → 3 log lines.
        self.assertEqual(captured.count("dropping signal"), 3)


# ---------------------------------------------------------------------------
# #24 — Driver REP MAXMSGSIZE cap
# ---------------------------------------------------------------------------


class DriverRpcMessageCapTests(unittest.TestCase):
    def test_constant_is_reasonable(self) -> None:
        # 1 MiB ceiling: above any legitimate manager.command envelope
        # (typically < 16 KiB) but bounded enough that a misbehaving
        # client can't allocate gigabytes via a single recv.
        self.assertEqual(_DRIVER_RPC_MAX_MSG_BYTES, 1 * 1024 * 1024)
        self.assertGreaterEqual(_DRIVER_RPC_MAX_MSG_BYTES, 16 * 1024)
        self.assertLessEqual(_DRIVER_RPC_MAX_MSG_BYTES, 16 * 1024 * 1024)

    def test_connect_ipc_sets_maxmsgsize(self) -> None:
        """connect_ipc must call setsockopt(MAXMSGSIZE) on the REP socket
        BEFORE bind_to_random_port, otherwise an inbound oversize
        message during the bind window could land before the cap is in
        effect."""
        runner = object.__new__(DeviceRunner)
        ctx = zmq.Context()
        runner.ctx = ctx
        runner.rpc = ctx.socket(zmq.REP)
        runner.pub = ctx.socket(zmq.PUB)
        try:
            runner.connect_ipc()
            # Verify the option is set on the live socket.
            actual = runner.rpc.getsockopt(zmq.MAXMSGSIZE)
            self.assertEqual(actual, _DRIVER_RPC_MAX_MSG_BYTES)
        finally:
            try:
                runner.rpc.close(0)
            except Exception:
                pass
            try:
                runner.pub.close(0)
            except Exception:
                pass
            ctx.term()


# ---------------------------------------------------------------------------
# #47 — sequencer range gen sign validation + float-error resistance
# ---------------------------------------------------------------------------


class SequencerRangeGenSignValidationTests(unittest.TestCase):
    def test_wrong_sign_step_raises(self) -> None:
        # start=0, stop=5, step=-1 → pre-fix np.arange returned []
        # silently. Now raises with a clear message.
        with self.assertRaises(ValueError) as ctx:
            generate_from_gen(
                {"range": {"start": 0.0, "stop": 5.0, "step": -1.0}}, env={}
            )
        self.assertIn("step direction", str(ctx.exception))

    def test_wrong_sign_reverse_also_raises(self) -> None:
        # start=5, stop=0, step=+1 — same problem in the other direction.
        with self.assertRaises(ValueError):
            generate_from_gen(
                {"range": {"start": 5.0, "stop": 0.0, "step": 1.0}}, env={}
            )

    def test_correct_descending_range_works(self) -> None:
        # start=5, stop=0, step=-1 — descending, sign correct.
        recs = generate_from_gen(
            {"range": {"start": 5.0, "stop": 0.0, "step": -1.0}}, env={}
        )
        values = [r["value"] for r in recs]
        self.assertEqual(values, [5.0, 4.0, 3.0, 2.0, 1.0])

    def test_float_error_does_not_add_extra_element(self) -> None:
        # The classic float-error case: 0.3 / 0.1 == 2.9999999999999996,
        # so np.arange(0.0, 0.3, 0.1) returns [0.0, 0.1, 0.2] — but
        # any drift like np.arange(0.0, 0.3 + tiny, 0.1) returns 4
        # elements. The fix uses a math.ceil + tolerance so the count
        # is exactly 3 regardless of float drift.
        recs = generate_from_gen(
            {"range": {"start": 0.0, "stop": 0.3, "step": 0.1}}, env={}
        )
        # The result is exclusive of stop (same as np.arange).
        self.assertEqual(len(recs), 3)
        values = [r["value"] for r in recs]
        self.assertAlmostEqual(values[0], 0.0)
        self.assertAlmostEqual(values[1], 0.1)
        self.assertAlmostEqual(values[2], 0.2)

    def test_zero_step_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            generate_from_gen(
                {"range": {"start": 0.0, "stop": 5.0, "step": 0.0}}, env={}
            )
        self.assertIn("step must be non-zero", str(ctx.exception))

    def test_empty_range_when_start_equals_stop(self) -> None:
        # Edge case: start == stop with any step → empty (exclusive of
        # stop). Doesn't raise — it's a legitimate "no work" config.
        recs = generate_from_gen(
            {"range": {"start": 1.0, "stop": 1.0, "step": 0.5}}, env={}
        )
        self.assertEqual(recs, [])


if __name__ == "__main__":
    unittest.main()
