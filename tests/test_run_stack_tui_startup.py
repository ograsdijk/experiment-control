# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.cli.run_stack import _wait_for_manager_ready


class _FakeProc:
    def __init__(self, polls: list[int | None]) -> None:
        self._polls = list(polls)
        self._last: int | None = None

    def poll(self) -> int | None:
        if self._polls:
            self._last = self._polls.pop(0)
        return self._last


class TuiStartupWaitTests(unittest.TestCase):
    def test_wait_for_manager_ready_succeeds_after_probe(self) -> None:
        calls: list[tuple[str, int, str | None]] = []
        answers = iter([False, True])

        def probe(
            manager_rpc: str, *, timeout_ms: int, expected_instance_id: str | None
        ) -> bool:
            calls.append((manager_rpc, timeout_ms, expected_instance_id))
            return next(answers)

        ok, err = _wait_for_manager_ready(
            manager_rpc="tcp://127.0.0.1:6000",
            manager_proc=_FakeProc([None, None, None]),  # type: ignore[arg-type]
            expected_instance_id="vacuum",
            startup_delay_s=0.0,
            startup_timeout_s=1.0,
            probe_timeout_ms=250,
            poll_interval_s=0.0,
            probe_fn=probe,
            clock_fn=lambda: 0.0,
        )

        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(
            calls,
            [
                ("tcp://127.0.0.1:6000", 250, "vacuum"),
                ("tcp://127.0.0.1:6000", 250, "vacuum"),
            ],
        )

    def test_wait_for_manager_ready_fails_when_process_exits_early(self) -> None:
        probe_calls = 0

        def probe(
            manager_rpc: str, *, timeout_ms: int, expected_instance_id: str | None
        ) -> bool:
            nonlocal probe_calls
            probe_calls += 1
            return False

        ok, err = _wait_for_manager_ready(
            manager_rpc="tcp://127.0.0.1:6000",
            manager_proc=_FakeProc([5]),  # type: ignore[arg-type]
            expected_instance_id="vacuum",
            startup_delay_s=0.0,
            startup_timeout_s=1.0,
            probe_timeout_ms=250,
            poll_interval_s=0.0,
            probe_fn=probe,
            clock_fn=lambda: 0.0,
        )

        self.assertFalse(ok)
        self.assertIsNotNone(err)
        assert err is not None
        self.assertIn("exit code 5", err)
        self.assertEqual(probe_calls, 0)

    def test_wait_for_manager_ready_times_out(self) -> None:
        probe_calls = 0

        def probe(
            manager_rpc: str, *, timeout_ms: int, expected_instance_id: str | None
        ) -> bool:
            nonlocal probe_calls
            probe_calls += 1
            return False

        ok, err = _wait_for_manager_ready(
            manager_rpc="tcp://127.0.0.1:6000",
            manager_proc=_FakeProc([None, None]),  # type: ignore[arg-type]
            expected_instance_id="vacuum",
            startup_delay_s=0.0,
            startup_timeout_s=0.0,
            probe_timeout_ms=250,
            poll_interval_s=0.0,
            probe_fn=probe,
            clock_fn=lambda: 0.0,
        )

        self.assertFalse(ok)
        self.assertIsNotNone(err)
        assert err is not None
        self.assertIn("did not become ready", err)
        self.assertEqual(probe_calls, 1)

    def test_wait_for_manager_ready_applies_initial_delay(self) -> None:
        sleeps: list[float] = []

        def probe(
            manager_rpc: str, *, timeout_ms: int, expected_instance_id: str | None
        ) -> bool:
            return True

        ok, err = _wait_for_manager_ready(
            manager_rpc="tcp://127.0.0.1:6000",
            manager_proc=_FakeProc([None, None]),  # type: ignore[arg-type]
            expected_instance_id="vacuum",
            startup_delay_s=0.5,
            startup_timeout_s=1.0,
            probe_timeout_ms=250,
            poll_interval_s=0.0,
            probe_fn=probe,
            sleep_fn=sleeps.append,
            clock_fn=lambda: 0.0,
        )

        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(sleeps, [0.5])


if __name__ == "__main__":
    unittest.main()
