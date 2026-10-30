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

    def test_try_finally_runs_on_normal_completion(self) -> None:
        calls: list[tuple[str, str]] = []

        runtime = SequencerRuntime(
            call_device=lambda d, a, p: calls.append((d, a))
            or {"ok": True, "result": None},
            get_telemetry=lambda d, s: None,
            set_stream_context=lambda *a: None,
        )
        runtime.load(
            parse_sequence(
                {
                    "version": 1,
                    "steps": [
                        {
                            "try": {
                                "do": [{"call": {"device": "dev", "action": "body"}}],
                                "finally": [
                                    {"call": {"device": "dev", "action": "cleanup"}}
                                ],
                            }
                        }
                    ],
                }
            )
        )
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()
        self.assertEqual(runtime.status()["state"], "STOPPED")
        self.assertEqual(calls, [("dev", "body"), ("dev", "cleanup")])

    def test_try_finally_runs_on_call_failure(self) -> None:
        calls: list[tuple[str, str]] = []

        def call_device(device: str, action: str, params: dict[str, object]):
            calls.append((device, action))
            if action == "body":
                return {"ok": False, "error": "body failed"}
            return {"ok": True, "result": None}

        runtime = SequencerRuntime(
            call_device=call_device,
            get_telemetry=lambda d, s: None,
            set_stream_context=lambda *a: None,
        )
        runtime.load(
            parse_sequence(
                {
                    "version": 1,
                    "steps": [
                        {
                            "try": {
                                "do": [{"call": {"device": "dev", "action": "body"}}],
                                "finally": [
                                    {"call": {"device": "dev", "action": "cleanup"}}
                                ],
                            }
                        }
                    ],
                }
            )
        )
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()
        status = runtime.status()
        self.assertEqual(status["state"], "ERROR")
        self.assertEqual(status["error"], "body failed")
        self.assertEqual(calls, [("dev", "body"), ("dev", "cleanup")])

    def test_try_finally_runs_on_requested_stop(self) -> None:
        calls: list[tuple[str, str]] = []
        runtime = SequencerRuntime(
            call_device=lambda d, a, p: calls.append((d, a))
            or {"ok": True, "result": None},
            get_telemetry=lambda d, s: None,
            set_stream_context=lambda *a: None,
        )
        runtime.load(
            parse_sequence(
                {
                    "version": 1,
                    "steps": [
                        {
                            "try": {
                                "do": [{"sleep": 10}],
                                "finally": [
                                    {"call": {"device": "dev", "action": "cleanup"}}
                                ],
                            }
                        }
                    ],
                }
            )
        )
        runtime.start()
        runtime.tick()
        runtime.request_stop()
        while runtime.state == "RUNNING":
            runtime.tick()
        self.assertEqual(runtime.status()["state"], "STOPPED")
        self.assertEqual(calls, [("dev", "cleanup")])

    def test_stop_requested_while_paused_transitions_to_stopped(self) -> None:
        calls: list[tuple[str, str]] = []
        runtime = SequencerRuntime(
            call_device=lambda d, a, p: calls.append((d, a))
            or {"ok": True, "result": None},
            get_telemetry=lambda d, s: None,
            set_stream_context=lambda *a: None,
        )
        runtime.load(
            parse_sequence(
                {
                    "version": 1,
                    "steps": [{"sleep": 10}],
                }
            )
        )
        runtime.start()
        runtime.request_pause()
        runtime.tick()
        self.assertEqual(runtime.status()["state"], "PAUSED")

        runtime.request_stop()
        while runtime.state == "RUNNING":
            runtime.tick()
        self.assertEqual(runtime.status()["state"], "STOPPED")

    def test_try_finally_runs_on_external_fail(self) -> None:
        calls: list[tuple[str, str]] = []
        runtime = SequencerRuntime(
            call_device=lambda d, a, p: calls.append((d, a))
            or {"ok": True, "result": None},
            get_telemetry=lambda d, s: None,
            set_stream_context=lambda *a: None,
        )
        runtime.load(
            parse_sequence(
                {
                    "version": 1,
                    "steps": [
                        {
                            "try": {
                                "do": [{"sleep": 10}],
                                "finally": [
                                    {"call": {"device": "dev", "action": "cleanup"}}
                                ],
                            }
                        }
                    ],
                }
            )
        )
        runtime.start()
        runtime.tick()
        runtime.fail("external fault test")
        while runtime.state == "RUNNING":
            runtime.tick()
        status = runtime.status()
        self.assertEqual(status["state"], "ERROR")
        self.assertEqual(status["error"], "external fault test")
        self.assertEqual(calls, [("dev", "cleanup")])

    def _run_cleanup_case(self, failing: set[str], *, stop: bool = False) -> tuple[dict, list[str]]:
        """A finally with an if branch, a for loop, a nested try and a plain step."""
        calls: list[str] = []

        def call_device(device: str, action: str, params: dict[str, object]):
            calls.append(action)
            if action in failing:
                return {"ok": False, "error": f"{action} failed"}
            return {"ok": True, "result": None}

        def call(action: str) -> dict:
            return {"call": {"device": "dev", "action": action}}

        runtime = SequencerRuntime(
            call_device=call_device,
            get_telemetry=lambda d, s: None,
            set_stream_context=lambda *a: None,
        )
        runtime.load(
            parse_sequence(
                {
                    "version": 1,
                    "steps": [
                        {
                            "try": {
                                "do": [call("body"), {"sleep": 10} if stop else call("more")],
                                "finally": [
                                    {"if": {"condition": True, "then": [call("if_1"), call("if_2")]}},
                                    {"for": {"bind": "x", "in": [1, 2], "do": [call("loop")]}},
                                    {"try": {"do": [call("inner")], "finally": [call("inner_finally")]}},
                                    call("last"),
                                ],
                            }
                        }
                    ],
                }
            )
        )
        runtime.start()
        if stop:
            runtime.tick()
            runtime.tick()
            runtime.request_stop()
        while runtime.state == "RUNNING":
            runtime.tick()
        return runtime.status(), calls

    CLEANUP = ["if_1", "if_2", "loop", "loop", "inner", "inner_finally", "last"]

    def test_finally_control_flow_runs_after_a_body_failure(self) -> None:
        # Regression: if/for/try inside finally were discarded on error, so
        # conditional cleanup (disarm, restore, release) never ran.
        status, calls = self._run_cleanup_case({"body"})
        self.assertEqual(status["state"], "ERROR")
        self.assertEqual(status["error"], "body failed")
        self.assertEqual(calls, ["body", *self.CLEANUP])

    def test_finally_control_flow_runs_after_a_stop(self) -> None:
        status, calls = self._run_cleanup_case(set(), stop=True)
        self.assertEqual(status["state"], "STOPPED")
        self.assertEqual(calls, ["body", *self.CLEANUP])

    def test_finally_continues_after_one_of_its_own_steps_fails(self) -> None:
        # Without a body error the failing cleanup step must not drop the rest.
        status, calls = self._run_cleanup_case({"if_1"})
        self.assertEqual(status["state"], "ERROR")
        self.assertEqual(status["error"], "if_1 failed")
        self.assertEqual(calls, ["body", "more", *self.CLEANUP])
        # After a body error, a failing cleanup step is recorded alongside it.
        status, calls = self._run_cleanup_case({"body", "inner"})
        self.assertIn("body failed", str(status["error"]))
        self.assertIn("inner failed", str(status["error"]))
        self.assertEqual(calls, ["body", *self.CLEANUP])

    def test_try_finally_cleanup_failure_preserves_original_error(self) -> None:
        calls: list[tuple[str, str]] = []

        def call_device(device: str, action: str, params: dict[str, object]):
            calls.append((device, action))
            if action == "body":
                return {"ok": False, "error": "body failed"}
            return {"ok": False, "error": "cleanup failed too"}

        runtime = SequencerRuntime(
            call_device=call_device,
            get_telemetry=lambda d, s: None,
            set_stream_context=lambda *a: None,
        )
        runtime.load(
            parse_sequence(
                {
                    "version": 1,
                    "steps": [
                        {
                            "try": {
                                "do": [{"call": {"device": "dev", "action": "body"}}],
                                "finally": [
                                    {"call": {"device": "dev", "action": "cleanup"}}
                                ],
                            }
                        }
                    ],
                }
            )
        )
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()
        status = runtime.status()
        self.assertEqual(status["state"], "ERROR")
        self.assertIn("body failed", str(status["error"]))
        self.assertIn("cleanup failed", str(status["error"]))

    def test_nested_try_finally_continues_outer_cleanup_after_inner_cleanup_failure(
        self,
    ) -> None:
        calls: list[tuple[str, str]] = []

        def call_device(device: str, action: str, params: dict[str, object]):
            calls.append((device, action))
            if action == "body":
                return {"ok": False, "error": "body failed"}
            if action == "inner_cleanup":
                return {"ok": False, "error": "inner cleanup failed"}
            return {"ok": True, "result": None}

        runtime = SequencerRuntime(
            call_device=call_device,
            get_telemetry=lambda d, s: None,
            set_stream_context=lambda *a: None,
        )
        runtime.load(
            parse_sequence(
                {
                    "version": 1,
                    "steps": [
                        {
                            "try": {
                                "do": [
                                    {
                                        "try": {
                                            "do": [
                                                {
                                                    "call": {
                                                        "device": "dev",
                                                        "action": "body",
                                                    }
                                                }
                                            ],
                                            "finally": [
                                                {
                                                    "call": {
                                                        "device": "dev",
                                                        "action": "inner_cleanup",
                                                    }
                                                }
                                            ],
                                        }
                                    }
                                ],
                                "finally": [
                                    {
                                        "call": {
                                            "device": "dev",
                                            "action": "outer_cleanup",
                                        }
                                    }
                                ],
                            }
                        }
                    ],
                }
            )
        )
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()
        status = runtime.status()
        self.assertEqual(status["state"], "ERROR")
        self.assertIn("body failed", str(status["error"]))
        self.assertIn("inner cleanup failed", str(status["error"]))
        self.assertEqual(
            calls,
            [
                ("dev", "body"),
                ("dev", "inner_cleanup"),
                ("dev", "outer_cleanup"),
            ],
        )

    def test_try_finally_restores_use_vars_before_parent_cleanup(self) -> None:
        calls: list[tuple[str, str]] = []

        def call_device(device: str, action: str, params: dict[str, object]):
            calls.append((device, action))
            if action == "body":
                return {"ok": False, "error": "body failed"}
            return {"ok": True, "result": None}

        helper = parse_sequence(
            {
                "version": 1,
                "vars": {"cleanup_device": "child"},
                "steps": [{"call": {"device": "dev", "action": "body"}}],
            }
        )
        main = parse_sequence(
            {
                "version": 1,
                "vars": {"cleanup_device": "parent"},
                "steps": [
                    {
                        "try": {
                            "do": [{"use": {"id": "helper"}}],
                            "finally": [
                                {
                                    "call": {
                                        "device": "${cleanup_device}",
                                        "action": "cleanup",
                                    }
                                }
                            ],
                        }
                    }
                ],
            }
        )
        runtime = SequencerRuntime(
            call_device=call_device,
            get_telemetry=lambda d, s: None,
            set_stream_context=lambda *a: None,
            resolve_use=lambda sequence_id: {"helper": helper}[sequence_id],
        )
        runtime.load(main)
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()
        self.assertEqual(runtime.status()["state"], "ERROR")
        self.assertEqual(calls, [("dev", "body"), ("parent", "cleanup")])


if __name__ == "__main__":
    unittest.main()
