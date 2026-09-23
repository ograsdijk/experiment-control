# ruff: noqa: E402, SLF001

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.contracts.context_fields import SEQUENCER_RUN_ID_FIELD
from experiment_control.sequencer.ast import (
    SequenceSpec,
    SetContextStep,
    load_sequence_yaml,
)
from experiment_control.sequencer.runtime import SequencerRuntime
from experiment_control.sequencer.sequencer import SequencerProcess

_STREAMS = [{"device": "scope", "stream": "trace"}]


def _spec(fields: dict[str, object], *, n_steps: int = 1) -> SequenceSpec:
    return SequenceSpec(
        version=1,
        meta={},
        vars={},
        steps=[
            SetContextStep(streams=list(_STREAMS), fields=dict(fields))
            for _ in range(n_steps)
        ],
        context_columns=None,
    )


def _run_to_completion(runtime: SequencerRuntime) -> None:
    runtime.start()
    for _ in range(1000):
        if runtime.state != "RUNNING":
            return
        runtime.tick()
    raise AssertionError("sequence did not finish")


def _sync_runtime(calls: list[tuple[int, dict[str, object]]]) -> SequencerRuntime:
    return SequencerRuntime(
        call_device=lambda *a, **k: {"ok": True, "result": None},
        get_telemetry=lambda *a, **k: None,
        set_stream_context=lambda device, stream, ctx_id, fields: calls.append(
            (ctx_id, dict(fields))
        ),
        expect_streams=lambda *a, **k: None,
    )


def _async_runtime(calls: list[tuple[int, dict[str, object]]]) -> SequencerRuntime:
    def begin_set_context(streams, context_id, fields):  # noqa: ANN001, ANN202
        del streams
        calls.append((context_id, dict(fields)))
        return None

    return SequencerRuntime(
        call_device=lambda *a, **k: {"ok": True, "result": None},
        get_telemetry=lambda *a, **k: None,
        set_stream_context=lambda *a, **k: None,
        expect_streams=lambda *a, **k: None,
        begin_set_context=begin_set_context,
        poll_set_context=lambda state, now: (True, None),
    )


class RuntimeRunIdInjectionTests(unittest.TestCase):
    def test_sync_path_injects_run_id_per_start(self) -> None:
        calls: list[tuple[int, dict[str, object]]] = []
        runtime = _sync_runtime(calls)
        runtime.load(_spec({"freq_hz": 1.0}, n_steps=2))

        _run_to_completion(runtime)
        _run_to_completion(runtime)

        self.assertEqual(
            calls,
            [
                (0, {"freq_hz": 1.0, SEQUENCER_RUN_ID_FIELD: 1}),
                (1, {"freq_hz": 1.0, SEQUENCER_RUN_ID_FIELD: 1}),
                (2, {"freq_hz": 1.0, SEQUENCER_RUN_ID_FIELD: 2}),
                (3, {"freq_hz": 1.0, SEQUENCER_RUN_ID_FIELD: 2}),
            ],
        )
        # context_id stays a single monotonic counter across runs.
        self.assertEqual(runtime.status()["last_context_id"], 3)

    def test_async_path_receives_identical_fields(self) -> None:
        sync_calls: list[tuple[int, dict[str, object]]] = []
        async_calls: list[tuple[int, dict[str, object]]] = []
        sync_runtime = _sync_runtime(sync_calls)
        async_runtime = _async_runtime(async_calls)
        for runtime in (sync_runtime, async_runtime):
            runtime.load(_spec({"freq_hz": 1.0}))
            _run_to_completion(runtime)
            _run_to_completion(runtime)

        self.assertEqual(async_calls, sync_calls)
        self.assertEqual(
            [fields[SEQUENCER_RUN_ID_FIELD] for _, fields in async_calls], [1, 2]
        )

    def test_run_id_survives_loading_a_different_sequence(self) -> None:
        calls: list[tuple[int, dict[str, object]]] = []
        runtime = _sync_runtime(calls)
        runtime.load(_spec({"a": 1}))
        _run_to_completion(runtime)
        # Loaded but never started: must not consume a run id.
        runtime.load(_spec({"b": 2}))
        runtime.load(_spec({"c": 3}))
        _run_to_completion(runtime)

        self.assertEqual(
            [fields[SEQUENCER_RUN_ID_FIELD] for _, fields in calls], [1, 2]
        )

    def test_programmatic_spoofed_value_is_overwritten_and_step_not_mutated(
        self,
    ) -> None:
        calls: list[tuple[int, dict[str, object]]] = []
        runtime = _sync_runtime(calls)
        spec = _spec({"freq_hz": 1.0, SEQUENCER_RUN_ID_FIELD: 99})
        runtime.load(spec)
        _run_to_completion(runtime)

        self.assertEqual(calls[0][1][SEQUENCER_RUN_ID_FIELD], 1)
        step = spec.steps[0]
        assert isinstance(step, SetContextStep)
        self.assertEqual(step.fields, {"freq_hz": 1.0, SEQUENCER_RUN_ID_FIELD: 99})


class ReservedFieldValidationTests(unittest.TestCase):
    def test_context_columns_cannot_declare_run_id(self) -> None:
        with self.assertRaisesRegex(TypeError, "sequencer_run_id.*reserved"):
            load_sequence_yaml(
                "version: 1\n"
                "context_columns:\n"
                "  freq_hz: float64\n"
                "  sequencer_run_id: int64\n"
                "steps: []\n"
            )

    def test_set_context_fields_cannot_set_run_id(self) -> None:
        with self.assertRaisesRegex(TypeError, "sequencer_run_id.*reserved"):
            load_sequence_yaml(
                "version: 1\n"
                "steps:\n"
                "  - repeat:\n"
                "      count: 2\n"
                "      do:\n"
                "        - set_context:\n"
                "            streams: [{device: scope, stream: trace}]\n"
                "            fields: {freq_hz: 1.0, sequencer_run_id: 7}\n"
            )


_EXPLICIT_YAML = (
    "version: 1\n"
    "context_columns:\n"
    "  freq_step_index: int64\n"
    "steps:\n"
    "  - set_context:\n"
    "      streams: [{device: scope, stream: trace}]\n"
    "      fields: {freq_step_index: 0}\n"
)
_NO_SCHEMA_YAML = (
    "version: 1\n"
    "steps:\n"
    "  - set_context:\n"
    "      streams: [{device: scope, stream: trace}]\n"
    "      fields: {freq_hz: 1.0}\n"
)
_EMPTY_SCHEMA_YAML = "version: 1\ncontext_columns: {}\n" + _NO_SCHEMA_YAML[len("version: 1\n"):]


def _build_process() -> tuple[SequencerProcess, list[dict[str, object]]]:
    process = object.__new__(SequencerProcess)
    process._runtime = SequencerRuntime(
        call_device=lambda *a, **k: {"ok": True, "result": None},
        get_telemetry=lambda *a, **k: None,
        set_stream_context=lambda *a, **k: None,
    )
    process._sequence_library = None
    process._sequence_library_path = None
    process._sequence_library_error = None
    process._sequence_library_warnings = []
    process._autoload_error = None
    process._autoload_error_ts_wall = None
    process._autoload_error_source = None
    process._last_progress_event_signature = None
    process._last_progress_event_mono = 0.0
    process._active_sequence_id = None
    process._context_columns = None
    process._loaded_sequence_spec = None
    process._loaded_sequence_source = None
    process._loaded_sequence_source_kind = None
    process._loaded_sequence_text = None
    process._loaded_sequence_run_id = None
    process._check_start_preconditions = lambda req: None  # type: ignore[method-assign]
    published: list[dict[str, object]] = []
    process._publish_lifecycle_event = (  # type: ignore[method-assign]
        lambda **kwargs: published.append(kwargs)
    )
    return process, published


def _load_text(process: SequencerProcess, text: str) -> None:
    process._set_loaded_sequence(
        spec=load_sequence_yaml(text),
        text=text,
        source="test.yaml",
        source_kind="rpc",
        active_sequence_id=None,
    )


class EffectiveContextColumnsTests(unittest.TestCase):
    def test_explicit_schema_gains_run_id_consistently(self) -> None:
        process, published = _build_process()
        spec = load_sequence_yaml(_EXPLICIT_YAML)
        process._set_loaded_sequence(
            spec=spec,
            text=_EXPLICIT_YAML,
            source="test.yaml",
            source_kind="rpc",
            active_sequence_id=None,
        )
        expected = {"freq_step_index": "int64", SEQUENCER_RUN_ID_FIELD: "int64"}

        status = process._rpc_sequencer_status({"params": {}})
        self.assertEqual(status["result"]["context_columns"], expected)

        start = process._rpc_sequencer_start({"params": {}})
        self.assertTrue(start["ok"])
        start_events = [e for e in published if e["event"] == "start"]
        self.assertEqual(start_events[0]["payload"]["context_columns"], expected)  # type: ignore[index]
        self.assertEqual(start_events[0]["payload"]["run_id"], 1)  # type: ignore[index]

        # The user's spec and stored YAML are untouched.
        self.assertEqual(spec.context_columns, {"freq_step_index": "int64"})
        loaded = process._rpc_sequencer_loaded_yaml({"params": {}})["result"]
        self.assertEqual(loaded["text"], _EXPLICIT_YAML)

    def test_load_ok_payload_uses_effective_schema(self) -> None:
        process, published = _build_process()
        process._rpc_sequencer_load({"params": {"text": _EXPLICIT_YAML}})

        load_events = [e for e in published if e["event"] == "load_ok"]
        self.assertEqual(
            load_events[0]["payload"]["context_columns"],  # type: ignore[index]
            {"freq_step_index": "int64", SEQUENCER_RUN_ID_FIELD: "int64"},
        )

    def test_missing_or_empty_schema_stays_none_for_inference(self) -> None:
        for text in (_NO_SCHEMA_YAML, _EMPTY_SCHEMA_YAML):
            with self.subTest(text=text):
                process, _published = _build_process()
                _load_text(process, text)
                status = process._rpc_sequencer_status({"params": {}})
                self.assertIsNone(status["result"]["context_columns"])


class LoadedYamlRunIdTests(unittest.TestCase):
    def test_loaded_yaml_reports_run_id_of_loaded_text(self) -> None:
        process, _published = _build_process()
        _load_text(process, _NO_SCHEMA_YAML)
        loaded = process._rpc_sequencer_loaded_yaml({"params": {}})["result"]
        self.assertIsNone(loaded["run_id"])

        process._rpc_sequencer_start({"params": {}})
        loaded = process._rpc_sequencer_loaded_yaml({"params": {}})["result"]
        self.assertEqual(loaded["run_id"], 1)

        process._runtime.fail("stop for reload")
        _load_text(process, _EXPLICIT_YAML)
        loaded = process._rpc_sequencer_loaded_yaml({"params": {}})["result"]
        self.assertIsNone(loaded["run_id"])


if __name__ == "__main__":
    unittest.main()
