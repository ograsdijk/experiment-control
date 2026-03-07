# ruff: noqa: E402

import sys
import io
import os
import unittest
from pathlib import Path
from contextlib import redirect_stderr
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.cli.run_stack import (
    _preflight_instance_cleanup,
    _run_with_tui,
    _wait_for_manager_ready,
)
from experiment_control.utils.manager_network import resolve_manager_network


class _FakeProc:
    def __init__(self, polls: list[int | None]) -> None:
        self._polls = list(polls)
        self._last: int | None = None

    def poll(self) -> int | None:
        if self._polls:
            self._last = self._polls.pop(0)
        return self._last


class TuiStartupWaitTests(unittest.TestCase):
    def test_main_no_tui_instance_lock_survives_identity_probe_on_windows(self) -> None:
        stack_raw = {
            "instance_id": "vacuum",
            "manager": {},
            "tui": {"enabled": True},
            "startup": {
                "start_devices": False,
                "start_processes": False,
                "wait_for_registered": False,
                "wait_for_online": False,
                "timeout_s": 0.1,
                "poll_ms": 1,
            },
        }
        manager_network = resolve_manager_network({})
        lock_calls = {"acquire": 0, "release": 0}
        probe_status: dict[str, str | None] = {"status": None}

        class _FakeLock:
            def __init__(self, *, instance_id: str, manager_rpc: str) -> None:
                self.instance_id = instance_id
                self.manager_rpc = manager_rpc

            def acquire(self) -> None:
                lock_calls["acquire"] += 1

            def release(self) -> None:
                lock_calls["release"] += 1

        class _FakeLockPath:
            def exists(self) -> bool:
                return True

            def __str__(self) -> str:
                return "C:\\fake\\instance_locks\\vacuum.json"

        def _fake_read_info(path: object):
            from experiment_control.utils import instance_lock as lock_module

            owner_pid = 12345
            return lock_module.InstanceLockInfo(
                instance_id="vacuum",
                pid=owner_pid,
                owner_alive=lock_module._pid_is_alive(owner_pid),
                manager_rpc=manager_network.local_rpc_connect,
                lock_path=str(path),
                acquired_wall_s=1.0,
            )

        class _FakeManager:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

            def add_device(self, spec: object) -> None:
                return

            def add_process(self, spec: object) -> None:
                return

            def startup_sequence(self, **kwargs: object) -> None:
                return

            def run_forever(self) -> None:
                from experiment_control.utils import instance_lock as lock_module

                status = lock_module.read_instance_lock_status("vacuum")
                probe_status["status"] = str(status.get("status"))
                return

        with (
            mock.patch(
                "experiment_control.cli.run_stack._load_yaml",
                return_value=stack_raw,
            ),
            mock.patch(
                "experiment_control.cli.run_stack.resolve_manager_network",
                return_value=manager_network,
            ),
            mock.patch(
                "experiment_control.cli.run_stack.parse_federation_config",
                return_value={},
            ),
            mock.patch("experiment_control.cli.run_stack.Manager", _FakeManager),
            mock.patch("experiment_control.cli.run_stack.InstanceLock", _FakeLock),
            mock.patch("experiment_control.utils.instance_lock.sys.platform", "win32"),
            mock.patch(
                "experiment_control.utils.instance_lock.get_instance_lock_path",
                return_value=_FakeLockPath(),
            ),
            mock.patch(
                "experiment_control.utils.instance_lock.InstanceLock._read_info",
                side_effect=_fake_read_info,
            ),
            mock.patch(
                "experiment_control.utils.instance_lock._pid_is_alive_windows",
                return_value=True,
            ) as win_probe,
            mock.patch(
                "experiment_control.utils.instance_lock.os.kill",
                side_effect=AssertionError(
                    "os.kill(pid, 0) should not be used on win32"
                ),
            ),
        ):
            from experiment_control.cli.run_stack import main

            main(["dummy_stack.yaml", "--no-tui", "--instance-lock"])

        self.assertEqual(lock_calls["acquire"], 1)
        self.assertEqual(lock_calls["release"], 1)
        self.assertEqual(probe_status["status"], "active")
        win_probe.assert_called()

    def test_main_no_tui_passes_command_journal_settings_to_manager(self) -> None:
        stack_raw = {
            "instance_id": "vacuum",
            "manager": {
                "command_journal": {
                    "enabled": True,
                    "path": ".state/journal.sqlite3",
                    "queue_max": 5000,
                    "batch_size": 100,
                    "flush_interval_ms": 50,
                    "retention": {"max_rows": 20000, "max_age_days": 7},
                }
            },
            "startup": {
                "start_devices": False,
                "start_processes": False,
                "wait_for_registered": False,
                "wait_for_online": False,
            },
        }
        manager_network = resolve_manager_network({})
        captured_kwargs: dict[str, object] = {}

        class _FakeManager:
            def __init__(self, **kwargs: object) -> None:
                captured_kwargs.update(kwargs)

            def add_device(self, spec: object) -> None:
                return

            def add_process(self, spec: object) -> None:
                return

            def startup_sequence(self, **kwargs: object) -> None:
                return

            def run_forever(self) -> None:
                return

        with (
            mock.patch(
                "experiment_control.cli.run_stack._load_yaml",
                return_value=stack_raw,
            ),
            mock.patch(
                "experiment_control.cli.run_stack.resolve_manager_network",
                return_value=manager_network,
            ),
            mock.patch(
                "experiment_control.cli.run_stack.parse_federation_config",
                return_value={},
            ),
            mock.patch("experiment_control.cli.run_stack.Manager", _FakeManager),
        ):
            from experiment_control.cli.run_stack import main

            main(["dummy_stack.yaml", "--no-tui"])

        expected_base = Path("dummy_stack.yaml").expanduser().resolve().parent
        self.assertTrue(bool(captured_kwargs.get("command_journal_enabled")))
        self.assertEqual(
            captured_kwargs.get("command_journal_path"),
            (expected_base / ".state" / "journal.sqlite3").resolve(),
        )
        self.assertEqual(captured_kwargs.get("command_journal_queue_max"), 5000)
        self.assertEqual(captured_kwargs.get("command_journal_batch_size"), 100)
        self.assertEqual(captured_kwargs.get("command_journal_flush_interval_ms"), 50)
        self.assertEqual(captured_kwargs.get("command_journal_retention_max_rows"), 20000)
        self.assertEqual(captured_kwargs.get("command_journal_retention_max_age_days"), 7.0)

    def test_main_no_tui_passes_manager_logging_settings_to_manager(self) -> None:
        stack_raw = {
            "instance_id": "vacuum",
            "manager": {
                "logging": {
                    "stderr": False,
                    "file": ".state/manager_errors.log",
                    "min_level": "warning",
                }
            },
            "startup": {
                "start_devices": False,
                "start_processes": False,
                "wait_for_registered": False,
                "wait_for_online": False,
            },
        }
        manager_network = resolve_manager_network({})
        captured_kwargs: dict[str, object] = {}

        class _FakeManager:
            def __init__(self, **kwargs: object) -> None:
                captured_kwargs.update(kwargs)

            def add_device(self, spec: object) -> None:
                return

            def add_process(self, spec: object) -> None:
                return

            def startup_sequence(self, **kwargs: object) -> None:
                return

            def run_forever(self) -> None:
                return

        with (
            mock.patch(
                "experiment_control.cli.run_stack._load_yaml",
                return_value=stack_raw,
            ),
            mock.patch(
                "experiment_control.cli.run_stack.resolve_manager_network",
                return_value=manager_network,
            ),
            mock.patch(
                "experiment_control.cli.run_stack.parse_federation_config",
                return_value={},
            ),
            mock.patch("experiment_control.cli.run_stack.Manager", _FakeManager),
        ):
            from experiment_control.cli.run_stack import main

            main(["dummy_stack.yaml", "--no-tui"])

        expected_base = Path("dummy_stack.yaml").expanduser().resolve().parent
        self.assertEqual(captured_kwargs.get("manager_log_stderr"), False)
        self.assertEqual(
            captured_kwargs.get("manager_log_file"),
            (expected_base / ".state" / "manager_errors.log").resolve(),
        )
        self.assertEqual(captured_kwargs.get("manager_log_min_level"), "warning")

    def test_run_with_tui_spawns_child_without_opt_in_flags_by_default(self) -> None:
        manager_network = resolve_manager_network({})
        with (
            mock.patch("experiment_control.cli.run_stack.subprocess.Popen") as popen_mock,
            mock.patch(
                "experiment_control.cli.run_stack._wait_for_manager_ready",
                return_value=(False, "boom"),
            ),
            mock.patch("experiment_control.cli.run_stack._wait_for_exit"),
        ):
            with self.assertRaises(SystemExit):
                _run_with_tui(
                    instance_id="vacuum",
                    stack_path=Path("dummy_stack.yaml"),
                    manager_network=manager_network,
                    tui_raw={},
                )
        cmd = popen_mock.call_args.args[0]
        self.assertIn("--no-tui", cmd)
        self.assertNotIn("--cleanup-orphans", cmd)
        self.assertNotIn("--instance-lock", cmd)

    def test_run_with_tui_forwards_instance_lock_flag_to_child(self) -> None:
        manager_network = resolve_manager_network({})
        with (
            mock.patch("experiment_control.cli.run_stack.subprocess.Popen") as popen_mock,
            mock.patch(
                "experiment_control.cli.run_stack._wait_for_manager_ready",
                return_value=(False, "boom"),
            ),
            mock.patch("experiment_control.cli.run_stack._wait_for_exit"),
        ):
            with self.assertRaises(SystemExit):
                _run_with_tui(
                    instance_id="vacuum",
                    stack_path=Path("dummy_stack.yaml"),
                    manager_network=manager_network,
                    tui_raw={},
                    instance_lock=True,
                )
        cmd = popen_mock.call_args.args[0]
        self.assertIn("--instance-lock", cmd)

    def test_run_with_tui_forces_manager_log_stderr_off_in_child_env(self) -> None:
        manager_network = resolve_manager_network({})
        with (
            mock.patch.dict(
                os.environ,
                {"MANAGER_LOG_STDERR": "1", "MANAGER_LOG_FILE": "C:\\tmp\\manager.log"},
                clear=False,
            ),
            mock.patch("experiment_control.cli.run_stack.subprocess.Popen") as popen_mock,
            mock.patch(
                "experiment_control.cli.run_stack._wait_for_manager_ready",
                return_value=(False, "boom"),
            ),
            mock.patch("experiment_control.cli.run_stack._wait_for_exit"),
        ):
            with self.assertRaises(SystemExit):
                _run_with_tui(
                    instance_id="vacuum",
                    stack_path=Path("dummy_stack.yaml"),
                    manager_network=manager_network,
                    tui_raw={},
                )
        env = popen_mock.call_args.kwargs.get("env")
        self.assertIsInstance(env, dict)
        assert isinstance(env, dict)
        self.assertEqual(env.get("MANAGER_LOG_STDERR"), "0")
        self.assertEqual(env.get("MANAGER_LOG_FILE"), "C:\\tmp\\manager.log")

    def test_main_with_tui_runs_cleanup_in_parent_before_spawn(self) -> None:
        manager_network = resolve_manager_network({})
        stack_raw = {
            "instance_id": "vacuum",
            "manager": {},
            "tui": {"enabled": True},
        }
        expected_path = Path("dummy_stack.yaml").expanduser().resolve()
        with (
            mock.patch(
                "experiment_control.cli.run_stack._load_yaml",
                return_value=stack_raw,
            ),
            mock.patch("experiment_control.cli.run_stack.sys.platform", "linux"),
            mock.patch(
                "experiment_control.cli.run_stack._preflight_instance_cleanup"
            ) as preflight_mock,
            mock.patch(
                "experiment_control.cli.run_stack._run_with_tui",
                side_effect=SystemExit(0),
            ) as run_tui_mock,
        ):
            with self.assertRaises(SystemExit):
                from experiment_control.cli.run_stack import main

                main(["dummy_stack.yaml", "--cleanup-orphans"])
        preflight_mock.assert_called_once_with(
            instance_id="vacuum",
            manager_rpc=manager_network.local_rpc_connect,
        )
        run_tui_mock.assert_called_once_with(
            instance_id="vacuum",
            stack_path=expected_path,
            manager_network=manager_network,
            tui_raw={"enabled": True},
            instance_lock=False,
        )

    def test_main_with_tui_windows_allows_lifecycle_flags(self) -> None:
        manager_network = resolve_manager_network({})
        stack_raw = {
            "instance_id": "vacuum",
            "manager": {},
            "tui": {"enabled": True},
        }
        with (
            mock.patch(
                "experiment_control.cli.run_stack._load_yaml",
                return_value=stack_raw,
            ),
            mock.patch("experiment_control.cli.run_stack.sys.platform", "win32"),
            mock.patch(
                "experiment_control.cli.run_stack._preflight_instance_cleanup"
            ) as preflight_mock,
            mock.patch(
                "experiment_control.cli.run_stack._run_with_tui",
                side_effect=SystemExit(0),
            ) as run_tui_mock,
        ):
            with self.assertRaises(SystemExit):
                from experiment_control.cli.run_stack import main

                main(["dummy_stack.yaml", "--cleanup-orphans", "--instance-lock"])
        preflight_mock.assert_called_once_with(
            instance_id="vacuum",
            manager_rpc=manager_network.local_rpc_connect,
        )
        run_tui_mock.assert_called_once()
        run_kwargs = run_tui_mock.call_args.kwargs
        self.assertEqual(run_kwargs.get("instance_id"), "vacuum")
        self.assertEqual(run_kwargs.get("manager_network"), manager_network)
        self.assertEqual(run_kwargs.get("tui_raw"), {"enabled": True})
        self.assertTrue(bool(run_kwargs.get("instance_lock")))

    def test_main_with_tui_emits_lifecycle_summary(self) -> None:
        manager_network = resolve_manager_network({})
        stack_raw = {
            "instance_id": "vacuum",
            "manager": {},
            "tui": {"enabled": True},
        }
        with (
            mock.patch(
                "experiment_control.cli.run_stack._load_yaml",
                return_value=stack_raw,
            ),
            mock.patch(
                "experiment_control.cli.run_stack._preflight_instance_cleanup"
            ) as preflight_mock,
            mock.patch(
                "experiment_control.cli.run_stack._run_with_tui",
                side_effect=SystemExit(0),
            ),
        ):
            buf = io.StringIO()
            with self.assertRaises(SystemExit):
                with redirect_stderr(buf):
                    from experiment_control.cli.run_stack import main

                    main(["dummy_stack.yaml", "--cleanup-orphans", "--instance-lock"])
        preflight_mock.assert_called_once_with(
            instance_id="vacuum",
            manager_rpc=manager_network.local_rpc_connect,
        )
        text = buf.getvalue()
        self.assertIn("[run_stack] lifecycle:", text)
        self.assertIn("mode=tui", text)
        self.assertIn("cleanup=on", text)
        self.assertIn("lock=on", text)
        self.assertIn("preflight=run", text)

    def test_main_no_tui_emits_lifecycle_summary(self) -> None:
        stack_raw = {
            "instance_id": "vacuum",
            "manager": {},
            "startup": {
                "start_devices": False,
                "start_processes": False,
                "wait_for_registered": False,
                "wait_for_online": False,
            },
        }

        class _FakeManager:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

            def add_device(self, spec: object) -> None:
                return

            def add_process(self, spec: object) -> None:
                return

            def startup_sequence(self, **kwargs: object) -> None:
                return

            def run_forever(self) -> None:
                return

        with (
            mock.patch(
                "experiment_control.cli.run_stack._load_yaml",
                return_value=stack_raw,
            ),
            mock.patch(
                "experiment_control.cli.run_stack.parse_federation_config",
                return_value={},
            ),
            mock.patch("experiment_control.cli.run_stack.Manager", _FakeManager),
        ):
            buf = io.StringIO()
            with redirect_stderr(buf):
                from experiment_control.cli.run_stack import main

                main(["dummy_stack.yaml", "--no-tui"])
        text = buf.getvalue()
        self.assertIn("[run_stack] lifecycle:", text)
        self.assertIn("mode=headless", text)
        self.assertIn("cleanup=off", text)
        self.assertIn("lock=off", text)
        self.assertIn("preflight=skip", text)

    def test_main_no_tui_runs_preflight_once_when_enabled(self) -> None:
        stack_raw = {
            "instance_id": "vacuum",
            "manager": {},
            "startup": {
                "start_devices": False,
                "start_processes": False,
                "wait_for_registered": False,
                "wait_for_online": False,
            },
        }

        class _FakeManager:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

            def add_device(self, spec: object) -> None:
                return

            def add_process(self, spec: object) -> None:
                return

            def startup_sequence(self, **kwargs: object) -> None:
                return

            def run_forever(self) -> None:
                return

        with (
            mock.patch(
                "experiment_control.cli.run_stack._load_yaml",
                return_value=stack_raw,
            ),
            mock.patch(
                "experiment_control.cli.run_stack._preflight_instance_cleanup"
            ) as preflight_mock,
            mock.patch(
                "experiment_control.cli.run_stack.parse_federation_config",
                return_value={},
            ),
            mock.patch("experiment_control.cli.run_stack.Manager", _FakeManager),
        ):
            from experiment_control.cli.run_stack import main

            main(["dummy_stack.yaml", "--no-tui", "--cleanup-orphans"])
        preflight_mock.assert_called_once()

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

    def test_preflight_rejects_live_same_instance(self) -> None:
        with mock.patch(
            "experiment_control.cli.run_stack._probe_manager_ready",
            return_value=True,
        ):
            with self.assertRaises(SystemExit) as ctx:
                _preflight_instance_cleanup(
                    instance_id="vacuum",
                    manager_rpc="tcp://127.0.0.1:6000",
                )
        self.assertIn("already running", str(ctx.exception))

    def test_preflight_runs_orphan_cleanup_and_reports_summary(self) -> None:
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
                    "terminated": [123, 456],
                    "failed": [789],
                },
            ),
        ):
            buf = io.StringIO()
            with redirect_stderr(buf):
                _preflight_instance_cleanup(
                    instance_id="vacuum",
                    manager_rpc="tcp://127.0.0.1:6000",
                )
        text = buf.getvalue()
        self.assertIn("orphan cleanup", text)
        self.assertIn("failed pids", text)


if __name__ == "__main__":
    unittest.main()
