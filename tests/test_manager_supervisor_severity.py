import unittest

from experiment_control.manager import Manager, ManagedProcessState, ProcessHandle, ProcessSpec


class _DummyManager:
    _normalize_log_severity = staticmethod(Manager._normalize_log_severity)


def _infer(stream: str, message: str, *, reader_error: bool = False) -> str:
    return Manager._supervisor_infer_severity(
        _DummyManager(),
        stream=stream,
        message=message,
        reader_error=reader_error,
    )


class SupervisorSeverityInferenceTests(unittest.TestCase):
    def test_supervisor_records_raw_and_emitted_process_logs(self) -> None:
        mgr = object.__new__(Manager)
        handle = ProcessHandle(
            spec=ProcessSpec(process_id="influx_writer", argv=["python"]),
            state=ManagedProcessState.RUNNING,
        )
        mgr._processes = {"influx_writer": handle}  # type: ignore[attr-defined]
        mgr._devices = {}  # type: ignore[attr-defined]

        raw = {
            "source_kind": "process",
            "source_id": "influx_writer",
            "stream": "stderr",
            "pid": 123,
            "message": "ERROR write failed",
        }
        Manager._record_supervisor_raw_log(mgr, raw)  # type: ignore[arg-type]
        Manager._record_supervisor_emitted_log(mgr, raw, severity="error")  # type: ignore[arg-type]

        self.assertEqual(handle.supervisor_stderr_tail[-1]["message"], "ERROR write failed")
        self.assertEqual(handle.supervisor_stderr_tail[-1]["stream"], "stderr")
        self.assertEqual(handle.supervisor_log_tail[-1]["severity"], "error")
        self.assertEqual(handle.supervisor_log_tail[-1]["pid"], 123)

    def test_supervisor_infers_bracket_prefixed_error(self) -> None:
        sev = _infer("stderr", "[start_driver] error: No module named 'linien_client'")
        self.assertEqual(sev, "error")

    def test_supervisor_infers_bracket_prefixed_warning(self) -> None:
        sev = _infer("stderr", "[watchdog] warning: telemetry stale")
        self.assertEqual(sev, "warning")

    def test_supervisor_fallback_stderr_warning(self) -> None:
        sev = _infer("stderr", "[start_driver] this is plain stderr text")
        self.assertEqual(sev, "warning")

    def test_supervisor_fallback_stdout_info(self) -> None:
        sev = _infer("stdout", "[start_driver] this is plain stdout text")
        self.assertEqual(sev, "info")


if __name__ == "__main__":
    unittest.main()
