# ruff: noqa: E402

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager import Manager


class _PopenStub:
    def __init__(self, pid: int) -> None:
        self.pid = int(pid)


class _GuardStub:
    def __init__(self, *, available: bool, init_error: str | None = None) -> None:
        self.available = bool(available)
        self.init_error = init_error

    def adopt_popen(self, popen: _PopenStub) -> None:
        del popen
        raise RuntimeError("job attach failed")


class ManagerProcessGuardTests(unittest.TestCase):
    def test_adopt_with_process_guard_records_failure(self) -> None:
        mgr = object.__new__(Manager)
        mgr._process_guard = _GuardStub(available=True)  # type: ignore[attr-defined]
        mgr._process_guard_attach_failures = 0  # type: ignore[attr-defined]
        mgr._process_guard_last_error = None  # type: ignore[attr-defined]
        mgr._emit_log = mock.Mock()  # type: ignore[attr-defined]

        Manager._adopt_with_process_guard(  # type: ignore[arg-type]
            mgr,
            _PopenStub(pid=4321),
            target_kind="driver",
            target_id="trace1",
        )
        self.assertEqual(mgr._process_guard_attach_failures, 1)  # type: ignore[attr-defined]
        self.assertIn("attach failed", str(mgr._process_guard_last_error))  # type: ignore[attr-defined]
        mgr._emit_log.assert_called_once()  # type: ignore[attr-defined]

    def test_manager_identity_includes_process_guard_state(self) -> None:
        mgr = object.__new__(Manager)
        mgr._instance_id = "inst-x"  # type: ignore[attr-defined]
        mgr._started_t_wall = 1.0  # type: ignore[attr-defined]
        mgr._started_t_mono = 2.0  # type: ignore[attr-defined]
        mgr._last_orphan_cleanup = None  # type: ignore[attr-defined]
        mgr._process_guard = _GuardStub(available=False, init_error="init failed")  # type: ignore[attr-defined]
        mgr._process_guard_init_error = "init failed"  # type: ignore[attr-defined]
        mgr._process_guard_attach_failures = 3  # type: ignore[attr-defined]
        mgr._process_guard_last_error = "attach failed"  # type: ignore[attr-defined]

        with mock.patch(
            "experiment_control.manager.read_instance_lock_status",
            return_value={"status": "active", "owner_pid": 1, "owner_alive": True},
        ):
            resp = Manager._route_internal_request(  # type: ignore[arg-type]
                mgr,
                {"type": "manager.identity"},
            )
        self.assertTrue(resp.get("ok"))
        result = resp.get("result", {})
        pg = result.get("process_guard", {})
        self.assertIsInstance(pg, dict)
        self.assertFalse(bool(pg.get("enabled")))
        self.assertEqual(pg.get("init_error"), "init failed")
        self.assertEqual(pg.get("attach_failures"), 3)
        self.assertEqual(pg.get("last_attach_error"), "attach failed")


if __name__ == "__main__":
    unittest.main()
