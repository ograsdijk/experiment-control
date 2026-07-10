# ruff: noqa: E402

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.sequencer.ast import parse_sequence
from experiment_control.sequencer.runtime import SequencerRuntime


def _build_runtime(*, resolve_use=None) -> SequencerRuntime:
    calls: list[tuple[str, str, dict[str, object]]] = []

    def call_device(
        device_id: str, action: str, params: dict[str, object]
    ) -> dict[str, object]:
        calls.append((device_id, action, dict(params)))
        return {"ok": True, "result": None}

    def get_telemetry(device_id: str, signal: str) -> dict[str, object] | None:
        return None

    def set_stream_context(
        device_id: str, stream: str, context_id: int, fields: dict[str, object]
    ) -> None:
        return None

    runtime = SequencerRuntime(
        call_device=call_device,
        get_telemetry=get_telemetry,
        set_stream_context=set_stream_context,
        resolve_use=resolve_use,
    )
    runtime._test_calls = calls  # type: ignore[attr-defined]
    return runtime


class SequencerDisabledStepTests(unittest.TestCase):
    def test_disabled_call_step_is_skipped(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {
                        "disabled": True,
                        "call": {"device": "dev", "action": "do_thing", "params": {}},
                    },
                    {"assign": {"ran": True}},
                ],
            }
        )
        runtime = _build_runtime()
        runtime.load(spec)
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()
        status = runtime.status()
        self.assertEqual(runtime._test_calls, [])  # type: ignore[attr-defined]
        self.assertTrue(status["env"].get("ran"))

    def test_disabled_step_defaults_to_false(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {"call": {"device": "dev", "action": "do_thing", "params": {}}},
                ],
            }
        )
        runtime = _build_runtime()
        runtime.load(spec)
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()
        self.assertEqual(len(runtime._test_calls), 1)  # type: ignore[attr-defined]

    def test_disabled_body_step_inside_for_is_skipped(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {
                        "for": {
                            "bind": "freq_hz",
                            "in": {"gen": {"values": [10, 20, 30]}},
                            "do": [
                                {
                                    "disabled": True,
                                    "call": {
                                        "device": "dev",
                                        "action": "do_thing",
                                        "params": {},
                                    },
                                },
                                {"assign": {"last_freq": "${freq_hz}"}},
                            ],
                        }
                    }
                ],
            }
        )
        runtime = _build_runtime()
        runtime.load(spec)
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()
        status = runtime.status()
        self.assertEqual(runtime._test_calls, [])  # type: ignore[attr-defined]
        self.assertEqual(status["env"].get("last_freq"), 30)


if __name__ == "__main__":
    unittest.main()
