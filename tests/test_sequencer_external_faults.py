# ruff: noqa: E402

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.sequencer.runtime import SequencerRuntime
from experiment_control.sequencer.ast import SequenceSpec
from experiment_control.sequencer.sequencer import (
    _should_trigger_external_sequencer_fault,
)


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


class SequencerExternalFaultFilterTests(unittest.TestCase):
    def test_driver_warning_triggers(self) -> None:
        ok, reason = _should_trigger_external_sequencer_fault(
            {
                "severity": "warning",
                "source_kind": "driver",
                "source_id": "synth0",
                "message": "device not reachable",
            }
        )
        self.assertTrue(ok)
        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("driver:synth0", reason)

    def test_driver_info_does_not_trigger(self) -> None:
        ok, reason = _should_trigger_external_sequencer_fault(
            {
                "severity": "info",
                "source_kind": "driver",
                "source_id": "synth0",
                "message": "connected",
            }
        )
        self.assertFalse(ok)
        self.assertIsNone(reason)

    def test_hdf_writer_error_triggers(self) -> None:
        ok, reason = _should_trigger_external_sequencer_fault(
            {
                "severity": "error",
                "source_kind": "process",
                "source_id": "hdf_writer",
                "message": "write failed",
            }
        )
        self.assertTrue(ok)
        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("process:hdf_writer", reason)

    def test_other_process_error_does_not_trigger(self) -> None:
        ok, reason = _should_trigger_external_sequencer_fault(
            {
                "severity": "error",
                "source_kind": "process",
                "source_id": "stream_analysis",
                "message": "operator error",
            }
        )
        self.assertFalse(ok)
        self.assertIsNone(reason)


class SequencerRuntimeFailTests(unittest.TestCase):
    def test_fail_forces_error_state(self) -> None:
        runtime = _build_runtime()
        runtime.load(
            SequenceSpec(
                version=1,
                meta={},
                vars={},
                steps=[],
                context_columns=None,
            )
        )
        runtime.start()
        runtime.fail("external fault test")
        status = runtime.status()
        self.assertEqual(status.get("state"), "ERROR")
        self.assertEqual(status.get("error"), "external fault test")


if __name__ == "__main__":
    unittest.main()
