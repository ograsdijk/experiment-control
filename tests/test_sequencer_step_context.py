# ruff: noqa: E402

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.sequencer.ast import load_sequence_yaml
from experiment_control.sequencer.runtime import SequencerRuntime
from experiment_control.sequencer.sequencer import _build_step_line_map
from experiment_control.sequencer.source_info import build_step_source_info


def _runtime_for_errors() -> SequencerRuntime:
    def call_device(
        device_id: str, action: str, params: dict[str, object]
    ) -> dict[str, object]:
        del device_id, params
        if action == "cleanup":
            return {"ok": False, "error": {"code": "cleanup_failed", "message": "cleanup timeout"}}
        return {"ok": False, "error": {"code": "timeout", "message": "body timeout"}}

    def get_telemetry(device_id: str, signal: str) -> dict[str, object] | None:
        del device_id, signal
        return None

    def set_stream_context(
        device_id: str, stream: str, context_id: int, fields: dict[str, object]
    ) -> None:
        del device_id, stream, context_id, fields

    return SequencerRuntime(
        call_device=call_device,
        get_telemetry=get_telemetry,
        set_stream_context=set_stream_context,
    )


def _load_yaml(runtime: SequencerRuntime, text: str, *, source: str = "test.yaml") -> None:
    spec = load_sequence_yaml(text)
    runtime.load(
        spec,
        step_source_info=build_step_source_info(
            spec,
            source=source,
            line_map=_build_step_line_map(text),
        ),
    )


class SequencerStepContextTests(unittest.TestCase):
    def test_call_failure_reports_step_line_path_and_target(self) -> None:
        runtime = _runtime_for_errors()
        _load_yaml(
            runtime,
            """
version: 1
steps:
  - call:
      device: fs740
      action: timestamp
      params: {}
""".lstrip(),
        )
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        status = runtime.status()
        self.assertEqual(status.get("state"), "ERROR")
        self.assertIn("body timeout", str(status.get("error")))
        detail = status.get("error_detail")
        self.assertIsInstance(detail, dict)
        assert isinstance(detail, dict)
        self.assertIn("fs740.timestamp", str(detail.get("formatted")))
        step = detail.get("step")
        self.assertIsInstance(step, dict)
        assert isinstance(step, dict)
        self.assertEqual(step.get("path"), "steps[0]")
        self.assertEqual(step.get("line"), 3)
        self.assertEqual(step.get("device"), "fs740")
        self.assertEqual(step.get("action"), "timestamp")

    def test_try_finally_line_map_descends_into_finally(self) -> None:
        text = """
version: 1
steps:
  - try:
      do:
        - call:
            device: fs740
            action: body
      finally:
        - call:
            device: fs740
            action: cleanup
""".lstrip()
        line_map = _build_step_line_map(text)
        self.assertEqual(line_map.get("steps[0]"), 3)
        self.assertEqual(line_map.get("steps[0].try.do[0]"), 5)
        self.assertEqual(line_map.get("steps[0].try.finally[0]"), 9)

    def test_try_finally_cleanup_error_does_not_hide_original_failure(self) -> None:
        runtime = _runtime_for_errors()
        _load_yaml(
            runtime,
            """
version: 1
steps:
  - try:
      do:
        - call:
            device: fs740
            action: body
      finally:
        - call:
            device: fs740
            action: cleanup
""".lstrip(),
        )
        runtime.start()
        while runtime.state == "RUNNING":
            runtime.tick()

        status = runtime.status()
        self.assertEqual(status.get("state"), "ERROR")
        self.assertIn("body timeout", str(status.get("error")))
        detail = status.get("error_detail")
        self.assertIsInstance(detail, dict)
        assert isinstance(detail, dict)
        self.assertIn("body timeout", str(detail.get("formatted")))
        cleanup_errors = detail.get("cleanup_errors")
        self.assertIsInstance(cleanup_errors, list)
        assert isinstance(cleanup_errors, list)
        self.assertEqual(len(cleanup_errors), 1)
        self.assertIn("cleanup timeout", str(cleanup_errors[0].get("formatted")))
        cleanup_step = cleanup_errors[0].get("step")
        self.assertIsInstance(cleanup_step, dict)
        assert isinstance(cleanup_step, dict)
        self.assertEqual(cleanup_step.get("branch"), "finally")
        self.assertEqual(cleanup_step.get("path"), "steps[0].try.finally[0]")


if __name__ == "__main__":
    unittest.main()
