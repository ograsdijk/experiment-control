# ruff: noqa: E402

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control._manager.lifecycle import LifecycleMixin


class _FakeFederationHub:
    def __init__(self) -> None:
        self.activated = False

    def activate(self) -> None:
        self.activated = True


class _FakeManager(LifecycleMixin):
    def __init__(self) -> None:
        self._devices: dict[str, Any] = {}
        self._processes: dict[str, Any] = {}
        self._federation_hub = _FakeFederationHub()
        self.connect_all_calls = 0
        self.logs: list[dict[str, Any]] = []
        self._startup_sequence_active = False
        self._startup_sequence_complete_mono = None

    def _ensure_router_running(self, *, timeout_s: float, poll_ms: int) -> None:
        return

    def start_all_processes(self) -> None:
        return

    def start_all_drivers(self) -> None:
        return

    def connect_all_devices(self) -> None:
        self.connect_all_calls += 1

    def _emit_log(self, **kwargs: Any) -> None:
        self.logs.append(kwargs)


def test_startup_connect_true_is_ignored_with_deprecation_warning() -> None:
    manager = _FakeManager()

    manager.startup_sequence(
        start_drivers=False,
        start_processes=False,
        wait_processes_running=False,
        connect=True,
        wait_for_registered=False,
        wait_for_online=False,
    )

    assert manager.connect_all_calls == 0
    assert any(
        entry.get("topic") == "manager.startup.connect_deprecated"
        and entry.get("payload", {}).get("connect_value") is True
        for entry in manager.logs
    )


def test_startup_connect_false_is_ignored_with_deprecation_warning() -> None:
    manager = _FakeManager()

    manager.startup_sequence(
        start_drivers=False,
        start_processes=False,
        wait_processes_running=False,
        connect=False,
        wait_for_registered=False,
        wait_for_online=False,
    )

    assert manager.connect_all_calls == 0
    assert any(
        entry.get("topic") == "manager.startup.connect_deprecated"
        and entry.get("payload", {}).get("connect_value") is False
        for entry in manager.logs
    )


def test_startup_without_connect_argument_does_not_warn() -> None:
    manager = _FakeManager()

    manager.startup_sequence(
        start_drivers=False,
        start_processes=False,
        wait_processes_running=False,
        wait_for_registered=False,
        wait_for_online=False,
    )

    assert manager.connect_all_calls == 0
    assert not any(
        entry.get("topic") == "manager.startup.connect_deprecated"
        for entry in manager.logs
    )
