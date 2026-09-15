"""Regression tests for the TUI render performance optimizations.

These cover the correctness guards behind three changes in
``src/experiment_control/_tui/app.py``:

- errors-table revision skip-guard (render only when ``_errors`` changed),
- ``_ingest_manager_log_entry`` returning whether it appended (so the drain
  loop does not mark the errors table dirty for sub-threshold / duplicate logs),
- the members-table tuple-hash fingerprint covering exactly the rendered fields.
"""
from __future__ import annotations

import time
import unittest
from collections import deque
from types import SimpleNamespace

from experiment_control._tui.app import ManagerTUI, _RESOURCE_COLUMNS
from experiment_control._tui.models import DeviceStatus, ResourceView
from experiment_control._tui.screens import ResultScreen
from experiment_control._tui.status_presentation import (
    format_age,
    render_health_state,
    render_link_state,
    render_run_state,
)


class _FakeTable:
    """Minimal stand-in for a Textual DataTable used by _render_errors_table."""

    def __init__(self) -> None:
        self.clear_calls = 0
        self.rows: list[tuple] = []

    def clear(self, columns: bool = False) -> None:
        self.clear_calls += 1
        self.rows = []

    def add_row(self, *args, **kwargs) -> None:
        self.rows.append((args, kwargs))


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
    def test_render_allows_duplicate_error_fingerprints(self) -> None:
        app = _errors_app()
        table = _FakeTable()
        app.query_one = lambda *a, **k: table  # type: ignore[method-assign]

        for device_id in ("d1", "d2"):
            app._record_error(
                source="device",
                id_=device_id,
                topic="manager.heartbeat",
                message="FAULT/DISCONNECTED",
                severity="error",
                fingerprint="same-fingerprint",
            )

        self.assertEqual(len(table.rows), 2)
        self.assertEqual(
            [row[1]["key"] for row in table.rows], ["error-0", "error-1"]
        )

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

    async def test_process_telemetry_contains_signals_not_status_config(self) -> None:
        from textual.widgets import DataTable

        app = ManagerTUI(snapshot_period_s=3600.0, rpc_timeout_ms=20)
        app._rpc_submit = lambda *a, **k: None  # type: ignore[method-assign]
        app._background_rpc_submit = lambda *a, **k: None  # type: ignore[method-assign]
        async with app.run_test(headless=True, size=(120, 40)) as pilot:
            app._stop_event.set()
            process = {
                "process_id": "writer",
                "state": "RUNNING",
                "pid": 42,
                "registered": True,
                "argv": ["python", "writer.py"],
                "heartbeat_timeout_s": 3.0,
            }
            app._processes = [process]
            app._process_status_map = {"writer": process}
            app._enqueue_pub_message(
                "manager.process_telemetry_update",
                {
                    "process_id": "writer",
                    "signals": {
                        "writing_active": {
                            "value": True,
                            "units": "",
                            "quality": "ok",
                            "ts": {"t_mono": time.monotonic()},
                        }
                    },
                },
            )
            app._drain_pub_queue()
            self.assertIn("writer", app._process_telemetry_cache)
            app._render_resources_table()
            app._select_resource_key("process:writer")
            await pilot.pause()

            telemetry = app.query_one("#telemetry_table", DataTable)
            self.assertEqual(telemetry.row_count, 1)
            self.assertEqual(str(telemetry.get_row_at(0)[0]), "writing_active")
            self.assertNotIn("argv", [str(telemetry.get_row_at(0)[0])])
            details = app.query_one("#process_table", DataTable)
            detail_fields = [str(details.get_row_at(row)[0]) for row in range(details.row_count)]
            self.assertIn("registered", detail_fields)


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

    def test_compact_status_symbols_and_age_formatting(self) -> None:
        self.assertEqual(render_link_state("ONLINE").plain, "●")
        self.assertEqual(render_link_state("DISCONNECTED").plain, "○")
        self.assertEqual(render_link_state("STALE").plain, "◐")
        self.assertEqual(render_link_state("OFFLINE").plain, "×")
        self.assertNotEqual(
            render_link_state("OFFLINE").style,
            render_link_state("OFFLINE", failure_related=True).style,
        )
        self.assertEqual(render_link_state(None).plain, "—")
        self.assertEqual(render_link_state("unexpected").plain, "?")
        self.assertEqual(render_health_state("healthy").plain, "◆")
        self.assertEqual(render_health_state("degraded").plain, "▲")
        self.assertEqual(render_health_state("stale").plain, "△")
        self.assertEqual(render_health_state("failed").plain, "×")
        self.assertEqual(render_health_state("neutral").plain, "·")
        self.assertEqual(render_health_state("unknown").plain, "?")
        self.assertEqual(render_health_state("transition").plain, "◇")
        self.assertEqual(render_run_state("RUNNING").plain, "▶")
        self.assertEqual(render_run_state("STARTING").plain, "▷")
        self.assertEqual(render_run_state("STOPPING").plain, "◼")
        self.assertEqual(render_run_state("STOPPED").plain, "■")
        self.assertEqual(render_run_state("EXITED").plain, "□")
        self.assertEqual(render_run_state("FAILED").plain, "×")
        self.assertEqual(render_run_state("CRASHLOOP").plain, "↻")
        self.assertEqual(render_run_state("UNKNOWN").plain, "?")
        self.assertEqual(render_run_state(None).plain, "?")
        indicators = (
            *(render_link_state(state) for state in ("ONLINE", "DISCONNECTED", "STALE", "OFFLINE", None, "unexpected")),
            *(
                render_health_state(state)
                for state in (
                    "healthy",
                    "degraded",
                    "stale",
                    "failed",
                    "neutral",
                    "unknown",
                    "transition",
                )
            ),
            *(
                render_run_state(state)
                for state in (
                    "RUNNING",
                    "STARTING",
                    "STOPPING",
                    "STOPPED",
                    "EXITED",
                    "FAILED",
                    "CRASHLOOP",
                    "UNKNOWN",
                )
            ),
        )
        self.assertTrue(all(indicator.cell_len == 1 for indicator in indicators))
        self.assertEqual(format_age(0.1), "0.1s")
        self.assertEqual(format_age(1.4), "1.4s")
        self.assertEqual(format_age(14.0), "14s")
        self.assertEqual(format_age(126.0), "2.1m")

    def test_device_health_precedence_and_distinct_link_failure_modes(self) -> None:
        status = DeviceStatus(
            device_id="d1", registered=True, liveness="ONLINE", hb_age_s=0.1,
            telemetry_age_s=0.1, driver_state="OK", device_state="FAULT",
            device_reachable=True, last_error="device fault", driver_proc_state="RUNNING",
            driver_pid=1, driver_restart_count=0, driver_last_exit_code=None,
            driver_last_error=None,
        )
        self.assertEqual(ManagerTUI._device_health(status), "failed")
        status.device_state = "DEGRADED"
        status.liveness = "DISCONNECTED"
        self.assertEqual(ManagerTUI._device_health(status), "degraded")
        status.device_state = "DISCONNECTED"
        self.assertEqual(ManagerTUI._device_health(status), "neutral")
        status.liveness = "OFFLINE"
        status.driver_proc_state = "CRASHLOOP"
        self.assertEqual(ManagerTUI._device_health(status), "failed")
        status.driver_proc_state = "RUNNING"
        status.liveness = "ONLINE"
        status.device_state = "UNKNOWN"
        status.driver_state = "OK"
        self.assertEqual(ManagerTUI._device_health(status), "unknown")

    def test_collected_views_keep_raw_states_searchable_and_federated_linked(self) -> None:
        app = object.__new__(ManagerTUI)
        app._device_status = {
            "pxi": DeviceStatus(
                device_id="pxi", registered=True, liveness="DISCONNECTED", hb_age_s=0.2,
                telemetry_age_s=None, driver_state="DEGRADED", device_state="DEGRADED",
                device_reachable=False, last_error=None, driver_proc_state="RUNNING",
                driver_pid=1, driver_restart_count=0, driver_last_exit_code=None,
                driver_last_error=None,
            )
        }
        app._processes = [
            {"process_id": "local", "state": "RUNNING"},
            {
                "process_id": "remote", "state": "FAILED", "is_remote": True,
                "liveness": "OFFLINE", "source_kind": "federated",
            },
        ]
        views = {view.key: view for view in app._collect_resource_views()}
        device = views["device:pxi"]
        self.assertEqual((device.connection, device.health, device.state), ("DISCONNECTED", "degraded", "RUNNING"))
        self.assertIn("degraded", device.searchable_text)
        self.assertIn("disconnected", device.searchable_text)
        self.assertEqual(views["process:local"].connection, None)
        self.assertEqual(views["process:remote"].connection, "OFFLINE")
        self.assertEqual(views["process:remote"].health, "failed")

        app._device_status["pxi"].liveness = None
        self.assertEqual(app._collect_resource_views()[0].connection, "UNKNOWN")

    def test_health_summary_counts_degraded_as_an_issue(self) -> None:
        app = object.__new__(ManagerTUI)
        app.backend_connected = True
        app._backend_status_text = "Backend: connected"
        app._unhealthy_only = False
        summary = app._health_summary_text(
            [ResourceView("device", "pxi", "degraded", "RUNNING", 0.2, None)]
        )
        self.assertIn("ISSUES // 1", summary.plain)

    def test_remote_link_loss_is_an_operator_issue(self) -> None:
        local_disconnected = ResourceView(
            "device", "local", "neutral", "RUNNING", None, None, "DISCONNECTED"
        )
        remote_offline = ResourceView(
            "process",
            "remote_offline",
            "healthy",
            "RUNNING",
            None,
            None,
            "OFFLINE",
            is_remote=True,
        )
        remote_stale = ResourceView(
            "process",
            "remote_stale",
            "healthy",
            "RUNNING",
            None,
            None,
            "STALE",
            is_remote=True,
        )
        remote_online = ResourceView(
            "process",
            "remote_online",
            "healthy",
            "RUNNING",
            None,
            None,
            "ONLINE",
            is_remote=True,
        )
        degraded = ResourceView("device", "degraded", "degraded", "RUNNING", None, None)
        self.assertFalse(ManagerTUI._resource_has_issue(local_disconnected))
        self.assertTrue(ManagerTUI._resource_has_issue(remote_offline))
        self.assertTrue(ManagerTUI._resource_has_issue(remote_stale))
        self.assertFalse(ManagerTUI._resource_has_issue(remote_online))
        self.assertTrue(ManagerTUI._resource_has_issue(degraded))

        app = object.__new__(ManagerTUI)
        app._unhealthy_only = True
        app._resource_filter = ""
        app._resource_sort_column = "resource"
        app._resource_sort_reverse = False
        app._collect_resource_views = lambda: [  # type: ignore[method-assign]
            local_disconnected,
            remote_offline,
            remote_stale,
            remote_online,
            degraded,
        ]
        self.assertEqual(
            [view.resource_id for view in app._visible_resource_views()],
            ["degraded", "remote_offline", "remote_stale"],
        )
        app.backend_connected = True
        app._backend_status_text = "Backend: connected"
        summary = app._health_summary_text(
            [local_disconnected, remote_offline, remote_stale, remote_online, degraded]
        )
        self.assertIn("ISSUES // 3", summary.plain)

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
        from textual.widgets import Button, DataTable, Input, Label, TabbedContent

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
                    driver_state="OK",
                    device_state="OK",
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
                "●",
            )
            device_headers = [
                column.label.plain
                for column in app.query_one("#devices_table", DataTable).columns.values()
            ]
            process_headers = [
                column.label.plain
                for column in app.query_one("#processes_table", DataTable).columns.values()
            ]
            telemetry_headers = [
                column.label.plain
                for column in app.query_one("#telemetry_table", DataTable).columns.values()
            ]
            self.assertIn("HB_AGE", device_headers)
            self.assertIn("TELEMETRY_AGE", device_headers)
            self.assertIn("HB_AGE", process_headers)
            self.assertIn("AGE", telemetry_headers)
            app._select_resource_key("device:healthy_dev")
            selected_title = app.query_one("#selected_resource_title", Label).render()
            self.assertIn("LINK ● ONLINE", selected_title.plain)
            self.assertIn("HEALTH ◆ HEALTHY", selected_title.plain)
            self.assertIn("RUN ▶ RUNNING", selected_title.plain)

            original_view = app._resource_views["device:healthy_dev"]
            app._resource_views["device:healthy_dev"] = ResourceView(
                kind=original_view.kind,
                resource_id=original_view.resource_id,
                health=original_view.health,
                state=original_view.state,
                age_s=original_view.age_s,
                error=original_view.error,
                connection=original_view.connection,
                is_remote=True,
                owner_peer_id="remote-laboratory",
            )
            app._refresh_selected_resource_chrome()
            narrow_title = app.query_one("#selected_resource_title", Label).render()
            self.assertIn("● ONLINE", narrow_title.plain)
            self.assertIn("◆ HEALTHY", narrow_title.plain)
            self.assertIn("▶ RUNNING", narrow_title.plain)
            self.assertNotIn("LINK ● ONLINE", narrow_title.plain)
            app._resource_views["device:healthy_dev"] = original_view

            app._select_resource_key("process:bad_proc")
            process_title = app.query_one("#selected_resource_title", Label).render()
            self.assertIn("LINK —", process_title.plain)
            self.assertNotIn("N/A", process_title.plain)
            app._select_resource_key("device:healthy_dev")
            app._refresh_selected_resource_chrome()
            overview_details = app.query_one("#process_table", DataTable)
            overview_rows = [
                tuple(map(str, overview_details.get_row_at(row)))
                for row in range(overview_details.row_count)
            ]
            self.assertIn(("connection", "connected"), overview_rows)
            self.assertIn(("heartbeat_age", "0.2s"), overview_rows)
            self.assertIn(("telemetry_age", "0.1s"), overview_rows)
            status = app._device_status["healthy_dev"]
            status.hb_age_s = None
            status.telemetry_age_s = None
            app._render_inspector()
            overview_rows = [
                tuple(map(str, overview_details.get_row_at(row)))
                for row in range(overview_details.row_count)
            ]
            self.assertNotIn("heartbeat_age", {row[0] for row in overview_rows})
            self.assertNotIn("telemetry_age", {row[0] for row in overview_rows})
            self.assertFalse(app.query_one("#action_disconnect").disabled)
            self.assertTrue(app.query_one("#action_connect").disabled)
            self.assertEqual(app.query_one("#action_start", Button).label.plain, "START (s)")
            self.assertEqual(
                app.query_one("#action_disconnect", Button).label.plain, "DISCONNECT (d)"
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
                {"1", "2", "3", "4", "a", "t", "enter", "e", "R"}.issubset(
                    hidden_footer_keys
                )
            )
            self.assertEqual(str(app.query_one("#streaming_status").render()), "STREAM // ACTIVE (t)")
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
            app._config_cache["device:healthy_dev"] = {
                "driver": {"class_name": "DemoDriver"},
                "init_kwargs": {"port": "COM4", "password": "*** redacted ***"},
            }
            await pilot.press("4")
            await pilot.pause()
            self.assertEqual(app.query_one("#inspector_tabs", TabbedContent).active, "config")
            config = app.query_one("#config_table", DataTable)
            self.assertIs(app.focused, config)
            self.assertEqual(config.row_count, 3)
            config_rows = [tuple(map(str, config.get_row_at(row))) for row in range(3)]
            self.assertIn(("init_kwargs.port", "COM4"), config_rows)
            self.assertIn(("init_kwargs.password", "*** redacted ***"), config_rows)
            await pilot.press("escape")
            self.assertIs(app.focused, table)
            await pilot.press("enter")
            self.assertIs(app.focused, app.query_one("#config_table", DataTable))
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
            self.assertEqual(app.query_one("#inspector_tabs", TabbedContent).active, "config")

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
            self.assertEqual(resource_column.label.plain, "RESOURCE ▲")
            app.action_resource_sort_reverse()
            self.assertEqual(
                [str(row.key.value) for row in table.ordered_rows],
                ["device:healthy_dev", "process:bad_proc"],
            )
            self.assertEqual(resource_column.label.plain, "RESOURCE ▼")
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
            focus_before_activity = app.focused
            app.action_toggle_activity()
            await pilot.pause()
            self.assertTrue(app._activity_open)
            self.assertEqual(app._activity_unread_error, 0)
            self.assertIs(app.focused, app.query_one("#errors_table", DataTable))
            await pilot.press("right_square_bracket")
            self.assertEqual(app.query_one("#activity_tabs", TabbedContent).active, "events")
            self.assertIs(app.focused, app.query_one("#event_log"))
            await pilot.press("left_square_bracket")
            self.assertEqual(app.query_one("#activity_tabs", TabbedContent).active, "errors")
            self.assertIs(app.focused, app.query_one("#errors_table", DataTable))
            await pilot.press("a")
            self.assertFalse(app._activity_open)
            self.assertIs(app.focused, focus_before_activity)

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
