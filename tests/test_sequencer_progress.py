# ruff: noqa: E402

import sys
from pathlib import Path
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.sequencer.ast import (
    AssignStep,
    RepeatStep,
    SequenceSpec,
    SleepStep,
    WhileStep,
)
from experiment_control.sequencer.runtime import SequencerRuntime


def _build_runtime() -> SequencerRuntime:
    def call_device(
        device_id: str, action: str, params: dict[str, object]
    ) -> dict[str, object]:
        return {"ok": True, "result": None}

    def get_telemetry(device_id: str, signal: str) -> dict[str, object] | None:
        return None

    def set_stream_context(
        device_id: str, stream: str, context_id: int, fields: dict[str, object]
    ) -> None:
        return None

    return SequencerRuntime(
        call_device=call_device,
        get_telemetry=get_telemetry,
        set_stream_context=set_stream_context,
    )


class SequencerProgressTests(unittest.TestCase):
    def test_progress_reports_known_total(self) -> None:
        runtime = _build_runtime()
        runtime.load(
            SequenceSpec(
                version=1,
                meta={},
                vars={},
                steps=[
                    RepeatStep(
                        times=3,
                        body=[
                            AssignStep(values={"x": 1}),
                        ],
                    )
                ],
                context_columns=None,
            )
        )
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        status = runtime.status()
        progress = status.get("progress", {})
        self.assertEqual(progress.get("total_steps"), 4)
        self.assertEqual(progress.get("completed_steps"), 4)
        self.assertEqual(progress.get("percent"), 100.0)
        self.assertEqual(progress.get("eta_s"), 0.0)
        elapsed = progress.get("elapsed_s")
        self.assertIsInstance(elapsed, float)
        assert isinstance(elapsed, float)
        self.assertGreaterEqual(elapsed, 0.0)

    def test_progress_marks_unknown_total_for_while(self) -> None:
        runtime = _build_runtime()
        runtime.load(
            SequenceSpec(
                version=1,
                meta={},
                vars={},
                steps=[
                    WhileStep(
                        condition=False,
                        body=[],
                    )
                ],
                context_columns=None,
            )
        )
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        status = runtime.status()
        progress = status.get("progress", {})
        self.assertIsNone(progress.get("total_steps"))
        self.assertIsNone(progress.get("percent"))

    def test_eta_hidden_until_min_completed_steps(self) -> None:
        runtime = _build_runtime()
        runtime.load(
            SequenceSpec(
                version=1,
                meta={},
                vars={},
                steps=[
                    RepeatStep(
                        times=8,
                        body=[SleepStep(seconds=0.002)],
                    )
                ],
                context_columns=None,
            )
        )
        runtime.start()

        saw_eta_after_min = False
        for _ in range(200):
            runtime.tick()
            status = runtime.status()
            progress = status.get("progress", {})
            completed = int(progress.get("completed_steps") or 0)
            total = int(progress.get("total_steps") or 0)
            eta_s = progress.get("eta_s")
            if completed < 5:
                self.assertIsNone(eta_s)
            elif completed < total and eta_s is not None:
                saw_eta_after_min = True
                break
            if runtime.state != "RUNNING":
                break
            time.sleep(0.003)

        self.assertTrue(saw_eta_after_min)

    def test_repeat_count_updates_loop_progress_fields(self) -> None:
        runtime = _build_runtime()
        runtime.load(
            SequenceSpec(
                version=1,
                meta={},
                vars={},
                steps=[AssignStep(values={"x": 1})],
                context_columns=None,
            )
        )
        runtime.start(repeat_count=3)
        while runtime.state == "RUNNING":
            runtime.tick()
        status = runtime.status()
        progress = status.get("progress", {})
        self.assertEqual(status.get("loop_mode"), "repeat")
        self.assertEqual(status.get("loops_target"), 3)
        self.assertEqual(status.get("loops_completed"), 3)
        self.assertEqual(progress.get("loop_mode"), "repeat")
        self.assertEqual(progress.get("loops_target"), 3)
        self.assertEqual(progress.get("loops_completed"), 3)


if __name__ == "__main__":
    unittest.main()
