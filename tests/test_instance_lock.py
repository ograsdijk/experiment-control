# ruff: noqa: E402

import sys
import os
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.utils.instance_lock import (
    InstanceLock,
    InstanceLockActiveError,
    derive_lock_effective_status,
    lock_effective_status_help,
    read_instance_lock_status,
)
from tests._temp_utils import repo_temp_dir


class InstanceLockTests(unittest.TestCase):
    def test_pid_is_alive_uses_windows_probe_on_win32(self) -> None:
        from experiment_control.utils import instance_lock as module

        with (
            mock.patch.object(module.sys, "platform", "win32"),
            mock.patch.object(module, "_pid_is_alive_windows", return_value=True) as win_probe,
            mock.patch.object(module.os, "kill", side_effect=AssertionError("os.kill should not be used on win32")),
        ):
            self.assertTrue(module._pid_is_alive(123))
        win_probe.assert_called_once_with(123)

    def test_acquire_and_release(self) -> None:
        with repo_temp_dir("instance-lock") as root:
            with mock.patch(
                "experiment_control.utils.instance_lock._lock_root",
                return_value=root,
            ):
                lock = InstanceLock(
                    instance_id="vacuum",
                    manager_rpc="tcp://127.0.0.1:6000",
                )
                lock.acquire()
                self.assertTrue(lock.path.exists())
                lock.release()
                self.assertFalse(lock.path.exists())

    def test_acquire_overwrites_stale_lock(self) -> None:
        with repo_temp_dir("instance-lock") as root:
            lock_path = root / "vacuum.json"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.write_text(
                '{"version":1,"instance_id":"vacuum","pid":999999,"manager_rpc":"tcp://127.0.0.1:6000"}',
                encoding="utf-8",
            )
            with mock.patch(
                "experiment_control.utils.instance_lock._lock_root",
                return_value=root,
            ):
                lock = InstanceLock(
                    instance_id="vacuum",
                    manager_rpc="tcp://127.0.0.1:6000",
                )
                lock.acquire()
                self.assertTrue(lock.path.exists())
                lock.release()

    def test_acquire_rejects_live_lock(self) -> None:
        with repo_temp_dir("instance-lock") as root:
            with mock.patch(
                "experiment_control.utils.instance_lock._lock_root",
                return_value=root,
            ):
                first = InstanceLock(
                    instance_id="vacuum",
                    manager_rpc="tcp://127.0.0.1:6000",
                )
                first.acquire()
                second = InstanceLock(
                    instance_id="vacuum",
                    manager_rpc="tcp://127.0.0.1:6000",
                )
                with self.assertRaises(InstanceLockActiveError):
                    second.acquire()
                first.release()

    def test_read_instance_lock_status_reports_missing(self) -> None:
        with repo_temp_dir("instance-lock") as root:
            with mock.patch(
                "experiment_control.utils.instance_lock._lock_root",
                return_value=root,
            ):
                status = read_instance_lock_status("vacuum")
        self.assertFalse(status["exists"])
        self.assertEqual(status["status"], "missing")

    def test_read_instance_lock_status_reports_active(self) -> None:
        with repo_temp_dir("instance-lock") as root:
            with mock.patch(
                "experiment_control.utils.instance_lock._lock_root",
                return_value=root,
            ):
                lock = InstanceLock(
                    instance_id="vacuum",
                    manager_rpc="tcp://127.0.0.1:6000",
                )
                lock.acquire()
                status = read_instance_lock_status("vacuum")
                lock.release()
        self.assertTrue(status["exists"])
        self.assertEqual(status["status"], "active")
        self.assertEqual(int(status["owner_pid"]), int(os.getpid()))

    def test_derive_lock_effective_status_returns_active_when_owner_matches_manager(self) -> None:
        status = derive_lock_effective_status(
            lock_status={"status": "stale", "owner_pid": 1234},
            manager_pid=1234,
            manager_reachable=True,
            reported_effective_status=None,
        )
        self.assertEqual(status, "active")

    def test_derive_lock_effective_status_running_unlocked_when_manager_reachable(self) -> None:
        status = derive_lock_effective_status(
            lock_status={"status": "missing"},
            manager_pid=4321,
            manager_reachable=True,
            reported_effective_status=None,
        )
        self.assertEqual(status, "running_unlocked")

    def test_derive_lock_effective_status_treats_invalid_as_missing(self) -> None:
        status = derive_lock_effective_status(
            lock_status={"status": "invalid"},
            manager_pid=None,
            manager_reachable=False,
            reported_effective_status=None,
        )
        self.assertEqual(status, "missing")

    def test_lock_effective_status_help(self) -> None:
        self.assertIn("running manager process", lock_effective_status_help("active"))
        self.assertIn(
            "no active instance lock",
            lock_effective_status_help("running_unlocked"),
        )


if __name__ == "__main__":
    unittest.main()
