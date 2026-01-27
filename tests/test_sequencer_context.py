import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.sequencer.ast import SequenceSpec, SetContextStep
from experiment_control.sequencer.runtime import SequencerRuntime


def _build_runtime(calls: list[tuple[str, str, int, dict[str, object]]]) -> SequencerRuntime:
    def call_device(device_id: str, action: str, params: dict[str, object]) -> dict[str, object]:
        return {"ok": True, "result": None}

    def get_telemetry(device_id: str, signal: str) -> dict[str, object] | None:
        return None

    def set_stream_context(
        device_id: str,
        stream: str,
        context_id: int,
        fields: dict[str, object],
    ) -> None:
        calls.append((device_id, stream, context_id, dict(fields)))

    return SequencerRuntime(
        call_device=call_device,
        get_telemetry=get_telemetry,
        set_stream_context=set_stream_context,
    )


class SequencerContextIdTests(unittest.TestCase):
    def test_context_id_keeps_incrementing_across_start(self) -> None:
        calls: list[tuple[str, str, int, dict[str, object]]] = []
        runtime = _build_runtime(calls)
        spec = SequenceSpec(
            version=1,
            meta={},
            vars={},
            steps=[
                SetContextStep(
                    streams=[{"device": "scope", "stream": "trace"}],
                    fields={"freq_hz": 1.0},
                )
            ],
            context_columns=None,
        )
        runtime.load(spec)

        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        context_ids = [item[2] for item in calls]
        self.assertEqual(context_ids, [0, 1])

    def test_status_exposes_context_counters(self) -> None:
        calls: list[tuple[str, str, int, dict[str, object]]] = []
        runtime = _build_runtime(calls)
        spec = SequenceSpec(
            version=1,
            meta={},
            vars={},
            steps=[
                SetContextStep(
                    streams=[{"device": "scope", "stream": "trace"}],
                    fields={"freq_hz": 1.0},
                )
            ],
            context_columns=None,
        )
        runtime.load(spec)

        initial = runtime.status()
        self.assertEqual(initial.get("last_context_id"), -1)
        self.assertEqual(initial.get("next_context_id"), 0)

        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        after = runtime.status()
        self.assertEqual(after.get("last_context_id"), 0)
        self.assertEqual(after.get("next_context_id"), 1)


if __name__ == "__main__":
    unittest.main()
