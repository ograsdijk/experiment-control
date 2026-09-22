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


def _build_runtime(*, fail_on_device_call: bool = False) -> tuple[SequencerRuntime, list]:
    calls: list[tuple[str, str, str, dict[str, object]]] = []

    def call_device(
        device_id: str, action: str, params: dict[str, object]
    ) -> dict[str, object]:
        calls.append(("device", device_id, action, dict(params)))
        if fail_on_device_call:
            return {"ok": False, "error": "boom"}
        return {"ok": True, "result": None}

    def call_process(
        process_id: str, action: str, params: dict[str, object]
    ) -> dict[str, object]:
        calls.append(("process", process_id, action, dict(params)))
        return {"ok": True, "result": None}

    def get_telemetry(device_id: str, signal: str) -> dict[str, object] | None:
        return None

    def set_stream_context(
        device_id: str, stream: str, context_id: int, fields: dict[str, object]
    ) -> None:
        return None

    runtime = SequencerRuntime(
        call_device=call_device,
        call_process=call_process,
        get_telemetry=get_telemetry,
        set_stream_context=set_stream_context,
    )
    return runtime, calls


class WatchdogGateTests(unittest.TestCase):
    def test_watchdog_ids_wraps_enable_body_disable(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "requires": {"watchdog_ids": ["laser_a_watchdog", "laser_b_watchdog"]},
                "steps": [
                    {"call": {"device": "dev", "action": "do_thing", "params": {}}},
                ],
            }
        )
        runtime, calls = _build_runtime()
        runtime.load(spec)
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()
        self.assertEqual(runtime.state, "STOPPED")
        self.assertEqual(
            calls,
            [
                ("process", "watchdog", "watchdog.enable", {"watchdog_id": "laser_a_watchdog"}),
                ("process", "watchdog", "watchdog.enable", {"watchdog_id": "laser_b_watchdog"}),
                ("device", "dev", "do_thing", {}),
                ("process", "watchdog", "watchdog.disable", {"watchdog_id": "laser_a_watchdog"}),
                ("process", "watchdog", "watchdog.disable", {"watchdog_id": "laser_b_watchdog"}),
            ],
        )

    def test_watchdog_ids_disabled_even_when_body_fails(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "requires": {"watchdog_ids": ["laser_a_watchdog"]},
                "steps": [
                    {"call": {"device": "dev", "action": "do_thing", "params": {}}},
                ],
            }
        )
        runtime, calls = _build_runtime(fail_on_device_call=True)
        runtime.load(spec)
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()
        self.assertEqual(runtime.state, "ERROR")
        actions = [(kind, target, action) for kind, target, action, _ in calls]
        self.assertIn(("process", "watchdog", "watchdog.disable"), actions)

    def test_no_watchdog_ids_leaves_steps_untouched(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {"call": {"device": "dev", "action": "do_thing", "params": {}}},
                ],
            }
        )
        runtime, calls = _build_runtime()
        runtime.load(spec)
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()
        self.assertEqual(
            calls, [("device", "dev", "do_thing", {})]
        )

    def test_watchdog_ids_must_be_nonempty_string_list(self) -> None:
        with self.assertRaises(TypeError):
            parse_sequence(
                {
                    "version": 1,
                    "requires": {"watchdog_ids": []},
                    "steps": [],
                }
            )
        with self.assertRaises(TypeError):
            parse_sequence(
                {
                    "version": 1,
                    "requires": {"watchdog_ids": ["", "ok"]},
                    "steps": [],
                }
            )

    def test_meta_watchdog_ids_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            parse_sequence(
                {
                    "version": 1,
                    "meta": {"watchdog_ids": ["laser_a_watchdog"]},
                    "steps": [],
                }
            )

    def test_requires_rejects_unknown_keys(self) -> None:
        with self.assertRaises(TypeError):
            parse_sequence(
                {
                    "version": 1,
                    "requires": {"bogus_key": True},
                    "steps": [],
                }
            )

    def test_requires_hdf_writing_sets_spec_flag(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "requires": {"hdf_writing": True},
                "steps": [],
            }
        )
        self.assertTrue(spec.require_hdf_writing)

    def test_requires_hdf_writing_defaults_false(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [],
            }
        )
        self.assertFalse(spec.require_hdf_writing)

    def test_requires_hdf_writing_must_be_bool(self) -> None:
        with self.assertRaises(TypeError):
            parse_sequence(
                {
                    "version": 1,
                    "requires": {"hdf_writing": "yes"},
                    "steps": [],
                }
            )


if __name__ == "__main__":
    unittest.main()
