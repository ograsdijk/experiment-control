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

from experiment_control._tui.app import ManagerTUI


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
        app._load_manager_log_tail_bootstrap = lambda *a, **k: None  # type: ignore[method-assign]
        async with app.run_test(headless=True, size=(120, 50)) as pilot:
            app._stop_event.set()
            app.streaming_enabled = False
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


if __name__ == "__main__":
    unittest.main()
