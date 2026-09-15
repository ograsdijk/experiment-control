# ruff: noqa: E402

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control._manager.lifecycle import LifecycleMixin
from experiment_control._manager.models import ManagedProcessState
from experiment_control.utils.manager_network import resolve_manager_network

PROCESS_IDS = ["hdf_writer", "influx_writer", "interlock", "watchdog"]


class _RecordingManager:
    """Fake Manager recording what run_stack registers, starts, and waits on."""

    last: "_RecordingManager | None" = None

    def __init__(self, **kwargs: object) -> None:
        self.registered: list[str] = []
        self.started: list[str] = []
        self.startup_kwargs: dict[str, Any] = {}
        _RecordingManager.last = self

    def add_device(self, spec: object) -> None:
        return

    def add_process(self, spec: Any) -> None:
        self.registered.append(spec.process_id)

    def start_process(self, process_id: str) -> None:
        if process_id not in self.registered:
            raise KeyError(process_id)
        self.started.append(process_id)

    def startup_sequence(self, **kwargs: Any) -> None:
        self.startup_kwargs = kwargs

    def run_forever(self) -> None:
        return


def _run_main(startup: dict[str, Any]) -> _RecordingManager:
    stack_raw = {"instance_id": "vacuum", "manager": {}, "startup": startup}

    def _collect(section: object, *, base: Path, label: str) -> list[Path]:
        if label == "processes":
            return [Path(f"{pid}.yaml") for pid in PROCESS_IDS]
        return []

    with (
        mock.patch(
            "experiment_control.cli.run_stack._load_yaml", return_value=stack_raw
        ),
        mock.patch(
            "experiment_control.cli.run_stack.resolve_manager_network",
            return_value=resolve_manager_network({}),
        ),
        mock.patch(
            "experiment_control.cli.run_stack.parse_federation_config",
            return_value={},
        ),
        mock.patch(
            "experiment_control.cli.run_stack._collect_config_paths",
            side_effect=_collect,
        ),
        mock.patch(
            "experiment_control.cli.run_stack.process_spec_from_yaml",
            side_effect=lambda path, **_: SimpleNamespace(process_id=path.stem),
        ),
        mock.patch("experiment_control.cli.run_stack.Manager", _RecordingManager),
    ):
        from experiment_control.cli.run_stack import main

        main(["dummy_stack.yaml", "--no-tui"])
    manager = _RecordingManager.last
    assert manager is not None
    return manager


_BASE_STARTUP = {
    "start_devices": False,
    "start_processes": True,
    "wait_for_registered": False,
    "wait_for_online": False,
}


class RunStackProcessExcludeTests(unittest.TestCase):
    def test_without_exclude_starts_all_and_waits_on_all(self) -> None:
        manager = _run_main(
            {**_BASE_STARTUP, "process_order": ["interlock", "watchdog"]}
        )
        self.assertEqual(manager.registered, PROCESS_IDS)
        self.assertEqual(
            manager.started, ["interlock", "watchdog", "hdf_writer", "influx_writer"]
        )
        self.assertTrue(manager.startup_kwargs["wait_processes_running"])
        self.assertIsNone(manager.startup_kwargs["wait_process_ids"])

    def test_without_exclude_or_order_keeps_hdf_writer_first_default(self) -> None:
        manager = _run_main(dict(_BASE_STARTUP))
        self.assertEqual(
            manager.started, ["hdf_writer", "influx_writer", "interlock", "watchdog"]
        )

    def test_excluded_process_is_registered_but_not_started(self) -> None:
        manager = _run_main({**_BASE_STARTUP, "process_exclude": ["hdf_writer"]})
        self.assertIn("hdf_writer", manager.registered)
        self.assertEqual(manager.started, ["influx_writer", "interlock", "watchdog"])
        self.assertEqual(
            manager.startup_kwargs["wait_process_ids"],
            ["influx_writer", "interlock", "watchdog"],
        )

    def test_process_order_applies_to_remaining_processes(self) -> None:
        manager = _run_main(
            {
                **_BASE_STARTUP,
                "process_exclude": ["hdf_writer"],
                "process_order": ["watchdog"],
            }
        )
        # Requested order first, then unspecified non-excluded ids sorted.
        self.assertEqual(manager.started, ["watchdog", "influx_writer", "interlock"])

    def test_excluded_process_can_be_started_manually_after_startup(self) -> None:
        manager = _run_main({**_BASE_STARTUP, "process_exclude": ["hdf_writer"]})
        manager.start_process("hdf_writer")
        self.assertEqual(manager.started[-1], "hdf_writer")

    def test_start_processes_false_still_honors_exclude_in_wait(self) -> None:
        manager = _run_main(
            {
                **_BASE_STARTUP,
                "start_processes": False,
                "wait_processes_running": True,
                "process_exclude": ["hdf_writer"],
            }
        )
        self.assertEqual(manager.started, [])
        self.assertNotIn("hdf_writer", manager.startup_kwargs["wait_process_ids"])

    def _assert_config_error(self, startup: dict[str, Any], pattern: str) -> None:
        with self.assertRaises(SystemExit) as ctx:
            _run_main({**_BASE_STARTUP, **startup})
        self.assertRegex(str(ctx.exception), pattern)

    def test_unknown_excluded_process_is_config_error(self) -> None:
        self._assert_config_error(
            {"process_exclude": ["nope"]},
            r"startup\.process_exclude.*unknown process_id\(s\): \['nope'\]",
        )

    def test_process_in_both_order_and_exclude_is_config_error(self) -> None:
        self._assert_config_error(
            {
                "process_exclude": ["hdf_writer"],
                "process_order": ["hdf_writer", "influx_writer"],
            },
            r"startup\.process_exclude.*also listed in startup\.process_order.*hdf_writer",
        )

    def test_duplicate_excluded_process_is_config_error(self) -> None:
        self._assert_config_error(
            {"process_exclude": ["hdf_writer", "hdf_writer"]},
            r"startup\.process_exclude\[1\].*duplicate process_id 'hdf_writer'",
        )

    def test_non_list_exclude_is_config_error(self) -> None:
        self._assert_config_error(
            {"process_exclude": "hdf_writer"},
            r"startup\.process_exclude.*must be a list\[str\]",
        )

    def test_non_string_exclude_entry_is_config_error(self) -> None:
        self._assert_config_error(
            {"process_exclude": [3]},
            r"startup\.process_exclude\[0\].*must be a non-empty string",
        )


class _FakeFederationHub:
    def activate(self) -> None:
        return


class _LifecycleManager(LifecycleMixin):
    def __init__(self, states: dict[str, ManagedProcessState]) -> None:
        self._devices: dict[str, Any] = {}
        self._processes: dict[str, Any] = {
            pid: SimpleNamespace(state=state) for pid, state in states.items()
        }
        self._federation_hub = _FakeFederationHub()
        self._startup_sequence_active = False
        self._startup_sequence_complete_mono = None
        self.logs: list[dict[str, Any]] = []

    def _ensure_router_running(self, *, timeout_s: float, poll_ms: int) -> None:
        return

    def _pump_once(self, *, poll_ms: int) -> None:
        return

    def start_process(self, process_id: str) -> None:
        self._processes[process_id].state = ManagedProcessState.RUNNING

    def _emit_log(self, **kwargs: Any) -> None:
        self.logs.append(kwargs)


def _startup(manager: _LifecycleManager, **kwargs: Any) -> None:
    manager.startup_sequence(
        start_drivers=False,
        start_processes=False,
        wait_processes_running=True,
        wait_for_registered=False,
        wait_for_online=False,
        timeout_s=0.05,
        poll_ms=1,
        **kwargs,
    )


class StartupWaitProcessIdsTests(unittest.TestCase):
    def test_excluded_stopped_process_does_not_time_out_subset_wait(self) -> None:
        manager = _LifecycleManager(
            {
                "hdf_writer": ManagedProcessState.STOPPED,
                "interlock": ManagedProcessState.RUNNING,
            }
        )
        # Raises TimeoutError if the stopped excluded process were awaited.
        _startup(manager, wait_process_ids=["interlock"])

    def test_default_wait_still_requires_every_registered_process(self) -> None:
        manager = _LifecycleManager(
            {
                "hdf_writer": ManagedProcessState.STOPPED,
                "interlock": ManagedProcessState.RUNNING,
            }
        )
        with self.assertRaisesRegex(TimeoutError, r"\['hdf_writer'\]"):
            _startup(manager)

    def test_subset_wait_still_requires_selected_processes_running(self) -> None:
        manager = _LifecycleManager(
            {
                "hdf_writer": ManagedProcessState.STOPPED,
                "interlock": ManagedProcessState.RUNNING,
                "watchdog": ManagedProcessState.STARTING,
            }
        )
        with self.assertRaisesRegex(TimeoutError, r"\['watchdog'\]") as ctx:
            _startup(manager, wait_process_ids=["interlock", "watchdog"])
        self.assertNotIn("hdf_writer", str(ctx.exception))
        timeout_logs = [
            e for e in manager.logs if e["topic"] == "manager.startup.process_timeout"
        ]
        self.assertEqual(timeout_logs[0]["payload"], {"not_running": ["watchdog"]})

    def test_excluded_process_is_startable_after_subset_startup(self) -> None:
        manager = _LifecycleManager(
            {
                "hdf_writer": ManagedProcessState.STOPPED,
                "interlock": ManagedProcessState.RUNNING,
            }
        )
        _startup(manager, wait_process_ids=["interlock"])
        self.assertEqual(
            manager._not_running_process_ids(ManagedProcessState.RUNNING),
            ["hdf_writer"],
        )
        manager.start_process("hdf_writer")
        self.assertEqual(
            manager._not_running_process_ids(ManagedProcessState.RUNNING), []
        )


if __name__ == "__main__":
    unittest.main()
