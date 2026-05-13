# ruff: noqa: E402

from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
