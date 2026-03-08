# ruff: noqa: E402

import io
import sys
from pathlib import Path
import unittest
from contextlib import redirect_stderr
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.cli.run_stack import _preflight_instance_cleanup
from experiment_control.manager import (
    ManagedProcessState,
    Manager,
    ProcessHandle,
    ProcessSpec,
)


def _build_handle(process_id: str = "hdf_writer") -> ProcessHandle:
    spec = ProcessSpec(process_id=process_id, argv=["python", "-m", "dummy"])
    return ProcessHandle(spec=spec, state=ManagedProcessState.STARTING)


class ManagerLifecycleRecoveryTests(unittest.TestCase):
    def test_lifecycle_chain_preflight_lock_transition_and_cleanup_shape(self) -> None:
        with (
            mock.patch(
                "experiment_control.cli.run_stack._probe_manager_ready",
                return_value=False,
            ),
            mock.patch(
                "experiment_control.cli.run_stack.cleanup_orphan_children",
                return_value={
                    "instance_id": "vacuum",
                    "matched": 2,
                    "terminated": [2001],
                    "failed": [2002],
                    "dry_run": False,
                    "stale_only": True,
                    "skipped_live_parent": [],
                    "candidates": [2001, 2002],
                },
            ),
        ):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                _preflight_instance_cleanup(
                    instance_id="vacuum",
                    manager_rpc="tcp://127.0.0.1:6000",
                )
        preflight_text = stderr.getvalue()
        self.assertIn("orphan cleanup", preflight_text)
        self.assertIn("matched=2", preflight_text)
        self.assertIn("failed pids", preflight_text)

        mgr = object.__new__(Manager)
        mgr._instance_id = "vacuum"  # type: ignore[attr-defined]
        mgr._started_t_wall = 10.0  # type: ignore[attr-defined]
        mgr._started_t_mono = 20.0  # type: ignore[attr-defined]
        mgr._last_orphan_cleanup = None  # type: ignore[attr-defined]
        mgr._cleanup_orphans_summary = mock.Mock(  # type: ignore[attr-defined]
            return_value={
                "instance_id": "vacuum",
                "matched": 3,
                "terminated": [3010, 3011],
                "failed": [3012],
                "dry_run": True,
                "stale_only": True,
                "skipped_live_parent": [],
                "candidates": [3010, 3011, 3012],
            }
        )
        mgr._publish_manager_event = mock.Mock()  # type: ignore[attr-defined]

        with mock.patch(
            "experiment_control.manager.read_instance_lock_status",
            return_value={
                "status": "stale",
                "owner_pid": 999_999,
                "owner_alive": False,
                "acquired_wall_s": 1.0,
                "manager_rpc": "tcp://127.0.0.1:6000",
            },
        ):
            identity_stale = Manager._route_internal_request(
                mgr,  # type: ignore[arg-type]
                {"type": "manager.identity"},
            )
        self.assertTrue(identity_stale.get("ok"))
        stale_result = identity_stale.get("result", {})
        self.assertEqual(stale_result.get("lock_effective_status"), "running_unlocked")
        self.assertIn(
            "no active instance lock",
            str(stale_result.get("lock_effective_help", "")).lower(),
        )

        manager_pid = int(stale_result.get("manager_pid"))
        with mock.patch(
            "experiment_control.manager.read_instance_lock_status",
            return_value={
                "status": "stale",
                "owner_pid": manager_pid,
                "owner_alive": True,
                "acquired_wall_s": 1.0,
                "manager_rpc": "tcp://127.0.0.1:6000",
            },
        ):
            identity_active = Manager._route_internal_request(
                mgr,  # type: ignore[arg-type]
                {"type": "manager.identity"},
            )
        self.assertTrue(identity_active.get("ok"))
        active_result = identity_active.get("result", {})
        self.assertEqual(active_result.get("lock_effective_status"), "active")

        cleanup_resp = Manager._route_internal_request(
            mgr,  # type: ignore[arg-type]
            {
                "type": "manager.cleanup_orphans",
                "params": {"dry_run": True, "stale_only": True, "timeout_s": 2.0},
            },
        )
        self.assertTrue(cleanup_resp.get("ok"))
        cleanup_result = cleanup_resp.get("result", {})
        self.assertIsInstance(cleanup_result, dict)
        assert isinstance(cleanup_result, dict)
        for key in (
            "instance_id",
            "matched",
            "terminated",
            "failed",
            "dry_run",
            "stale_only",
            "skipped_live_parent",
            "candidates",
        ):
            self.assertIn(key, cleanup_result)
        self.assertEqual(cleanup_result.get("instance_id"), "vacuum")
        self.assertEqual(cleanup_result.get("matched"), 3)
        self.assertTrue(cleanup_result.get("dry_run"))
        mgr._cleanup_orphans_summary.assert_called_once_with(
            dry_run=True,
            stale_only=True,
            timeout_s=2.0,
        )
        mgr._publish_manager_event.assert_called_once()

    def test_manager_identity_reports_last_orphan_cleanup(self) -> None:
        mgr = object.__new__(Manager)
        mgr._instance_id = "vacuum"  # type: ignore[attr-defined]
        mgr._started_t_wall = 10.0  # type: ignore[attr-defined]
        mgr._started_t_mono = 20.0  # type: ignore[attr-defined]
        mgr._last_orphan_cleanup = None  # type: ignore[attr-defined]
        mgr._cleanup_orphans_summary = mock.Mock(  # type: ignore[attr-defined]
            return_value={
                "instance_id": "vacuum",
                "matched": 1,
                "terminated": [11],
                "failed": [],
                "dry_run": False,
                "stale_only": True,
                "skipped_live_parent": [],
                "candidates": [11],
            }
        )
        mgr._publish_manager_event = mock.Mock()  # type: ignore[attr-defined]
        cleanup_resp = Manager._route_internal_request(
            mgr,  # type: ignore[arg-type]
            {"type": "manager.cleanup_orphans", "params": {}},
        )
        self.assertTrue(cleanup_resp.get("ok"))
        identity_resp = Manager._route_internal_request(
            mgr,  # type: ignore[arg-type]
            {"type": "manager.identity"},
        )
        self.assertTrue(identity_resp.get("ok"))
        identity = identity_resp.get("result", {})
        self.assertIsInstance(identity, dict)
        self.assertEqual(identity.get("instance_id"), "vacuum")
        self.assertIsInstance(identity.get("manager_pid"), int)
        self.assertIsInstance(identity.get("lock_status"), dict)
        self.assertIsInstance(identity.get("lock_effective_status"), str)
        process_guard = identity.get("process_guard")
        self.assertIsInstance(process_guard, dict)
        assert isinstance(process_guard, dict)
        self.assertIn("enabled", process_guard)
        self.assertIn("attach_failures", process_guard)
        last_cleanup = identity.get("last_orphan_cleanup")
        self.assertIsInstance(last_cleanup, dict)
        assert isinstance(last_cleanup, dict)
        self.assertEqual(last_cleanup.get("source"), "rpc")
        summary = last_cleanup.get("result")
        self.assertIsInstance(summary, dict)
        assert isinstance(summary, dict)
        self.assertEqual(summary.get("matched"), 1)

    def test_manager_cleanup_orphans_rpc(self) -> None:
        mgr = object.__new__(Manager)
        mgr._instance_id = "vacuum"  # type: ignore[attr-defined]
        mgr._cleanup_orphans_summary = mock.Mock(  # type: ignore[attr-defined]
            return_value={
                "instance_id": "vacuum",
                "matched": 2,
                "terminated": [10],
                "failed": [],
                "dry_run": True,
                "stale_only": False,
                "skipped_live_parent": [],
                "candidates": [10, 11],
            }
        )
        mgr._publish_manager_event = mock.Mock()  # type: ignore[attr-defined]
        resp = Manager._route_internal_request(
            mgr,  # type: ignore[arg-type]
            {
                "type": "manager.cleanup_orphans",
                "params": {"dry_run": True, "stale_only": False, "timeout_s": 1.5},
            },
        )
        self.assertTrue(resp.get("ok"))
        self.assertEqual(resp.get("result", {}).get("matched"), 2)
        mgr._cleanup_orphans_summary.assert_called_once_with(
            dry_run=True, stale_only=False, timeout_s=1.5
        )
        mgr._publish_manager_event.assert_called_once()

    def test_manager_cleanup_orphans_rpc_invalid_params(self) -> None:
        mgr = object.__new__(Manager)
        mgr._instance_id = "vacuum"  # type: ignore[attr-defined]
        mgr._publish_manager_event = mock.Mock()  # type: ignore[attr-defined]
        resp = Manager._route_internal_request(
            mgr,  # type: ignore[arg-type]
            {"type": "manager.cleanup_orphans", "params": "invalid"},
        )
        self.assertFalse(resp.get("ok"))
        self.assertEqual(resp.get("error", {}).get("code"), "invalid_params")

    def test_start_collision_recovery_retries_once(self) -> None:
        mgr = object.__new__(Manager)
        mgr._cleanup_orphans_summary = mock.Mock(  # type: ignore[attr-defined]
            return_value={
                "instance_id": "vacuum",
                "matched": 1,
                "terminated": [42],
                "failed": [],
                "dry_run": False,
                "stale_only": True,
                "skipped_live_parent": [],
                "candidates": [42],
            }
        )
        mgr._emit_log = mock.Mock()  # type: ignore[attr-defined]
        mgr._publish_manager_event = mock.Mock()  # type: ignore[attr-defined]
        mgr._publish_process_event = mock.Mock()  # type: ignore[attr-defined]
        mgr._recent_process_logs = mock.Mock(return_value=[])  # type: ignore[attr-defined]
        mgr._start_process_handle = mock.Mock()  # type: ignore[attr-defined]

        handle = _build_handle("sequencer")
        handle.last_error = "bind failed: endpoint already in use"
        self.assertTrue(
            Manager._maybe_recover_process_start_collision(mgr, handle)  # type: ignore[arg-type]
        )
        mgr._start_process_handle.assert_called_once_with(
            handle, reset_collision_retry=False
        )
        self.assertTrue(handle.startup_collision_retry_done)
        self.assertFalse(
            Manager._maybe_recover_process_start_collision(mgr, handle)  # type: ignore[arg-type]
        )
        self.assertEqual(mgr._start_process_handle.call_count, 1)

    def test_start_collision_recovery_ignores_non_collision(self) -> None:
        mgr = object.__new__(Manager)
        mgr._cleanup_orphans_summary = mock.Mock()  # type: ignore[attr-defined]
        mgr._emit_log = mock.Mock()  # type: ignore[attr-defined]
        mgr._publish_manager_event = mock.Mock()  # type: ignore[attr-defined]
        mgr._publish_process_event = mock.Mock()  # type: ignore[attr-defined]
        mgr._recent_process_logs = mock.Mock(return_value=["plain text"])  # type: ignore[attr-defined]
        mgr._start_process_handle = mock.Mock()  # type: ignore[attr-defined]

        handle = _build_handle("sequencer")
        handle.last_error = "process exited"
        self.assertFalse(
            Manager._maybe_recover_process_start_collision(mgr, handle)  # type: ignore[arg-type]
        )
        mgr._start_process_handle.assert_not_called()
        mgr._cleanup_orphans_summary.assert_not_called()


if __name__ == "__main__":
    unittest.main()
