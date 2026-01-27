import unittest

from experiment_control.manager import Manager


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
