# ruff: noqa: E402

import sys
import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager import ManagedProcessState, Manager


class _HubStub:
    @staticmethod
    def forward_device_request(_req):
        return None


class _JournalStub:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def append(self, row: dict[str, object]) -> None:
        self.rows.append(dict(row))


def _build_manager() -> Manager:
    mgr = object.__new__(Manager)
    mgr._devices = {"trace1": SimpleNamespace(rpc_endpoint="tcp://127.0.0.1:7001")}  # type: ignore[attr-defined]
    mgr._processes = {}  # type: ignore[attr-defined]
    mgr._federation_hub = _HubStub()  # type: ignore[attr-defined]
    mgr._command_journal = None  # type: ignore[attr-defined]
    mgr._command_journal_path = None  # type: ignore[attr-defined]
    mgr._command_journal_start_error = None  # type: ignore[attr-defined]
    mgr._instance_id = "test-instance"  # type: ignore[attr-defined]
    return mgr  # type: ignore[return-value]


class ManagerCommandSourceTests(unittest.TestCase):
    def test_route_command_defaults_source_from_caller(self) -> None:
        mgr = _build_manager()
        captured = {}

        mgr._driver_is_stopped = lambda _handle: False  # type: ignore[attr-defined]
        mgr._apply_command_interceptors = (  # type: ignore[attr-defined]
            lambda cmd, request_id, caller_process_id: (True, cmd, None)
        )

        def fake_call_device_rpc(**kwargs):
            captured.update(kwargs)
            return {"ok": True}

        mgr._call_device_rpc = fake_call_device_rpc  # type: ignore[attr-defined]
        resp = Manager._route_internal_request(  # type: ignore[arg-type]
            mgr,
            {
                "type": "command",
                "device_id": "trace1",
                "action": "set_frequency",
                "params": {"hz": 1.0},
                "request_id": "req-1",
                "caller_process_id": "sequencer",
            },
        )
        self.assertTrue(resp.get("ok"))
        self.assertEqual(captured.get("request_id"), "req-1")
        self.assertEqual(captured.get("caller_process_id"), "sequencer")
        self.assertEqual(captured.get("source_kind"), "process")
        self.assertEqual(captured.get("source_id"), "sequencer")
        self.assertFalse(bool(captured.get("is_remote_target")))

    def test_route_command_uses_explicit_source_fields(self) -> None:
        mgr = _build_manager()
        captured = {}

        mgr._driver_is_stopped = lambda _handle: False  # type: ignore[attr-defined]
        mgr._apply_command_interceptors = (  # type: ignore[attr-defined]
            lambda cmd, request_id, caller_process_id: (True, cmd, None)
        )

        def fake_call_device_rpc(**kwargs):
            captured.update(kwargs)
            return {"ok": True}

        mgr._call_device_rpc = fake_call_device_rpc  # type: ignore[attr-defined]
        resp = Manager._route_internal_request(  # type: ignore[arg-type]
            mgr,
            {
                "type": "command",
                "device_id": "trace1",
                "action": "set_frequency",
                "params": {"hz": 2.0},
                "source_kind": "ui",
                "source_id": "webui-main",
            },
        )
        self.assertTrue(resp.get("ok"))
        self.assertEqual(captured.get("source_kind"), "ui")
        self.assertEqual(captured.get("source_id"), "webui-main")

    def test_command_journal_status_and_tail_when_disabled(self) -> None:
        mgr = _build_manager()
        status_resp = Manager._route_internal_request(  # type: ignore[arg-type]
            mgr, {"type": "manager.commands.journal.status"}
        )
        self.assertTrue(status_resp.get("ok"))
        self.assertFalse(status_resp.get("result", {}).get("enabled"))

        tail_resp = Manager._route_internal_request(  # type: ignore[arg-type]
            mgr, {"type": "manager.commands.journal.tail", "params": {"limit": 5}}
        )
        self.assertFalse(tail_resp.get("ok"))
        self.assertEqual(tail_resp.get("error", {}).get("code"), "journal_disabled")

    def test_publish_manager_command_event_appends_to_journal(self) -> None:
        mgr = object.__new__(Manager)
        mgr._external_pub = mock.Mock()  # type: ignore[attr-defined]
        mgr._event_hooks = []  # type: ignore[attr-defined]
        mgr._maybe_publish_log_event = mock.Mock()  # type: ignore[attr-defined]
        mgr._append_command_journal_entry = mock.Mock()  # type: ignore[attr-defined]

        Manager._publish_manager_event(  # type: ignore[arg-type]
            mgr, "manager.command", {"device_id": "trace1", "action": "set_frequency_hz"}
        )
        mgr._append_command_journal_entry.assert_called_once()  # type: ignore[attr-defined]

    def test_append_command_journal_entry_skips_stream_action(self) -> None:
        mgr = _build_manager()
        journal = _JournalStub()
        mgr._command_journal = journal  # type: ignore[attr-defined]

        Manager._append_command_journal_entry(  # type: ignore[arg-type]
            mgr,
            {
                "device_id": "trace1",
                "action": "stream__acquire_trace",
                "params_json": "{}",
                "ok": True,
            },
        )
        self.assertEqual(journal.rows, [])

    def test_append_command_journal_entry_keeps_regular_action(self) -> None:
        mgr = _build_manager()
        journal = _JournalStub()
        mgr._command_journal = journal  # type: ignore[attr-defined]

        Manager._append_command_journal_entry(  # type: ignore[arg-type]
            mgr,
            {
                "device_id": "trace1",
                "action": "set_frequency_hz",
                "params_json": '{"frequency_hz":1000}',
                "ok": True,
            },
        )
        self.assertEqual(len(journal.rows), 1)
        self.assertEqual(journal.rows[0].get("action"), "set_frequency_hz")

    def test_append_command_journal_entry_skips_status_action(self) -> None:
        mgr = _build_manager()
        journal = _JournalStub()
        mgr._command_journal = journal  # type: ignore[attr-defined]

        Manager._append_command_journal_entry(  # type: ignore[arg-type]
            mgr,
            {
                "device_id": "process:sequencer",
                "action": "sequencer.status",
                "params_json": "{}",
                "ok": True,
            },
        )
        self.assertEqual(journal.rows, [])

    def test_append_command_journal_entry_skips_capabilities_action(self) -> None:
        mgr = _build_manager()
        journal = _JournalStub()
        mgr._command_journal = journal  # type: ignore[attr-defined]

        for action in ("capabilities", "process.capabilities", "device.capabilities"):
            with self.subTest(action=action):
                Manager._append_command_journal_entry(  # type: ignore[arg-type]
                    mgr,
                    {
                        "device_id": "trace1",
                        "action": action,
                        "params_json": "{}",
                        "ok": True,
                    },
                )

        self.assertEqual(journal.rows, [])

    def test_process_rpc_publishes_manager_command_event(self) -> None:
        mgr = _build_manager()
        mgr._processes = {  # type: ignore[attr-defined]
            "sequencer": SimpleNamespace(
                state=ManagedProcessState.RUNNING,
                rpc_endpoint="tcp://127.0.0.1:9901",
            )
        }
        mgr._call_process_rpc = mock.Mock(  # type: ignore[attr-defined]
            return_value={"ok": True, "result": {"status": "running"}}
        )
        publish_mock = mock.Mock()
        mgr._publish_manager_event = publish_mock  # type: ignore[attr-defined]

        resp = Manager._route_internal_request(  # type: ignore[arg-type]
            mgr,
            {
                "type": "manager.processes.rpc",
                "request_id": "req-1",
                "process_id": "sequencer",
                "request": {
                    "type": "sequencer.start",
                    "params": {"sequence_id": "main"},
                    "request_id": "req-1",
                },
                "source_kind": "webui",
                "source_id": "fastapi",
            },
        )
        self.assertTrue(resp.get("ok"))
        publish_mock.assert_called_once()
        topic = publish_mock.call_args.args[0]
        payload = publish_mock.call_args.args[1]
        self.assertEqual(topic, "manager.command")
        self.assertEqual(payload.get("device_id"), "process:sequencer")
        self.assertEqual(payload.get("process_id"), "sequencer")
        self.assertEqual(payload.get("action"), "sequencer.start")
        self.assertEqual(payload.get("source_kind"), "webui")
        self.assertEqual(payload.get("source_id"), "fastapi")

    def test_process_rpc_sequencer_start_is_written_to_command_journal(self) -> None:
        mgr = _build_manager()
        mgr._external_pub = mock.Mock()  # type: ignore[attr-defined]
        mgr._event_hooks = []  # type: ignore[attr-defined]
        mgr._maybe_publish_log_event = mock.Mock()  # type: ignore[attr-defined]
        mgr._processes = {  # type: ignore[attr-defined]
            "sequencer": SimpleNamespace(
                state=ManagedProcessState.RUNNING,
                rpc_endpoint="tcp://127.0.0.1:9901",
            )
        }
        mgr._call_process_rpc = mock.Mock(  # type: ignore[attr-defined]
            return_value={"ok": True, "result": {"state": "RUNNING"}}
        )
        journal = _JournalStub()
        mgr._command_journal = journal  # type: ignore[attr-defined]

        resp = Manager._route_internal_request(  # type: ignore[arg-type]
            mgr,
            {
                "type": "manager.processes.rpc",
                "process_id": "sequencer",
                "request": {
                    "type": "sequencer.start",
                    "params": {"sequence_id": "main"},
                    "request_id": "req-seq-start",
                },
                "source_kind": "webui",
                "source_id": "fastapi",
            },
        )
        self.assertTrue(resp.get("ok"))
        actions = [str(row.get("action", "")) for row in journal.rows]
        self.assertIn("sequencer.start", actions)

    def test_process_start_publishes_manager_command_event(self) -> None:
        mgr = _build_manager()
        mgr.start_process = mock.Mock()  # type: ignore[attr-defined]
        publish_mock = mock.Mock()
        mgr._publish_manager_event = publish_mock  # type: ignore[attr-defined]

        resp = Manager._route_internal_request(  # type: ignore[arg-type]
            mgr,
            {
                "type": "manager.processes.start",
                "request_id": "req-start",
                "process_id": "sequencer",
                "source_kind": "webui",
                "source_id": "fastapi",
            },
        )
        self.assertTrue(resp.get("ok"))
        mgr.start_process.assert_called_once_with("sequencer")  # type: ignore[attr-defined]
        publish_mock.assert_called_once()
        topic = publish_mock.call_args.args[0]
        payload = publish_mock.call_args.args[1]
        self.assertEqual(topic, "manager.command")
        self.assertEqual(payload.get("action"), "manager.processes.start")
        self.assertEqual(payload.get("device_id"), "process:sequencer")

    def test_startup_sequence_does_not_double_connect_when_auto_connect_enabled(self) -> None:
        mgr = object.__new__(Manager)
        mgr._auto_connect_on_register = True  # type: ignore[attr-defined]
        mgr._devices = {}  # type: ignore[attr-defined]
        mgr._processes = {}  # type: ignore[attr-defined]
        mgr._federation_hub = SimpleNamespace(activate=lambda: None)  # type: ignore[attr-defined]
        mgr._ensure_router_running = mock.Mock()  # type: ignore[attr-defined]
        mgr.start_all_processes = mock.Mock()  # type: ignore[attr-defined]
        mgr.start_all_drivers = mock.Mock()  # type: ignore[attr-defined]
        mgr._pump_once = mock.Mock()  # type: ignore[attr-defined]
        mgr.connect_all_devices = mock.Mock(return_value={})  # type: ignore[attr-defined]

        Manager.startup_sequence(  # type: ignore[arg-type]
            mgr,
            start_drivers=False,
            start_processes=False,
            connect=None,
            wait_for_registered=False,
            wait_for_online=False,
        )
        mgr.connect_all_devices.assert_not_called()  # type: ignore[attr-defined]

    def test_manager_log_sink_honors_min_level_for_manager_log_entries(self) -> None:
        mgr = object.__new__(Manager)
        sink = io.StringIO()
        mgr._manager_log_stderr_enabled = False  # type: ignore[attr-defined]
        mgr._manager_log_file = sink  # type: ignore[attr-defined]
        mgr._manager_log_min_level_rank = Manager._severity_rank("error")  # type: ignore[attr-defined]
        mgr._manager_log_sink_recent = {}  # type: ignore[attr-defined]
        mgr._manager_log_sink_recent_window_s = 0.5  # type: ignore[attr-defined]
        mgr._manager_log_sink_recent_max = 256  # type: ignore[attr-defined]

        warning_payload = {
            "severity": "warning",
            "topic": "manager.heartbeat_error",
            "message": "warning-only",
            "ts": {"t_wall": 1.0, "t_mono": 1.0},
        }
        Manager._maybe_emit_manager_log_sink(  # type: ignore[arg-type]
            mgr, "manager.log", warning_payload
        )
        self.assertEqual(sink.getvalue(), "")

        error_payload = {
            "severity": "error",
            "topic": "manager.heartbeat_error",
            "message": "error-line",
            "ts": {"t_wall": 1.0, "t_mono": 1.0},
        }
        Manager._maybe_emit_manager_log_sink(  # type: ignore[arg-type]
            mgr, "manager.log", error_payload
        )
        self.assertIn("ERROR", sink.getvalue())
        self.assertIn("manager.heartbeat_error", sink.getvalue())

    def test_manager_log_sink_accepts_manager_error_topics(self) -> None:
        mgr = object.__new__(Manager)
        sink = io.StringIO()
        mgr._manager_log_stderr_enabled = False  # type: ignore[attr-defined]
        mgr._manager_log_file = sink  # type: ignore[attr-defined]
        mgr._manager_log_min_level_rank = Manager._severity_rank("error")  # type: ignore[attr-defined]
        mgr._manager_log_sink_recent = {}  # type: ignore[attr-defined]
        mgr._manager_log_sink_recent_window_s = 0.5  # type: ignore[attr-defined]
        mgr._manager_log_sink_recent_max = 256  # type: ignore[attr-defined]

        payload = {
            "message": "heartbeat parse failed",
            "source_kind": "manager",
            "source_id": "manager",
            "ts": {"t_wall": 1.0, "t_mono": 1.0},
        }
        Manager._maybe_emit_manager_log_sink(  # type: ignore[arg-type]
            mgr, "manager.heartbeat_error", payload
        )
        text = sink.getvalue()
        self.assertIn("ERROR", text)
        self.assertIn("manager.heartbeat_error", text)


if __name__ == "__main__":
    unittest.main()

