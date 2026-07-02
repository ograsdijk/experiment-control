# ruff: noqa: E402

import sys
import time
from collections import deque
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.sequencer.ast import parse_sequence
from experiment_control.sequencer.runtime import SequencerRuntime


class _FakeAdaptiveController:
    def __init__(
        self,
        proposals: list[dict[str, object]],
        *,
        stop_after_tells: int | None = None,
    ) -> None:
        self._proposals = [dict(item) for item in proposals]
        self.tells: list[tuple[dict[str, object], dict[str, object]]] = []
        self._stop_after_tells = stop_after_tells

    def suggest(self) -> dict[str, object]:
        if not self._proposals:
            raise RuntimeError("no more fake adaptive proposals")
        return dict(self._proposals.pop(0))

    def tell(self, proposal: dict[str, object], trial: dict[str, object]) -> None:
        self.tells.append((dict(proposal), dict(trial)))

    def should_stop(self) -> bool:
        if self._stop_after_tells is None:
            return False
        return len(self.tells) >= self._stop_after_tells


class _AdaptiveTestRuntime(SequencerRuntime):
    def __init__(
        self,
        *,
        controller: _FakeAdaptiveController,
        call_device,
        get_telemetry,
    ) -> None:
        def set_stream_context(
            device_id: str, stream: str, context_id: int, fields: dict[str, object]
        ) -> None:
            del device_id, stream, context_id, fields
            return None

        super().__init__(
            call_device=call_device,
            get_telemetry=get_telemetry,
            set_stream_context=set_stream_context,
        )
        self._controller = controller

    def _create_adaptive_controller(
        self,
        step,
        *,
        rendered_controller,
        rendered_space,
    ):  # type: ignore[override]
        del step, rendered_controller, rendered_space
        return self._controller


class SequencerAdaptiveTests(unittest.TestCase):
    def test_adaptive_step_runs_trials_with_call_observations(self) -> None:
        samples = iter([1.0, 2.0, 3.0, 10.0, 11.0, 12.0])
        controller = _FakeAdaptiveController(
            [
                {"params_raw": {"x": 0.26}, "meta": {"trial_index": 0}},
                {"params_raw": {"x": 0.74}, "meta": {"trial_index": 1}},
            ]
        )

        def call_device(
            device_id: str, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            del params
            if device_id == "detector" and action == "sample":
                return {"ok": True, "result": next(samples)}
            return {"ok": True, "result": None}

        def get_telemetry(device_id: str, signal: str) -> dict[str, object] | None:
            del device_id, signal
            return None

        runtime = _AdaptiveTestRuntime(
            controller=controller,
            call_device=call_device,
            get_telemetry=get_telemetry,
        )
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {
                        "adaptive": {
                            "id": "call_observe",
                            "controller": {"kind": "adaptive.adaptive_grid_1d"},
                            "space": {
                                "x": {
                                    "type": "float",
                                    "min": 0.0,
                                    "max": 1.0,
                                    "step": 0.25,
                                    "snap": True,
                                    "origin": 0.0,
                                }
                            },
                            "bind": {"x": "scan_x", "trial_index": "trial_idx"},
                            "do": [
                                {
                                    "assign": {
                                        "applied_x": "${scan_x}",
                                        "last_trial_idx": "${trial_idx}",
                                    }
                                }
                            ],
                            "observe": {
                                "repeats": 3,
                                "metrics": {
                                    "signal": {
                                        "kind": "call",
                                        "config": {
                                            "device": "detector",
                                            "action": "sample",
                                        },
                                    }
                                },
                                "score": "${signal_mean}",
                            },
                            "stopping": {"max_trials": 2},
                        }
                    }
                ],
            }
        )

        runtime.load(spec)
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        status = runtime.status()
        self.assertEqual(status["state"], "STOPPED")
        self.assertEqual(status["env"].get("applied_x"), 0.75)
        self.assertEqual(status["env"].get("last_trial_idx"), 1)
        self.assertEqual(status["env"].get("signal_mean"), 11.0)
        self.assertEqual(status["env"].get("score"), 11.0)

        self.assertEqual(len(controller.tells), 2)
        first_proposal, first_trial = controller.tells[0]
        second_proposal, second_trial = controller.tells[1]
        self.assertEqual(first_proposal["params"]["x"], 0.25)
        self.assertEqual(second_proposal["params"]["x"], 0.75)
        self.assertEqual(first_trial["aggregates"]["signal"]["mean"], 2.0)
        self.assertEqual(second_trial["aggregates"]["signal"]["mean"], 11.0)
        self.assertEqual(first_trial["score"], 2.0)
        self.assertEqual(second_trial["score"], 11.0)

    def test_adaptive_step_renders_templated_space_bounds(self) -> None:
        controller = _FakeAdaptiveController(
            [
                {"params_raw": {"x": 1000000123.0}, "meta": {"trial_index": 0}},
            ]
        )

        def call_device(
            device_id: str, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            del device_id, action, params
            return {"ok": True, "result": 1.0}

        def get_telemetry(device_id: str, signal: str) -> dict[str, object] | None:
            del device_id, signal
            return None

        runtime = _AdaptiveTestRuntime(
            controller=controller,
            call_device=call_device,
            get_telemetry=get_telemetry,
        )
        spec = parse_sequence(
            {
                "version": 1,
                "vars": {
                    "center": 1000000000.0,
                    "span": 200.0,
                    "grid": 50.0,
                },
                "steps": [
                    {
                        "adaptive": {
                            "id": "templated_bounds",
                            "controller": {"kind": "adaptive.adaptive_grid_1d"},
                            "space": {
                                "x": {
                                    "type": "float",
                                    "min": "${center - span}",
                                    "max": "${center + span}",
                                    "step": "${grid}",
                                    "snap": True,
                                    "origin": "${center - span}",
                                }
                            },
                            "bind": {"x": "scan_x"},
                            "do": [{"assign": {"applied_x": "${scan_x}"}}],
                            "observe": {
                                "metrics": {
                                    "signal": {
                                        "kind": "call",
                                        "config": {
                                            "device": "detector",
                                            "action": "sample",
                                        },
                                    }
                                },
                                "score": "${signal}",
                            },
                            "stopping": {"max_trials": 1},
                        }
                    }
                ],
            }
        )

        runtime.load(spec)
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        self.assertEqual(runtime.status()["state"], "STOPPED")
        self.assertEqual(runtime.status()["env"].get("applied_x"), 1000000100.0)
        self.assertEqual(len(controller.tells), 1)
        proposal, trial = controller.tells[0]
        self.assertEqual(proposal["params_raw"]["x"], 1000000123.0)
        self.assertEqual(proposal["params"]["x"], 1000000100.0)
        self.assertEqual(trial["params"]["x"], 1000000100.0)

    def test_adaptive_step_respects_controller_should_stop(self) -> None:
        controller = _FakeAdaptiveController(
            [
                {"params_raw": {"x": 0.2}, "meta": {"trial_index": 0}},
                {"params_raw": {"x": 0.4}, "meta": {"trial_index": 1}},
            ],
            stop_after_tells=1,
        )

        def call_device(
            device_id: str, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            del device_id, action, params
            return {"ok": True, "result": 5.0}

        def get_telemetry(device_id: str, signal: str) -> dict[str, object] | None:
            del device_id, signal
            return None

        runtime = _AdaptiveTestRuntime(
            controller=controller,
            call_device=call_device,
            get_telemetry=get_telemetry,
        )
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {
                        "adaptive": {
                            "id": "controller_stop",
                            "controller": {"kind": "adaptive.adaptive_grid_1d"},
                            "space": {
                                "x": {
                                    "type": "float",
                                    "min": 0.0,
                                    "max": 1.0,
                                }
                            },
                            "bind": {"x": "scan_x"},
                            "do": [{"assign": {"applied_x": "${scan_x}"}}],
                            "observe": {
                                "metrics": {
                                    "signal": {
                                        "kind": "call",
                                        "config": {
                                            "device": "detector",
                                            "action": "sample",
                                        },
                                    }
                                },
                                "score": "${signal}",
                            },
                            "stopping": {"max_trials": 10},
                        }
                    }
                ],
            }
        )

        runtime.load(spec)
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        self.assertEqual(runtime.status()["state"], "STOPPED")
        self.assertEqual(len(controller.tells), 1)

    def test_adaptive_step_collects_state_and_telemetry_metrics(self) -> None:
        controller = _FakeAdaptiveController(
            [
                {"params_raw": {"x": 0.26}, "meta": {"trial_index": 0}},
                {"params_raw": {"x": 0.74}, "meta": {"trial_index": 1}},
            ]
        )
        now = time.monotonic()
        telemetry: dict[tuple[str, str], deque[dict[str, object]]] = {
            ("mirror", "x_actual"): deque(
                [
                    {"value": 0.24, "t_mono": now},
                    {"value": 0.76, "t_mono": now},
                ]
            ),
            ("detector", "brightness"): deque(
                [
                    {"value": 5.0, "t_mono": now},
                    {"value": 6.0, "t_mono": now},
                ]
            ),
        }

        def call_device(
            device_id: str, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            del device_id, action, params
            return {"ok": True, "result": None}

        def get_telemetry(device_id: str, signal: str) -> dict[str, object] | None:
            key = (device_id, signal)
            queue = telemetry.get(key)
            if not queue:
                return None
            return queue.popleft()

        runtime = _AdaptiveTestRuntime(
            controller=controller,
            call_device=call_device,
            get_telemetry=get_telemetry,
        )
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {
                        "adaptive": {
                            "id": "telemetry_state",
                            "controller": {"kind": "adaptive.adaptive_grid_1d"},
                            "space": {
                                "x": {
                                    "type": "float",
                                    "min": 0.0,
                                    "max": 1.0,
                                    "step": 0.25,
                                    "snap": True,
                                }
                            },
                            "bind": {"x": "scan_x"},
                            "state": {
                                "x_actual": {
                                    "kind": "telemetry",
                                    "config": {
                                        "device": "mirror",
                                        "signal": "x_actual",
                                    },
                                }
                            },
                            "do": [{"assign": {"applied_x": "${scan_x}"}}],
                            "observe": {
                                "metrics": {
                                    "brightness": {
                                        "kind": "telemetry",
                                        "config": {
                                            "device": "detector",
                                            "signal": "brightness",
                                        },
                                    }
                                },
                                "score": "${brightness}",
                            },
                            "stopping": {"max_trials": 2},
                        }
                    }
                ],
            }
        )

        runtime.load(spec)
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        self.assertEqual(runtime.status()["state"], "STOPPED")
        self.assertEqual(len(controller.tells), 2)
        first_trial = controller.tells[0][1]
        second_trial = controller.tells[1][1]
        self.assertEqual(first_trial["state"]["x_actual"], 0.24)
        self.assertEqual(second_trial["state"]["x_actual"], 0.76)
        self.assertEqual(first_trial["metrics"]["brightness"], 5.0)
        self.assertEqual(second_trial["metrics"]["brightness"], 6.0)

    def test_adaptive_step_waits_for_matching_analysis_output(self) -> None:
        controller = _FakeAdaptiveController(
            [
                {"params_raw": {"x": 0.5}, "meta": {"trial_index": 0}},
            ]
        )

        def call_device(
            device_id: str, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            del device_id, action, params
            return {"ok": True, "result": None}

        def get_telemetry(device_id: str, signal: str) -> dict[str, object] | None:
            del device_id, signal
            return None

        runtime = _AdaptiveTestRuntime(
            controller=controller,
            call_device=call_device,
            get_telemetry=get_telemetry,
        )
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {
                        "adaptive": {
                            "id": "analysis_wait",
                            "controller": {"kind": "adaptive.adaptive_grid_1d"},
                            "space": {
                                "x": {
                                    "type": "float",
                                    "min": 0.0,
                                    "max": 1.0,
                                }
                            },
                            "bind": {"x": "scan_x"},
                            "do": [
                                {
                                    "set_context": {
                                        "streams": [
                                            {"device": "trace1", "stream": "trace"}
                                        ],
                                        "fields": {"scan_x": "${scan_x}"},
                                    }
                                }
                            ],
                            "observe": {
                                "metrics": {
                                    "brightness": {
                                        "kind": "analysis_output",
                                        "config": {
                                            "workspace_id": "workspace-1",
                                            "output_id": "brightness",
                                            "require_current_context": True,
                                            "timeout_s": 0.5,
                                        },
                                    }
                                },
                                "score": "${brightness}",
                            },
                            "stopping": {"max_trials": 1},
                        }
                    }
                ],
            }
        )

        runtime.load(spec)
        runtime.start()
        for _ in range(5):
            runtime.tick()

        self.assertEqual(runtime.status()["state"], "RUNNING")
        self.assertEqual(len(controller.tells), 0)

        runtime.record_analysis_output(
            {
                "workspace_id": "workspace-1",
                "output_id": "brightness",
                "context_id": 99,
                "value": 1.0,
            }
        )
        runtime.tick()
        self.assertEqual(runtime.status()["state"], "RUNNING")
        self.assertEqual(len(controller.tells), 0)

        runtime.record_analysis_output(
            {
                "workspace_id": "workspace-1",
                "output_id": "brightness",
                "context_id": 0,
                "value": 7.5,
            }
        )
        while runtime.state == "RUNNING":
            runtime.tick()

        status = runtime.status()
        self.assertEqual(status["state"], "STOPPED")
        self.assertEqual(len(controller.tells), 1)
        proposal, trial = controller.tells[0]
        self.assertEqual(proposal["params"]["x"], 0.5)
        self.assertEqual(trial["context_id"], 0)
        self.assertEqual(trial["metrics"]["brightness"], 7.5)
        self.assertEqual(trial["score"], 7.5)

    def test_adaptive_step_can_warm_start_from_saved_trials(self) -> None:
        call_values = deque([5.0, 7.0])
        controller = _FakeAdaptiveController(
            [
                {"params_raw": {"x": 0.25}, "meta": {"trial_index": 0}},
            ]
        )

        def call_device(
            device_id: str, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            del device_id, action, params
            return {"ok": True, "result": call_values.popleft()}

        def get_telemetry(device_id: str, signal: str) -> dict[str, object] | None:
            del device_id, signal
            return None

        runtime = _AdaptiveTestRuntime(
            controller=controller,
            call_device=call_device,
            get_telemetry=get_telemetry,
        )
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {
                        "adaptive": {
                            "id": "reuse_scan",
                            "controller": {"kind": "adaptive.adaptive_grid_1d"},
                            "space": {
                                "x": {
                                    "type": "float",
                                    "min": 0.0,
                                    "max": 1.0,
                                }
                            },
                            "bind": {"x": "scan_x"},
                            "do": [{"assign": {"applied_x": "${scan_x}"}}],
                            "observe": {
                                "metrics": {
                                    "signal": {
                                        "kind": "call",
                                        "config": {
                                            "device": "detector",
                                            "action": "sample",
                                        },
                                    }
                                },
                                "score": "${signal}",
                            },
                            "stopping": {"max_trials": 1},
                        }
                    }
                ],
            }
        )

        runtime.load(spec)
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        adaptive_status = runtime.adaptive_status()
        self.assertEqual(
            adaptive_status["adaptive_studies"]["reuse_scan"]["trial_count"], 1
        )

        controller2 = _FakeAdaptiveController(
            [
                {"params_raw": {"x": 0.75}, "meta": {"trial_index": 0}},
            ]
        )
        runtime._controller = controller2
        spec2 = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {
                        "adaptive": {
                            "id": "reuse_scan",
                            "controller": {"kind": "adaptive.adaptive_grid_1d"},
                            "space": {
                                "x": {
                                    "type": "float",
                                    "min": 0.0,
                                    "max": 1.0,
                                }
                            },
                            "bind": {"x": "scan_x"},
                            "do": [{"assign": {"applied_x": "${scan_x}"}}],
                            "observe": {
                                "metrics": {
                                    "signal": {
                                        "kind": "call",
                                        "config": {
                                            "device": "detector",
                                            "action": "sample",
                                        },
                                    }
                                },
                                "score": "${signal}",
                            },
                            "stopping": {"max_trials": 2},
                        }
                    }
                ],
            }
        )
        runtime.load(spec2)
        runtime.start(adaptive={"reuse_scan": {"mode": "warm_start"}})
        while runtime.state == "RUNNING":
            runtime.tick()

        self.assertEqual(len(controller2.tells), 2)
        self.assertEqual(controller2.tells[0][1]["score"], 5.0)
        self.assertEqual(controller2.tells[1][1]["score"], 7.0)
        self.assertEqual(
            runtime.adaptive_status()["adaptive_studies"]["reuse_scan"]["trial_count"],
            2,
        )


class SequencerWaitUntilSampleCapTests(unittest.TestCase):
    def _runtime(self, values: list[float]) -> SequencerRuntime:
        samples = deque(values)

        def call_device(
            device_id: str, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            del device_id, action, params
            return {"ok": True, "result": samples.popleft()}

        def get_telemetry(device_id: str, signal: str) -> dict[str, object] | None:
            del device_id, signal
            return None

        return SequencerRuntime(
            call_device=call_device,
            get_telemetry=get_telemetry,
            set_stream_context=lambda *_args, **_kwargs: None,
        )

    def test_wait_until_reduce_max_samples_caps_retained_samples(self) -> None:
        runtime = self._runtime([1.0, 2.0, 3.0, 4.0])
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {
                        "wait_until": {
                            "every_s": 0.0,
                            "sample": {"call": {"device": "d", "action": "sample"}},
                            "reduce": {"method": "mean", "max_samples": 2},
                            "condition": {"gt": ["${sample}", 3.5]},
                        }
                    }
                ],
            }
        )
        runtime.load(spec)
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        self.assertEqual(runtime._env["samples"], [3.0, 4.0])  # noqa: SLF001
        self.assertEqual(runtime.state, "STOPPED")

    def test_wait_until_default_sample_cap_is_applied_without_window(self) -> None:
        runtime = self._runtime([1.0, 2.0, 3.0])
        runtime._start_wait_until(  # noqa: SLF001
            {
                "every_s": 0.0,
                "sample": {"call": {"device": "d", "action": "sample"}},
                "reduce": {"method": "mean", "max_samples": 2},
                "condition": {"gt": ["${sample}", 10.0]},
            }
        )
        assert runtime._wait_state is not None  # noqa: SLF001
        runtime._wait_state.samples = [(0.0, -1.0), (0.1, 0.0)]  # noqa: SLF001
        self.assertFalse(runtime._step_wait_until(time.monotonic()))  # noqa: SLF001
        self.assertEqual([value for _, value in runtime._wait_state.samples], [0.0, 1.0])  # noqa: SLF001

    def test_wait_until_caps_samples_without_reduce_spec(self) -> None:
        runtime = self._runtime([1.0])
        runtime._start_wait_until(  # noqa: SLF001
            {
                "every_s": 0.0,
                "sample": {"call": {"device": "d", "action": "sample"}},
                "condition": {"gt": ["${sample}", 10.0]},
            }
        )
        assert runtime._wait_state is not None  # noqa: SLF001
        runtime._wait_state.max_samples = 2  # noqa: SLF001
        runtime._wait_state.samples = [(0.0, -1.0), (0.1, 0.0)]  # noqa: SLF001
        self.assertFalse(runtime._step_wait_until(time.monotonic()))  # noqa: SLF001
        self.assertEqual([value for _, value in runtime._wait_state.samples], [0.0, 1.0])  # noqa: SLF001

    def test_wait_until_renders_templated_numbers_before_coercion(self) -> None:
        now = time.monotonic()

        def get_process_telemetry(
            process_id: str, signal: str
        ) -> dict[str, object] | None:
            self.assertEqual(process_id, "spb_microwave")
            self.assertEqual(signal, "locked")
            return {"value": 1.0, "t_mono": now}

        runtime = SequencerRuntime(
            call_device=lambda *_args, **_kwargs: {"ok": True, "result": None},
            get_telemetry=lambda *_args, **_kwargs: None,
            set_stream_context=lambda *_args, **_kwargs: None,
            get_process_telemetry=get_process_telemetry,
        )
        runtime.load(
            parse_sequence(
                {
                    "version": 1,
                    "vars": {
                        "prelock_timeout_s": 5.0,
                        "poll_s": 0.0,
                        "stable_s": 0.0,
                        "max_age_s": 1.0,
                        "reduce_window_s": 0.5,
                        "reduce_max_samples": 3,
                    },
                    "steps": [
                        {
                            "wait_until": {
                                "timeout_s": "${prelock_timeout_s}",
                                "every_s": "${poll_s}",
                                "stable_for_s": "${stable_s}",
                                "sample": {
                                    "telemetry": {
                                        "process": "spb_microwave",
                                        "signal": "locked",
                                        "max_age_s": "${max_age_s}",
                                    }
                                },
                                "reduce": {
                                    "method": "mean",
                                    "window_s": "${reduce_window_s}",
                                    "max_samples": "${reduce_max_samples}",
                                },
                                "condition": {"ge": ["${sample_reduced}", 1.0]},
                            }
                        }
                    ],
                }
            )
        )
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        self.assertEqual(runtime.state, "STOPPED")
        self.assertEqual(runtime._env["sample"], 1.0)  # noqa: SLF001
        self.assertEqual(runtime._env["sample_reduced"], 1.0)  # noqa: SLF001
        self.assertIsNone(runtime._wait_state)  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
