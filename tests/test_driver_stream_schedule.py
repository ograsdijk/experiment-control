# ruff: noqa: E402

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.driver import DeviceRunner, _ScheduledStreamCallPlan
from experiment_control.types import DeviceState, StreamCall, StreamOut


class DriverStreamScheduleTests(unittest.TestCase):
    @staticmethod
    def _runner() -> DeviceRunner:
        runner = object.__new__(DeviceRunner)
        runner._device_state = DeviceState.OK  # type: ignore[attr-defined]
        runner._device_reachable = False  # type: ignore[attr-defined]
        runner._last_ok_ts = None  # type: ignore[attr-defined]
        runner._last_error = None  # type: ignore[attr-defined]
        runner._scheduled_stream_missed_total = 0  # type: ignore[attr-defined]
        runner._stream_last_published_seq = {}  # type: ignore[attr-defined]
        runner._scheduled_streams_need_resync = False  # type: ignore[attr-defined]
        return runner  # type: ignore[return-value]

    def test_omitted_period_does_not_schedule_stream_call(self) -> None:
        runner = self._runner()
        runner._stream_calls = [  # type: ignore[attr-defined]
            StreamCall(
                method="acquire_trace",
                outputs=[StreamOut(stream="trace", dtype="float64", shape=(8,))],
            )
        ]
        runner._stream_rpc = {"stream__acquire_trace": lambda: None}  # type: ignore[attr-defined]
        runner._scheduled_stream_calls = []  # type: ignore[attr-defined]

        DeviceRunner._init_scheduled_stream_calls(runner)

        self.assertEqual(runner._scheduled_stream_calls, [])  # type: ignore[attr-defined]

    def test_periodic_stream_calls_have_independent_schedules(self) -> None:
        runner = self._runner()
        calls: list[str] = []
        runner._stream_rpc = {  # type: ignore[attr-defined]
            "stream__fast": lambda: calls.append("fast"),
            "stream__slow": lambda: calls.append("slow"),
        }
        runner._scheduled_stream_calls = [  # type: ignore[attr-defined]
            _ScheduledStreamCallPlan("stream__fast", 1.0, 10.0),
            _ScheduledStreamCallPlan("stream__slow", 5.0, 20.0),
        ]

        DeviceRunner._publish_scheduled_streams(runner, now=10.0)

        self.assertEqual(calls, ["fast"])
        plans = runner._scheduled_stream_calls  # type: ignore[attr-defined]
        self.assertEqual(plans[0].next_due_s, 11.0)
        self.assertEqual(plans[1].next_due_s, 20.0)

    def test_scheduled_stream_failure_does_not_block_other_streams(self) -> None:
        runner = self._runner()
        calls: list[str] = []

        def fail() -> None:
            calls.append("fail")
            raise RuntimeError("boom")

        runner._stream_rpc = {  # type: ignore[attr-defined]
            "stream__bad": fail,
            "stream__good": lambda: calls.append("good"),
        }
        runner._scheduled_stream_calls = [  # type: ignore[attr-defined]
            _ScheduledStreamCallPlan("stream__bad", 1.0, 10.0),
            _ScheduledStreamCallPlan("stream__good", 1.0, 10.0),
        ]

        DeviceRunner._publish_scheduled_streams(runner, now=10.0)

        self.assertEqual(calls, ["fail", "good"])
        self.assertTrue(runner._device_reachable)  # type: ignore[attr-defined]

    def test_missed_scheduled_stream_ticks_are_counted(self) -> None:
        # A loop that falls behind a plan's next_due_s must leave a trace
        # (previously the only zero-accounting loss point in the driver).
        runner = self._runner()
        runner._stream_rpc = {"stream__slow": lambda: None}  # type: ignore[attr-defined]
        runner._scheduled_stream_calls = [  # type: ignore[attr-defined]
            _ScheduledStreamCallPlan("stream__slow", 1.0, 10.0),
        ]

        # now is 5.5 periods past the due time -> 5 whole periods missed.
        DeviceRunner._publish_scheduled_streams(runner, now=15.5)

        self.assertEqual(runner._scheduled_stream_missed_total, 5)  # type: ignore[attr-defined]

    def test_reconnect_after_disconnect_does_not_inflate_missed_total(self) -> None:
        # A disconnect must not be reported as loop-lag: next_due_s is
        # frozen for the whole outage while DISCONNECTED, so the naive
        # missed-tick math on the first reconnected tick would otherwise
        # read the entire outage duration as "missed" scheduled samples.
        runner = self._runner()
        runner._stream_rpc = {"stream__slow": lambda: None}  # type: ignore[attr-defined]
        runner._scheduled_stream_calls = [  # type: ignore[attr-defined]
            _ScheduledStreamCallPlan("stream__slow", 1.0, 10.0),
        ]

        runner._device_state = DeviceState.DISCONNECTED  # type: ignore[attr-defined]
        # Several ticks pass while disconnected -- next_due_s stays frozen.
        DeviceRunner._publish_scheduled_streams(runner, now=11.0)
        DeviceRunner._publish_scheduled_streams(runner, now=50.0)
        DeviceRunner._publish_scheduled_streams(runner, now=110.0)
        self.assertEqual(runner._scheduled_stream_missed_total, 0)  # type: ignore[attr-defined]

        # Reconnect. The first tick after reconnect must resync, not fire
        # a huge missed-tick count for the outage.
        runner._device_state = DeviceState.OK  # type: ignore[attr-defined]
        DeviceRunner._publish_scheduled_streams(runner, now=110.5)

        self.assertEqual(runner._scheduled_stream_missed_total, 0)  # type: ignore[attr-defined]
        plans = runner._scheduled_stream_calls  # type: ignore[attr-defined]
        self.assertEqual(plans[0].next_due_s, 111.5)

        # Normal operation resumes: a genuine lag after this point is still
        # counted correctly.
        DeviceRunner._publish_scheduled_streams(runner, now=112.5)
        self.assertEqual(runner._scheduled_stream_missed_total, 1)  # type: ignore[attr-defined]

    def test_publish_stream_records_last_published_seq(self) -> None:
        import numpy as np

        runner = self._runner()
        runner._stream_outputs = {  # type: ignore[attr-defined]
            "trace": StreamOut(stream="trace", dtype="float64", shape=(2,)),
        }

        class _FakeWriter:
            def __init__(self) -> None:
                self._seq = 0
                self.layout = type("Layout", (), {"layout_version": 1})()

            def write(self, arr, *, t0_mono_ns, t0_wall_ns):  # noqa: ARG002
                self._seq += 1
                return self._seq

        runner._ensure_stream_publishers = lambda: None  # type: ignore[attr-defined]
        runner._stream_writers = {"trace": _FakeWriter()}  # type: ignore[attr-defined]
        runner._stream_shm_names = {"trace": "shm-trace"}  # type: ignore[attr-defined]
        runner._stream_context = {}  # type: ignore[attr-defined]
        runner.device_id = "dev1"  # type: ignore[attr-defined]

        class _FakePub:
            def send_multipart(self, frames: list[bytes]) -> None:
                pass

        runner.pub = _FakePub()  # type: ignore[attr-defined]

        DeviceRunner.publish_stream(runner, "trace", np.array([1.0, 2.0]))
        DeviceRunner.publish_stream(runner, "trace", np.array([3.0, 4.0]))

        self.assertEqual(
            runner._stream_last_published_seq["trace"], 2  # type: ignore[attr-defined]
        )

    def test_heartbeat_includes_missed_ticks_and_published_seq(self) -> None:
        # Heartbeat must carry both new fields so a consumer can turn
        # SHM/PUB losses and blocked-loop skips into checkable invariants.
        runner = self._runner()
        runner.device_id = "dev1"  # type: ignore[attr-defined]
        runner._heartbeat_seq = 0  # type: ignore[attr-defined]
        runner._scheduled_stream_missed_total = 3  # type: ignore[attr-defined]
        runner._stream_last_published_seq = {"trace": 7}  # type: ignore[attr-defined]

        sent: list[bytes] = []

        class _FakePub:
            def send_multipart(self, frames: list[bytes]) -> None:
                sent.append(frames[1])

        runner.pub = _FakePub()  # type: ignore[attr-defined]

        DeviceRunner._publish_heartbeat(runner, loop_lag_s=None)

        payload = json.loads(sent[0])
        self.assertEqual(payload["scheduled_stream_missed_total"], 3)
        self.assertEqual(payload["stream_last_published_seq"], {"trace": 7})


if __name__ == "__main__":
    unittest.main()
