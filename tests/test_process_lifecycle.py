# ruff: noqa: E402

import sys
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.utils import process_lifecycle


class ProcessLifecycleTests(unittest.TestCase):
    def test_process_exists_uses_windows_probe_on_win32(self) -> None:
        with (
            mock.patch.object(process_lifecycle.sys, "platform", "win32"),
            mock.patch.object(
                process_lifecycle,
                "_process_exists_windows",
                return_value=True,
            ) as win_probe,
            mock.patch.object(
                process_lifecycle.os,
                "kill",
                side_effect=AssertionError("os.kill should not be used on win32"),
            ),
        ):
            self.assertTrue(process_lifecycle._process_exists(123))
        win_probe.assert_called_once_with(123)

    def test_cleanup_orphan_children_filters_and_terminates(self) -> None:
        with (
            mock.patch.object(
                process_lifecycle,
                "_list_process_commands",
                return_value=[
                    (
                        101,
                        "python -m experiment_control.cli.start_driver --instance-id alpha",
                    ),
                    (
                        102,
                        "python -m experiment_control.cli.start_process --instance-id alpha",
                    ),
                    (
                        103,
                        "python -m experiment_control.cli.start_process --instance-id beta",
                    ),
                    (104, "python -m experiment_control.cli.run_stack --instance-id alpha"),
                ],
            ),
            mock.patch.object(
                process_lifecycle,
                "_terminate_pid",
                side_effect=lambda pid, timeout_s: pid == 102,
            ),
        ):
            summary = process_lifecycle.cleanup_orphan_children(
                instance_id="alpha",
                exclude_pids={101},
                timeout_s=1.0,
            )

        self.assertEqual(summary["matched"], 1)
        self.assertEqual(summary["terminated"], [102])
        self.assertEqual(summary["failed"], [])

    def test_cleanup_orphan_children_tracks_failed_termination(self) -> None:
        with (
            mock.patch.object(
                process_lifecycle,
                "_list_process_commands",
                return_value=[
                    (
                        202,
                        "python -m experiment_control.cli.start_process --instance-id alpha",
                    )
                ],
            ),
            mock.patch.object(
                process_lifecycle,
                "_terminate_pid",
                return_value=False,
            ),
        ):
            summary = process_lifecycle.cleanup_orphan_children(instance_id="alpha")

        self.assertEqual(summary["matched"], 1)
        self.assertEqual(summary["terminated"], [])
        self.assertEqual(summary["failed"], [202])

    def test_cleanup_orphan_children_stale_only_skips_live_parent(self) -> None:
        with (
            mock.patch.object(
                process_lifecycle,
                "_list_process_commands",
                return_value=[
                    (
                        301,
                        "python -m experiment_control.cli.start_process --instance-id alpha --parent-pid 999",
                    ),
                    (
                        302,
                        "python -m experiment_control.cli.start_process --instance-id alpha --parent-pid 888",
                    ),
                ],
            ),
            mock.patch.object(
                process_lifecycle,
                "_process_exists",
                side_effect=lambda pid: int(pid) == 999,
            ),
            mock.patch.object(
                process_lifecycle,
                "_terminate_pid",
                return_value=True,
            ) as terminate_mock,
        ):
            summary = process_lifecycle.cleanup_orphan_children(
                instance_id="alpha",
                stale_only=True,
            )

        self.assertEqual(summary["matched"], 1)
        self.assertEqual(summary["terminated"], [302])
        self.assertEqual(summary["skipped_live_parent"], [301])
        terminate_mock.assert_called_once()

    def test_cleanup_orphan_children_dry_run_does_not_terminate(self) -> None:
        with (
            mock.patch.object(
                process_lifecycle,
                "_list_process_commands",
                return_value=[
                    (
                        401,
                        "python -m experiment_control.cli.start_driver --instance-id alpha",
                    )
                ],
            ),
            mock.patch.object(
                process_lifecycle,
                "_terminate_pid",
                return_value=True,
            ) as terminate_mock,
        ):
            summary = process_lifecycle.cleanup_orphan_children(
                instance_id="alpha",
                dry_run=True,
            )

        self.assertEqual(summary["matched"], 1)
        self.assertEqual(summary["candidates"], [401])
        self.assertEqual(summary["terminated"], [])
        self.assertTrue(summary["dry_run"])
        terminate_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
