# ruff: noqa: E402

import sys
import types
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.sequencer.ast import load_sequence_yaml
from experiment_control.sequencer.run_events import RunEventLog
from experiment_control.sequencer.runtime import SequencerRuntime
from experiment_control.sequencer.sequencer import SequencerProcess, _build_step_line_map
from experiment_control.sequencer.source_info import build_step_source_info


def _runtime(fail_action: str | None = None) -> SequencerRuntime:
    def call_device(device_id: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
        del device_id, params
        if action == fail_action:
            return {"ok": False, "error": {"code": "timeout", "message": f"{action} timed out"}}
        return {"ok": True, "result": None}

    return SequencerRuntime(
        call_device=call_device,
        get_telemetry=lambda device_id, signal: None,
        set_stream_context=lambda device_id, stream, ctx, fields: None,
        call_process=lambda process_id, action, params: {"ok": True, "result": None},
    )


def _load(runtime: SequencerRuntime, text: str) -> None:
    spec = load_sequence_yaml(text)
    runtime.load(
        spec,
        step_source_info=build_step_source_info(
            spec, source="test.yaml", line_map=_build_step_line_map(text, spec)
        ),
    )


def _run(runtime: SequencerRuntime, max_ticks: int = 200) -> None:
    for _ in range(max_ticks):
        if runtime.state != "RUNNING":
            return
        runtime.tick()


def _events(runtime: SequencerRuntime) -> list[dict[str, Any]]:
    return runtime.run_events()["events"]


SLEEPS = """
version: 1
steps:
  - sleep: 0.05
  - sleep: 0.05
""".lstrip()


class SequencerRunEventTests(unittest.TestCase):
    def test_pause_records_who_and_why_and_resume_clears_it(self) -> None:
        runtime = _runtime()
        _load(runtime, SLEEPS)
        runtime.start()
        runtime.tick()  # in the first sleep
        trigger = {"watchdog_id": "lock", "rule": "lock_fault", "severity": "critical", "trip_id": "t1"}
        runtime.request_pause(reason="Lock dropped", source="watchdog", trigger=trigger)
        runtime.tick()
        self.assertEqual(runtime.state, "PAUSED")
        pause = runtime.status()["pause"]
        self.assertEqual(pause["reason"], "Lock dropped")
        self.assertEqual(pause["source"], "watchdog")
        self.assertEqual(pause["trigger"], trigger)
        event = _events(runtime)[-1]
        self.assertEqual(event["kind"], "pause")
        self.assertEqual(event["severity"], "warning")
        self.assertEqual(event["message"], "Paused by watchdog: Lock dropped")
        self.assertEqual(event["step"]["line"], 3)
        self.assertEqual(event["trigger"], trigger)

        runtime.resume(source="operator")
        self.assertIsNone(runtime.status()["pause"])
        self.assertEqual(_events(runtime)[-1]["kind"], "resume")
        self.assertEqual(_events(runtime)[-1]["source"], "operator")

    def test_pause_step_reason_and_operator_pause(self) -> None:
        runtime = _runtime()
        _load(
            runtime,
            """
version: 1
steps:
  - pause: {reason: "Spot walk exhausted"}
  - sleep: 0.01
""".lstrip(),
        )
        runtime.start()
        runtime.tick()
        self.assertEqual(runtime.state, "PAUSED")
        pause = runtime.status()["pause"]
        self.assertEqual(pause["source"], "sequence")
        self.assertEqual(pause["reason"], "Spot walk exhausted")
        self.assertEqual(_events(runtime)[-1]["severity"], "info")

    def test_deliberate_pauses_are_info_and_missing_source_is_unknown(self) -> None:
        for source, expected in (("client", "Paused by client: calibrate"), (None, "Paused by unknown: calibrate")):
            runtime = _runtime()
            _load(runtime, SLEEPS)
            runtime.start()
            runtime.tick()
            runtime.request_pause(reason="calibrate", source=source)
            runtime.tick()
            event = _events(runtime)[-1]
            self.assertEqual(event["severity"], "info", source)
            self.assertEqual(event["message"], expected)

    def test_failed_step_is_recorded_on_its_line(self) -> None:
        runtime = _runtime(fail_action="broken")
        _load(
            runtime,
            """
version: 1
steps:
  - call: {device: d, action: ok}
  - call: {device: d, action: broken}
""".lstrip(),
        )
        runtime.start()
        _run(runtime)
        self.assertEqual(runtime.state, "ERROR")
        failed = [e for e in _events(runtime) if e["kind"] == "step_failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["severity"], "error")
        self.assertEqual(failed[0]["step"]["line"], 4)
        self.assertIn("broken timed out", failed[0]["message"])
        self.assertEqual(runtime.status()["run_events"]["errors"], 1)

    def test_external_warnings_attach_to_the_running_step_and_start_resets(self) -> None:
        runtime = _runtime()
        _load(runtime, SLEEPS)
        runtime.start()
        runtime.tick()
        for _ in range(3):
            runtime.note_external_log(
                severity="warning", message="ramp slow", source="process:nltl_ramp"
            )
        events = _events(runtime)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["count"], 3)
        self.assertEqual(events[0]["kind"], "log")
        self.assertEqual(events[0]["step"]["line"], 3)
        seq = runtime.status()["run_events"]["seq"]
        runtime.request_stop()
        _run(runtime)
        runtime.start()
        self.assertEqual(_events(runtime), [])
        self.assertGreater(runtime.status()["run_events"]["seq"], seq)

    def test_watchdog_gated_sequence_events_point_at_source_lines(self) -> None:
        runtime = _runtime(fail_action="broken")
        _load(
            runtime,
            """
version: 1
requires:
  watchdog_ids: [lock]
steps:
  - call: {device: d, action: broken}
""".lstrip(),
        )
        runtime.start()
        _run(runtime)
        failed = [e for e in _events(runtime) if e["kind"] == "step_failed"]
        self.assertEqual(failed[0]["step"]["line"], 5)


class RunEventLogTests(unittest.TestCase):
    def test_bounded_with_drop_count(self) -> None:
        log = RunEventLog(max_events=3)
        for i in range(5):
            log.record(severity="warning", kind="log", message=f"m{i}", elapsed_s=float(i))
        snap = log.snapshot()
        self.assertEqual([e["message"] for e in snap["events"]], ["m2", "m3", "m4"])
        self.assertEqual(snap["dropped"], 2)
        self.assertEqual(log.summary(latest=1)["latest"][0]["message"], "m4")


class SequencerExternalWarningTests(unittest.TestCase):
    def test_other_sources_are_noted_and_own_logs_skipped(self) -> None:
        noted: list[dict[str, Any]] = []
        fake = types.SimpleNamespace(
            _process_id="sequencer",
            _runtime=types.SimpleNamespace(note_external_log=lambda **kw: noted.append(kw)),
        )
        note = SequencerProcess._note_external_warning
        note(fake, {"severity": "warning", "source_kind": "process", "source_id": "watchdog", "message": "trip"})
        note(fake, {"severity": "info", "source_kind": "process", "source_id": "watchdog", "message": "fine"})
        note(fake, {"severity": "error", "source_kind": "process", "source_id": "sequencer", "message": "own"})
        self.assertEqual(noted, [{"severity": "warning", "message": "trip", "source": "process:watchdog"}])


if __name__ == "__main__":
    unittest.main()
