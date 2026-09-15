"""Regression tests for the TUI render performance optimizations.

These cover the correctness guards behind three changes in
``src/experiment_control/_tui/app.py``:

- errors-table revision skip-guard (render only when ``_errors`` changed),
- ``_ingest_manager_log_entry`` returning whether it appended (so the drain
  loop does not mark the errors table dirty for sub-threshold / duplicate logs),
- the members-table tuple-hash fingerprint covering exactly the rendered fields.
"""
from __future__ import annotations

import unittest
from collections import deque
from types import SimpleNamespace

from experiment_control._tui.app import ManagerTUI, _RESOURCE_COLUMNS
from experiment_control._tui.models import DeviceStatus, ResourceView
from experiment_control._tui.screens import ResultScreen


class _FakeTable:
    """Minimal stand-in for a Textual DataTable used by _render_errors_table."""

    def __init__(self) -> None:
        self.clear_calls = 0
        self.rows: list[tuple] = []

    def clear(self, columns: bool = False) -> None:
        self.clear_calls += 1
        self.rows = []

    def add_row(self, *args, **kwargs) -> None:
        self.rows.append(args)


def _errors_app() -> ManagerTUI:
    app = object.__new__(ManagerTUI)
    app._errors = deque(maxlen=200)
    app._errors_rev = 0
    app._errors_rendered_rev = -1
    app._seen_error_fingerprints = set()
    app._seen_error_fingerprint_order = deque(maxlen=2000)
    app._last_manager_log_t_mono = None
    return app


class ErrorsTableSkipGuardTests(unittest.TestCase):
    def test_render_skips_when_errors_unchanged(self) -> None:
        app = _errors_app()
        table = _FakeTable()
        app.query_one = lambda *a, **k: table  # type: ignore[method-assign]

        app._record_error(
            source="device",
            id_="d1",
            topic="manager.log",
            message="boom",
            severity="error",
            fingerprint="fp1",
        )
        self.assertEqual(app._errors_rev, 1)
        self.assertEqual(table.clear_calls, 1)
        self.assertEqual(len(table.rows), 1)

        # Second render with no change to _errors is a no-op.
        app._render_errors_table()
        self.assertEqual(table.clear_calls, 1)

        # A genuinely new error renders again.
        app._record_error(
            source="device",
            id_="d2",
            topic="manager.log",
            message="boom2",
            severity="error",
            fingerprint="fp2",
        )
        self.assertEqual(app._errors_rev, 2)
        self.assertEqual(table.clear_calls, 2)
        self.assertEqual(len(table.rows), 2)


class IngestManagerLogReturnTests(unittest.TestCase):
    def _entry(self, severity: str, t_mono: float, message: str = "boom") -> dict:
        return {
            "severity": severity,
            "topic": "manager.log",
            "source_kind": "device",
            "source_id": "d1",
            "device_id": "d1",
            "message": message,
            "ts": {"t_wall": 1.0, "t_mono": t_mono},
        }

    def test_new_warning_appends_and_returns_true(self) -> None:
        app = _errors_app()
        self.assertTrue(app._ingest_manager_log_entry(self._entry("warning", 2.0)))
        self.assertEqual(app._errors_rev, 1)
        self.assertEqual(len(app._errors), 1)

    def test_duplicate_returns_false_and_does_not_append(self) -> None:
        app = _errors_app()
        entry = self._entry("warning", 2.0)
        self.assertTrue(app._ingest_manager_log_entry(entry))
        # Same fingerprint (same content + t_mono) -> deduped.
        self.assertFalse(app._ingest_manager_log_entry(self._entry("warning", 2.0)))
        self.assertEqual(app._errors_rev, 1)
        self.assertEqual(len(app._errors), 1)

    def test_sub_threshold_info_returns_false(self) -> None:
        app = _errors_app()
        self.assertFalse(app._ingest_manager_log_entry(self._entry("info", 3.0)))
        self.assertEqual(app._errors_rev, 0)
        self.assertEqual(len(app._errors), 0)
        # It still advances the log watermark.
        self.assertEqual(app._last_manager_log_t_mono, 3.0)


class MembersFingerprintTests(unittest.TestCase):
    def _member(self) -> dict:
        return {
            "name": "voltage",
            "kind": "attribute",
            "readable": True,
            "settable": True,
            "return_annotation": "",
            "value_annotation": "float",
            "source": "device",
            "doc": "Output voltage",
            # Non-rendered extras that must NOT affect the fingerprint:
            "signature": "(value: float)",
            "default": 0.0,
        }

    def test_rendered_field_change_changes_fingerprint(self) -> None:
        fp = ManagerTUI._members_render_fingerprint
        base = fp([self._member()])
        for field, new in (
            ("name", "current"),
            ("kind", "method"),
            ("readable", False),
            ("settable", False),
            ("return_annotation", "int"),
            ("value_annotation", "int"),
            ("source", "driver"),
            ("doc", "Different doc"),
        ):
            m = self._member()
            m[field] = new
            self.assertNotEqual(
                fp([m]), base, f"field {field!r} should change the fingerprint"
            )

    def test_non_rendered_field_change_keeps_fingerprint(self) -> None:
        fp = ManagerTUI._members_render_fingerprint
        base = fp([self._member()])
        m = self._member()
        m["signature"] = "(value: int)"
        m["default"] = 42
        m["extra_key"] = "anything"
        self.assertEqual(fp([m]), base)

    def test_doc_truncated_to_40_chars(self) -> None:
        fp = ManagerTUI._members_render_fingerprint
        m1 = self._member()
        m2 = self._member()
        m1["doc"] = "x" * 40 + "AAAA"
        m2["doc"] = "x" * 40 + "BBBB"
        # The render truncates doc to 40 chars, so changes past char 40 must
        # not force a re-render.
        self.assertEqual(fp([m1]), fp([m2]))

    def test_order_matters(self) -> None:
        fp = ManagerTUI._members_render_fingerprint
        a = self._member()
        b = self._member()
        b["name"] = "current"
        self.assertNotEqual(fp([a, b]), fp([b, a]))


class HeadlessRenderContentTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end against real Textual DataTables (headless): the skip-guard and
    tuple-hash fingerprint must not change the rendered content."""

    async def test_errors_and_members_render_content(self) -> None:
        from textual.widgets import DataTable

        app = ManagerTUI(snapshot_period_s=3600.0, rpc_timeout_ms=20)
        app._rpc_call = lambda *a, **k: None  # type: ignore[method-assign]
        # RPC now flows through the single-owner worker; stub the submit path so
        # the headless render test doesn't do real socket round-trips on mount.
        app._rpc_submit = lambda *a, **k: None  # type: ignore[method-assign]
        async with app.run_test(headless=True, size=(120, 50)) as pilot:
            app._stop_event.set()
            app.streaming_enabled = False
            app._activity_open = True
            await pilot.pause()

            errors = app.query_one("#errors_table", DataTable)
            for i in range(3):
                app._record_error(
                    source="device",
                    id_=f"d{i}",
                    topic="manager.log",
                    message=f"msg {i}",
                    severity="error",
                    fingerprint=f"fp{i}",
                )
            self.assertEqual(errors.row_count, 3)
            # Repeated render with no change keeps the rendered rows intact.
            app._render_errors_table()
            self.assertEqual(errors.row_count, 3)
            # A new error renders the additional row.
            app._record_error(
                source="device",
                id_="d3",
                topic="manager.log",
                message="msg 3",
                severity="warning",
                fingerprint="fp3",
            )
            self.assertEqual(errors.row_count, 4)

            # Members table: seed capabilities directly so no RPC is needed.
            did = "dev0"
            app._selected_device_id = did
            app._members_source = "device"
            app._inspector_mode = "device"
            app._members_last[did] = [
                {
                    "name": f"member_{i}",
                    "kind": "attribute",
                    "readable": True,
                    "settable": True,
                    "value_annotation": "float",
                    "source": "device",
                    "doc": f"doc {i}",
                }
                for i in range(6)
            ]
            members = app.query_one("#members_table", DataTable)
            app._render_members_table()
            self.assertEqual(members.row_count, 6)
            # Unchanged members -> fingerprint guard keeps the same rows.
            app._render_members_table()
            self.assertEqual(members.row_count, 6)
            # Add a member -> re-render reflects it.
            app._members_last[did].append(
                {
                    "name": "member_extra",
                    "kind": "method",
                    "readable": False,
                    "settable": False,
                    "return_annotation": "None",
                    "source": "device",
                    "doc": "extra",
                }
            )
            app._render_members_table()
            self.assertEqual(members.row_count, 7)

    async def test_short_result_uses_compact_dialog(self) -> None:
        app = ManagerTUI(snapshot_period_s=3600.0, rpc_timeout_ms=20)
        app._rpc_call = lambda *a, **k: None  # type: ignore[method-assign]
        app._rpc_submit = lambda *a, **k: None  # type: ignore[method-assign]
        app._drain_pub_queue = lambda: None  # type: ignore[method-assign]
        async with app.run_test(headless=True, size=(120, 40)) as pilot:
            app._stop_event.set()
            app.push_screen(ResultScreen("Result · d1.read", '{"value": 1}'))
            await pilot.pause()

            dialog = app.screen.query_one("#result_dialog")
            body = app.screen.query_one("#result_body")
            self.assertEqual(dialog.region.width, 80)
            self.assertLess(dialog.region.height, 20)
            self.assertGreaterEqual(body.region.height, 3)

    async def test_long_result_dialog_stays_capped_and_scrollable(self) -> None:
        app = ManagerTUI(snapshot_period_s=3600.0, rpc_timeout_ms=20)
        app._rpc_call = lambda *a, **k: None  # type: ignore[method-assign]
        app._rpc_submit = lambda *a, **k: None  # type: ignore[method-assign]
        app._drain_pub_queue = lambda: None  # type: ignore[method-assign]
        async with app.run_test(headless=True, size=(120, 40)) as pilot:
            app._stop_event.set()
            result = "\n".join(f"line {index}" for index in range(100))
            app.push_screen(ResultScreen("Result · d1.read", result))
            await pilot.pause()

            dialog = app.screen.query_one("#result_dialog")
            body = app.screen.query_one("#result_body")
            self.assertLessEqual(dialog.region.height, 32)
            self.assertGreater(body.max_scroll_y, 0)


class _FakeProcTable:
    """Minimal DataTable stand-in for _render_processes_table."""

    def __init__(self) -> None:
        self.ordered_rows: list = []
        self.added: list[tuple[tuple, dict]] = []

    def add_row(self, *args, **kwargs) -> None:
        self.added.append((args, kwargs))

    def update_cell(self, *args, **kwargs) -> None:  # pragma: no cover - unused here
        pass

    def remove_row(self, *args, **kwargs) -> None:  # pragma: no cover - unused here
        pass

    def move_cursor(self, *args, **kwargs) -> None:  # pragma: no cover - unused here
        pass


class _ProcessSelectionTable:
    def get_row(self, *_args, **_kwargs):  # pragma: no cover - not used now
        raise AssertionError("get_row should not be called")


class ProcessesTableFederatedBadgeTests(unittest.TestCase):
    def test_remote_row_prefixed_with_badge_key_stays_raw(self) -> None:
        app = object.__new__(ManagerTUI)
        app._suppress_selection_events = False
        app._selected_process_id = None
        app._has_user_process_selection = False
        app._processes = [
            {
                "process_id": "spb",
                "state": "RUNNING",
                "pid": 7,
                "hb_age_s": 0.5,
                "is_remote": True,
                "source_kind": "federated",
            },
            {"process_id": "local1", "state": "RUNNING", "pid": 8, "hb_age_s": 0.1},
        ]
        table = _FakeProcTable()
        app.query_one = lambda *a, **k: table  # type: ignore[method-assign]
        app.call_later = lambda *a, **k: None  # type: ignore[method-assign]

        app._render_processes_table()

        by_key = {kw["key"]: args for (args, kw) in table.added}
        # Display gets the ⇄ badge; the row key stays the raw process_id.
        self.assertEqual(by_key["spb"][0], "⇄ spb")
        self.assertEqual(by_key["local1"][0], "local1")

    def test_remote_process_selection_uses_row_key_not_display_label(self) -> None:
        app = object.__new__(ManagerTUI)
        app._suppress_selection_events = False
        app._selected_process_id = None
        app._has_user_process_selection = False
        app._set_inspector_mode = lambda mode: None  # type: ignore[method-assign]
        app._mark_inspector_dirty = lambda: None  # type: ignore[method-assign]
        app._render_inspector_if_needed = lambda *, force=False: None  # type: ignore[method-assign]
        table = _ProcessSelectionTable()
        app.query_one = lambda *a, **k: table  # type: ignore[method-assign]
        app._is_table_focused = lambda candidate: candidate is table  # type: ignore[method-assign]
        event = SimpleNamespace(row_key=SimpleNamespace(value="spb_microwave"))

        app._on_process_selected(event)
        self.assertEqual(app._selected_process_id, "spb_microwave")
        self.assertTrue(app._has_user_process_selection)

        app._selected_process_id = None
        app._has_user_process_selection = False
        app._on_process_cursor_moved(event)
        self.assertEqual(app._selected_process_id, "spb_microwave")


class UnifiedResourceModelTests(unittest.TestCase):
    def test_resource_key_and_search_text_cover_operator_fields(self) -> None:
        view = ResourceView(
            kind="device",
            resource_id="hornet_eql",
            health="stale",
            state="RUNNING/CONNECTED",
            age_s=4.2,
            error="heartbeat overdue",
            is_remote=True,
            owner_peer_id="vacuum-cryo",
        )
        self.assertEqual(view.key, "device:hornet_eql")
        self.assertIn("vacuum-cryo", view.searchable_text)
        self.assertIn("heartbeat overdue", view.searchable_text)

    def test_health_mapping_keeps_intentional_stops_neutral(self) -> None:
        status = DeviceStatus(
            device_id="d1",
            registered=False,
            liveness="OFFLINE",
            hb_age_s=None,
            telemetry_age_s=None,
            driver_state=None,
            device_state="DISCONNECTED",
            device_reachable=False,
            last_error=None,
            driver_proc_state="STOPPED",
            driver_pid=None,
            driver_restart_count=0,
            driver_last_exit_code=None,
            driver_last_error=None,
        )
        self.assertEqual(ManagerTUI._device_health(status), "neutral")
        status.liveness = "STALE"
        self.assertEqual(ManagerTUI._device_health(status), "stale")
        self.assertEqual(ManagerTUI._device_connection(status), "stale")
        status.liveness = "DISCONNECTED"
        self.assertEqual(ManagerTUI._device_connection(status), "disconnected")
        self.assertEqual(ManagerTUI._process_health({"state": "STOPPED"}), "neutral")
        self.assertEqual(ManagerTUI._process_health({"state": "CRASHLOOP"}), "failed")

    def test_non_null_result_opens_pretty_result_screen(self) -> None:
        app = object.__new__(ManagerTUI)
        pushed: list = []
        app.push_screen = pushed.append  # type: ignore[method-assign]
        app._show_command_result("Result · d1.read", None)
        self.assertEqual(pushed, [])
        app._show_command_result("Result · d1.read", {"value": [1, 2]})
        self.assertEqual(len(pushed), 1)
        self.assertIsInstance(pushed[0], ResultScreen)
        self.assertIn('\n  "value": [', pushed[0]._result_text)

    def test_mixed_resource_sort_keeps_empty_values_last(self) -> None:
        app = object.__new__(ManagerTUI)
        app._resource_sort_column = "connection"
        app._resource_sort_reverse = False
        views = [
            ResourceView("process", "alpha", "healthy", "RUNNING", 1.0, None),
            ResourceView(
                "device", "zulu", "healthy", "RUNNING", 2.0, None, "connected"
            ),
            ResourceView(
                "device", "beta", "stale", "RUNNING", 3.0, "late", "stale"
            ),
        ]
        self.assertEqual(
            [view.resource_id for view in app._sort_resource_views(views)],
            ["zulu", "beta", "alpha"],
        )
        app._resource_sort_reverse = True
        self.assertEqual(
            [view.resource_id for view in app._sort_resource_views(views)],
            ["beta", "zulu", "alpha"],
        )


class ConnectAllTests(unittest.TestCase):
    @staticmethod
    def _status(device_id: str, liveness: str, *, remote: bool = False) -> DeviceStatus:
        return DeviceStatus(
            device_id=device_id,
            registered=True,
            liveness=liveness,
            hb_age_s=0.2,
            telemetry_age_s=0.1,
            driver_state="OK",
            device_state="DISCONNECTED" if liveness == "DISCONNECTED" else "OK",
            device_reachable=liveness == "ONLINE",
            last_error=None,
            driver_proc_state="RUNNING",
            driver_pid=None if remote else 1,
            driver_restart_count=0,
            driver_last_exit_code=None,
            driver_last_error=None,
            is_remote=remote,
        )

    def test_connect_all_targets_only_eligible_local_devices(self) -> None:
        app = object.__new__(ManagerTUI)
        app._device_status = {
            "eligible": self._status("eligible", "DISCONNECTED"),
            "connected": self._status("connected", "ONLINE"),
            "remote": self._status("remote", "DISCONNECTED", remote=True),
        }
        calls: list[dict] = []
        app._run_bulk_rpc_worker = lambda **kwargs: calls.append(kwargs)  # type: ignore[method-assign]
        app.notify = lambda *args, **kwargs: None  # type: ignore[method-assign]
        app.action_devices_connect_all()
        self.assertEqual([item[0] for item in calls[0]["items"]], ["eligible"])
        self.assertEqual(calls[0]["skipped_count"], 2)


class PubCoalescingTests(unittest.TestCase):
    def test_latest_state_coalesces_but_logs_stay_ordered(self) -> None:
        import queue
        import threading

        app = object.__new__(ManagerTUI)
        app._latest_state_messages = {}
        app._state_lock = threading.Lock()
        app._coalesced_pub_messages = 0
        app._pub_queue = queue.Queue(maxsize=10)
        app._pub_queue_overflow_policy = "drop_newest"
        app._dropped_pub_messages = 0

        app._enqueue_pub_message(
            "manager.telemetry_update", {"device_id": "d1", "signals": {"x": 1}}
        )
        app._enqueue_pub_message(
            "manager.telemetry_update", {"device_id": "d1", "signals": {"x": 2}}
        )
        app._enqueue_pub_message("manager.log", {"message": "first"})
        app._enqueue_pub_message("manager.log", {"message": "second"})

        self.assertEqual(len(app._latest_state_messages), 1)
        latest = app._latest_state_messages[("manager.telemetry_update", "d1")][1]
        self.assertEqual(latest["signals"]["x"], 2)
        self.assertEqual(app._coalesced_pub_messages, 1)
        self.assertEqual(app._pub_queue.get_nowait()[1]["message"], "first")
        self.assertEqual(app._pub_queue.get_nowait()[1]["message"], "second")


class UnifiedResourceHeadlessTests(unittest.IsolatedAsyncioTestCase):
    async def test_filter_tabs_activity_and_stacked_navigation(self) -> None:
        from textual.widgets import Button, DataTable, Input, TabbedContent

        app = ManagerTUI(snapshot_period_s=3600.0, rpc_timeout_ms=20)
        app._rpc_submit = lambda *a, **k: None  # type: ignore[method-assign]
        app._background_rpc_submit = lambda *a, **k: None  # type: ignore[method-assign]
        async with app.run_test(headless=True, size=(80, 24)) as pilot:
            app._stop_event.set()
            app.streaming_enabled = False
            await pilot.pause()
            self.assertIs(app.focused, app.query_one("#resources_table", DataTable))
            app._device_status = {
                "healthy_dev": DeviceStatus(
                    device_id="healthy_dev",
                    registered=True,
                    liveness="ONLINE",
                    hb_age_s=0.2,
                    telemetry_age_s=0.1,
                    driver_state="RUNNING",
                    device_state="CONNECTED",
                    device_reachable=True,
                    last_error=None,
                    driver_proc_state="RUNNING",
                    driver_pid=1,
                    driver_restart_count=0,
                    driver_last_exit_code=None,
                    driver_last_error=None,
                )
            }
            app._processes = [{"process_id": "bad_proc", "state": "FAILED"}]
            app._render_resources_table()
            table = app.query_one("#resources_table", DataTable)
            self.assertEqual(table.row_count, 2)
            self.assertEqual(
                [str(row.key.value) for row in table.ordered_rows],
                ["device:healthy_dev", "process:bad_proc"],
            )
            self.assertEqual(
                str(table.get_cell("device:healthy_dev", "connection")),
                "● connected",
            )
            app._select_resource_key("device:healthy_dev")
            self.assertFalse(app.query_one("#action_disconnect").disabled)
            self.assertTrue(app.query_one("#action_connect").disabled)
            self.assertEqual(app.query_one("#action_start", Button).label.plain, "Start (s)")
            self.assertEqual(
                app.query_one("#action_disconnect", Button).label.plain, "Disconnect (d)"
            )
            self.assertEqual(app.query_one("#action_strip").region.height, 1)
            self.assertTrue(
                all(
                    button.region.height == 1
                    for button in app.query("#action_strip Button")
                )
            )
            from textual.binding import Binding

            hidden_footer_keys = {
                binding.key
                for binding in ManagerTUI.BINDINGS
                if isinstance(binding, Binding) and not binding.show
            }
            self.assertTrue({"s", "x", "r", "c", "d", "v"}.issubset(hidden_footer_keys))
            self.assertTrue(
                {"1", "2", "3", "a", "t", "enter", "e", "R"}.issubset(hidden_footer_keys)
            )
            self.assertEqual(str(app.query_one("#streaming_status").render()), "Streaming: ON (t)")
            inspector_bindings = {
                binding.key: binding
                for binding in ManagerTUI.BINDINGS
                if isinstance(binding, Binding)
                and binding.key in {"left_square_bracket", "right_square_bracket"}
            }
            self.assertEqual(inspector_bindings["right_square_bracket"].action, "inspector_next")
            self.assertEqual(inspector_bindings["left_square_bracket"].action, "inspector_previous")
            self.assertTrue(inspector_bindings["right_square_bracket"].priority)
            self.assertTrue(inspector_bindings["left_square_bracket"].priority)

            table.focus()
            await pilot.press("enter")
            self.assertIs(app.focused, app.query_one("#driver_table", DataTable))
            # Let TabPane.Focused settle before changing away from Overview.
            await pilot.pause()
            app.action_inspector_next()
            await pilot.pause(0.01)
            self.assertEqual(app.query_one("#inspector_tabs", TabbedContent).active, "telemetry")
            self.assertIs(app.focused, app.query_one("#telemetry_table", DataTable))
            app.action_inspector_previous()
            await pilot.pause(0.01)
            self.assertEqual(app.query_one("#inspector_tabs", TabbedContent).active, "overview")
            self.assertIs(app.focused, app.query_one("#driver_table", DataTable))
            await pilot.press("3")
            await pilot.pause()
            self.assertEqual(app.query_one("#inspector_tabs", TabbedContent).active, "commands")
            self.assertIs(app.focused, app.query_one("#members_table", DataTable))
            await pilot.press("escape")
            self.assertIs(app.focused, table)
            await pilot.press("enter")
            self.assertIs(app.focused, app.query_one("#members_table", DataTable))
            await pilot.press("escape")
            self.assertIs(app.focused, table)
            await pilot.press("u")
            self.assertEqual(table.row_count, 1)
            await pilot.press("u")
            self.assertEqual(table.row_count, 2)
            app._processes = [{"process_id": "bad_proc", "state": "RUNNING"}]
            app._render_resources_table()
            await pilot.press("u")
            self.assertEqual(table.row_count, 0)
            await pilot.press("u")
            self.assertEqual(table.row_count, 2)
            app._select_resource_key("device:healthy_dev")
            search = app.query_one("#resource_filter", Input)
            search.focus()
            await pilot.pause()
            self.assertFalse(app.check_action("inspector_next", ()))
            self.assertEqual(app.query_one("#inspector_tabs", TabbedContent).active, "commands")

            resource_key = app._resource_column_keys["resource"]
            resource_column = table.columns[resource_key]
            app._on_resource_header_selected(
                DataTable.HeaderSelected(table, resource_key, 1, resource_column.label)
            )
            self.assertEqual(app._resource_sort_column, "resource")
            self.assertFalse(app._resource_sort_reverse)
            self.assertEqual(
                [str(row.key.value) for row in table.ordered_rows],
                ["process:bad_proc", "device:healthy_dev"],
            )
            self.assertEqual(app._selected_resource_key, "device:healthy_dev")
            self.assertEqual(resource_column.label.plain, "resource ▲")
            app.action_resource_sort_reverse()
            self.assertEqual(
                [str(row.key.value) for row in table.ordered_rows],
                ["device:healthy_dev", "process:bad_proc"],
            )
            self.assertEqual(resource_column.label.plain, "resource ▼")
            for column_name, label in _RESOURCE_COLUMNS:
                app._set_resource_sort(column_name)
                column = table.columns[app._resource_column_keys[column_name]]
                self.assertEqual(column.label.plain, f"{label} ▲")
                self.assertGreaterEqual(column.content_width, column.label.cell_len)

            search.value = "healthy_dev"
            await pilot.pause()
            self.assertEqual(table.row_count, 1)

            app.action_inspector_commands()
            self.assertEqual(app.query_one("#inspector_tabs", TabbedContent).active, "commands")
            app._record_error(
                source="process",
                id_="bad_proc",
                topic="manager.log",
                message="failed",
                severity="error",
                fingerprint="bad-proc-failed",
            )
            self.assertEqual(app._activity_unread_error, 1)
            app.action_toggle_activity()
            self.assertTrue(app._activity_open)
            self.assertEqual(app._activity_unread_error, 0)

            app._resource_filter = ""
            search.value = ""
            await pilot.pause()
            app._select_resource_key("process:bad_proc")
            app._narrow_show_inspector = True
            app._apply_responsive_layout(80)
            self.assertTrue(app.query_one("#navigator").display)
            self.assertTrue(app.query_one("#inspector").display)
            self.assertEqual(app.query_one("#navigator").region.x, 0)
            self.assertEqual(app.query_one("#inspector").region.x, 0)
            self.assertLess(
                app.query_one("#navigator").region.y,
                app.query_one("#inspector").region.y,
            )

            app._processes = [
                {"process_id": f"process_{index:02d}", "state": "RUNNING"}
                for index in range(30)
            ]
            app._resource_order = []
            app._render_resources_table()
            await pilot.pause()
            self.assertLessEqual(app.query_one("#navigator").region.height, 16)
            self.assertGreater(table.max_scroll_y, 0)


if __name__ == "__main__":
    unittest.main()
