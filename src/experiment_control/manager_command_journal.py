from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from .manager_protocol import ManagerProtocol
    from .utils.command_journal import CommandJournal

    _MixinBase = ManagerProtocol
else:
    _MixinBase = object

Json = dict[str, Any]


def should_journal_command_action(action: Any) -> bool:
    text = str(action or "").strip().lower()
    if not text:
        return True
    if text.startswith("stream__"):
        return False
    if text.startswith("telemetry__"):
        return False
    if text == "capabilities" or text.endswith(".capabilities"):
        return False
    if text.endswith(".status"):
        return False
    if text.endswith(".list_status"):
        return False
    if text in {
        "device.get_status",
        "device.list_status",
        "manager.processes.list",
        "manager.devices.list",
    }:
        return False
    return True


class CommandJournalMixin(_MixinBase):
    """Mixin providing command-journal append + status helpers.

    Phase 8.2.2: migrated ``append_command_journal_entry`` and
    ``command_journal_status_payload`` from module-level helpers to
    mixin methods. Tests that called ``Manager._append_command_journal_entry``
    via the class continue to work via MRO.

    At runtime ``_MixinBase`` is ``object``; only mypy sees
    :class:`ManagerProtocol` as the base, which supplies the
    ``_safe_json`` signature (still on ``Manager`` itself).
    """

    # Owned-state attributes (concrete types declared on Manager).
    _command_journal: "CommandJournal | None"
    _command_journal_path: "Path | None"
    _command_journal_start_error: str | None
    _instance_id: str

    def _append_command_journal_entry(self, payload: Json) -> None:
        journal = self._command_journal
        if journal is None:
            return
        action_text = str(payload.get("action", "") or "")
        if not should_journal_command_action(action_text):
            return

        ts = payload.get("ts")
        t_wall = time.time()
        t_mono = time.monotonic()
        if isinstance(ts, dict):
            try:
                t_wall = float(ts.get("t_wall", t_wall))
            except Exception:
                pass
            try:
                t_mono = float(ts.get("t_mono", t_mono))
            except Exception:
                pass

        error_value = payload.get("error")
        error_json = ""
        if error_value is not None:
            error_json = self._safe_json(error_value)

        journal.append(
            {
                "t_wall": t_wall,
                "t_mono": t_mono,
                "instance_id": self._instance_id,
                "device_id": str(payload.get("device_id", "") or ""),
                "action": action_text,
                "params_json": str(payload.get("params_json", "") or ""),
                "ok": bool(payload.get("ok")),
                "status": payload.get("status"),
                "error_json": error_json,
                "result_json": str(payload.get("result_json", "") or ""),
                "request_id": payload.get("request_id"),
                "caller_process_id": payload.get("caller_process_id"),
                "source_kind": payload.get("source_kind"),
                "source_id": payload.get("source_id"),
                "is_remote_target": bool(payload.get("is_remote_target")),
            }
        )

    def _command_journal_status_payload(self) -> Json:
        journal = self._command_journal
        if journal is None:
            return {
                "enabled": False,
                "path": (
                    str(self._command_journal_path)
                    if self._command_journal_path is not None
                    else None
                ),
                "start_error": self._command_journal_start_error,
            }
        return journal.status()
