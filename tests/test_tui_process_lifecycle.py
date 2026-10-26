"""Mirrored process visibility must not grant local lifecycle control."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from experiment_control._tui.app import ManagerTUI


class ProcessLifecycleOwnershipTests(unittest.TestCase):
    def make_app(self, processes):
        app = object.__new__(ManagerTUI)
        app._processes = processes
        app._action_target = Mock(return_value="process")
        app._selected_process = Mock(return_value=processes[0]["process_id"])
        app._run_bulk_rpc_worker = Mock()
        app._submit_resource_action = Mock()
        app.notify = Mock()
        app.push_screen = Mock(side_effect=lambda screen, callback: callback(True))
        return app

    def test_bulk_start_and_stop_only_submit_local_processes(self):
        for action in ("start", "stop"):
            for local_count in (0, 1):
                with self.subTest(action=action, local_count=local_count):
                    processes = [
                        {
                            "process_id": "remote_flag",
                            "is_remote": True,
                            "state": "STOPPED",
                        },
                        {
                            "process_id": "remote_source",
                            "source_kind": "federated",
                            "state": "RUNNING",
                        },
                    ]
                    if local_count:
                        processes.append({"process_id": "local", "state": "STOPPED"})
                    app = self.make_app(processes)
                    getattr(app, f"action_drivers_{action}_all")()
                    kwargs = app._run_bulk_rpc_worker.call_args.kwargs
                    expected = (
                        [
                            (
                                "local",
                                {
                                    "type": f"manager.processes.{action}",
                                    "process_id": "local",
                                },
                            )
                        ]
                        if local_count
                        else []
                    )
                    self.assertEqual(kwargs["items"], expected)
                    self.assertEqual(kwargs["skipped_count"], 2)

    def test_individual_actions_reject_remote_processes_before_confirmation(self):
        for action in ("start", "stop", "restart"):
            for metadata in ({"is_remote": True}, {"source_kind": "federated"}):
                with self.subTest(action=action, metadata=metadata):
                    app = self.make_app(
                        [{"process_id": "remote", "state": "STOPPED", **metadata}]
                    )
                    getattr(app, f"action_driver_{action}")()
                    app._submit_resource_action.assert_not_called()
                    app.push_screen.assert_not_called()
                    app.notify.assert_called_once()

    def test_local_process_actions_still_submit(self):
        for action in ("start", "stop", "restart"):
            with self.subTest(action=action):
                state = "STOPPED" if action == "start" else "RUNNING"
                app = self.make_app(
                    [
                        {
                            "process_id": "local",
                            "state": state,
                            "pid": None if action == "start" else 12,
                        }
                    ]
                )
                getattr(app, f"action_driver_{action}")()
                self.assertEqual(
                    app._submit_resource_action.call_args.kwargs["payload"],
                    {
                        "type": f"manager.processes.{action}",
                        "process_id": "local",
                    },
                )

    def test_remote_process_capabilities_remain_available(self):
        app = self.make_app([{"process_id": "remote"}])
        app._process_status_map = {
            "remote": {
                "process_id": "remote",
                "state": "FEDERATED",
                "registered": True,
                "source_kind": "federated",
                "liveness": "ONLINE",
            }
        }
        self.assertTrue(app._process_capabilities_probe_ready("remote"))

    def test_selected_remote_process_has_no_lifecycle_buttons(self):
        for metadata in ({"is_remote": True}, {"source_kind": "federated"}):
            with self.subTest(metadata=metadata):
                app = self.make_app(
                    [{"process_id": "remote", "state": "RUNNING", **metadata}]
                )
                app._selected_resource_key = "process:remote"
                app._resource_views = {
                    "process:remote": SimpleNamespace(
                        kind="process",
                        resource_id="remote",
                        health="healthy",
                        state="RUNNING",
                        connection="ONLINE",
                        is_remote=False,
                    )
                }
                app._pending_resource_actions = set()
                app._size = SimpleNamespace(width=200)
                app._connection_cell = Mock(
                    return_value=SimpleNamespace(plain="online", style="")
                )
                app._health_cell = app._connection_cell
                app._runtime_cell = app._connection_cell
                buttons = {
                    f"#action_{a}": SimpleNamespace(disabled=False)
                    for a in ("start", "stop", "restart")
                }
                app.query_one = Mock(
                    side_effect=lambda key, *args: buttons.get(key, Mock())
                )
                app._refresh_selected_resource_chrome()
                self.assertTrue(all(button.disabled for button in buttons.values()))
