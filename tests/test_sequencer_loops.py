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
        resolve_use=resolve_use,
    )


class SequencerLoopTests(unittest.TestCase):
    def test_for_bind_string_maps_value_field(self) -> None:
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
                                    "assign": {
                                        "last_freq": "${freq_hz}",
                                    }
                                }
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
        self.assertEqual(status["env"].get("last_freq"), 30)

    def test_for_bind_object_can_use_value_and_index(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {
                        "for": {
                            "bind": {"value": "freq_hz", "index": "freq_idx"},
                            "in": {"gen": {"values": [10, 20, 30]}},
                            "do": [
                                {
                                    "assign": {
                                        "seen": "${freq_hz}",
                                        "seen_idx": "${freq_idx}",
                                    }
                                }
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
        self.assertEqual(status["env"].get("seen"), 30)
        self.assertEqual(status["env"].get("seen_idx"), 2)

    def test_for_bind_scan2d_can_bind_partial_fields(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {
                        "for": {
                            "bind": {"x": "scan_x", "col": "scan_col"},
                            "in": {
                                "gen": {
                                    "scan2d": {
                                        "center": {"x": 0.0, "y": 0.0},
                                        "width": 2.0,
                                        "height": 1.0,
                                        "steps": {"x": 3, "y": 2},
                                    }
                                }
                            },
                            "do": [
                                {
                                    "assign": {
                                        "last_x": "${scan_x}",
                                        "last_col": "${scan_col}",
                                    }
                                }
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
        self.assertEqual(status["env"].get("last_x"), -1.0)
        self.assertEqual(status["env"].get("last_col"), 0)

    def test_for_bind_requires_existing_record_field(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {
                        "for": {
                            "bind": {"missing": "foo"},
                            "in": {"gen": {"values": [1, 2]}},
                            "do": [],
                        }
                    }
                ],
            }
        )
        runtime = _build_runtime()
        runtime.load(spec)
        runtime.start()
        runtime.tick()
        status = runtime.status()
        self.assertEqual(status["state"], "ERROR")
        self.assertIn("missing", str(status["error"]))

    def test_use_step_applies_args_and_restores_parent_vars(self) -> None:
        helper = parse_sequence(
            {
                "version": 1,
                "vars": {"gain": 1},
                "steps": [{"assign": {"seen_gain": "${gain}"}}],
            }
        )
        main = parse_sequence(
            {
                "version": 1,
                "vars": {"base_gain": 2},
                "steps": [
                    {
                        "use": {
                            "id": "helper",
                            "args": {"gain": "${base_gain + 1}"},
                        }
                    },
                    {"assign": {"after": "${base_gain}"}},
                ],
            }
        )
        runtime = _build_runtime(resolve_use=lambda sequence_id: {"helper": helper}[sequence_id])
        runtime.load(main)
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()
        status = runtime.status()
        self.assertEqual(status["state"], "STOPPED")
        self.assertEqual(status["env"].get("seen_gain"), 3)
        self.assertEqual(status["env"].get("after"), 2)
        self.assertEqual(status["vars"].get("base_gain"), 2)

    def test_use_step_recursion_sets_error(self) -> None:
        main = parse_sequence(
            {
                "version": 1,
                "steps": [{"use": "helper"}],
            }
        )
        helper = parse_sequence(
            {
                "version": 1,
                "steps": [{"use": "main"}],
            }
        )
        library = {"main": main, "helper": helper}
        runtime = _build_runtime(resolve_use=lambda sequence_id: library[sequence_id])
        runtime.load(main)
        runtime.start()
        for _ in range(32):
            runtime.tick()
            if runtime.state != "RUNNING":
                break
        status = runtime.status()
        self.assertEqual(status["state"], "ERROR")
        self.assertIn("recursive use sequence detected", str(status["error"]))


if __name__ == "__main__":
    unittest.main()
