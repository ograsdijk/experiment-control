from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import experiment_control.manager as manager_module
from experiment_control._manager.models import Liveness
from experiment_control._manager.process_supervision import _auto_reconnect_should_attempt
from experiment_control.driver import DeviceRunner
from experiment_control.manager import Manager
from experiment_control.types import TelemetryCall, TelemetryOut


def test_auto_reconnect_is_suppressed_while_driver_heartbeat_is_stale() -> None:
    manager = SimpleNamespace(_heartbeat_timeout_s=3.0)
    handle = SimpleNamespace(
        driver_process_state="RUNNING",
        last_hb_recv_mono=96.0,
        driver_running_since_mono=90.0,
        spec=SimpleNamespace(auto_reconnect=SimpleNamespace(enabled=True)),
    )

    should_attempt, age_s, reason = _auto_reconnect_should_attempt(
        manager, "dev", handle, 100.0
    )

    assert should_attempt is False
    assert age_s is None
    assert reason == "heartbeat_stale"


def test_auto_reconnect_ignores_previous_generation_heartbeat() -> None:
    manager = SimpleNamespace(
        _heartbeat_timeout_s=3.0,
        _telemetry_last_bundle_ts={},
    )
    handle = SimpleNamespace(
        driver_process_state="RUNNING",
        last_hb_recv_mono=90.0,
        driver_running_since_mono=98.0,
        rpc_endpoint="tcp://127.0.0.1:12345",
        spec=SimpleNamespace(auto_reconnect=SimpleNamespace(enabled=True)),
    )

    should_attempt, age_s, reason = _auto_reconnect_should_attempt(
        manager, "dev", handle, 100.0
    )

    assert should_attempt is False
    assert age_s is None
    assert reason is None


def test_load_device_spec_revalidates_hard_timeout_on_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = object.__new__(Manager)
    manager._heartbeat_timeout_s = 3.0
    manager._devices = {
        "dev": SimpleNamespace(spec=SimpleNamespace(config_path=Path("device.yaml")))
    }
    invalid_spec = SimpleNamespace(
        device_id="dev",
        driver_heartbeat_hard_timeout_s=2.0,
    )
    monkeypatch.setattr(
        manager_module, "device_spec_from_yaml", lambda _path: invalid_spec
    )

    with pytest.raises(ValueError, match="driver_heartbeat_hard_timeout_s"):
        manager.load_device_spec_from_disk("dev")


def test_running_driver_without_first_heartbeat_becomes_stale() -> None:
    manager = object.__new__(Manager)
    manager._heartbeat_timeout_s = 3.0
    handle = SimpleNamespace(
        last_hb_recv_mono=None,
        driver_running_since_mono=96.0,
        driver_process_state="RUNNING",
        last_hb=None,
    )
    manager._devices = {"dev": handle}
    manager._last_liveness = {}
    manager._publish_manager_event = mock.Mock()

    manager._update_device_liveness(100.0)

    assert manager._last_liveness["dev"] == Liveness.STALE
    topic, payload = manager._publish_manager_event.call_args.args
    assert topic == "manager.liveness"
    assert payload["liveness"] == Liveness.STALE
    assert payload["age_s"] == 4.0


def test_heartbeat_age_ignores_previous_generation_heartbeat() -> None:
    handle = SimpleNamespace(
        last_hb_recv_mono=90.0,
        driver_running_since_mono=98.0,
    )
    assert Manager._device_heartbeat_age_s(handle, 100.0) == 2.0


def test_property_access_is_inside_telemetry_operation_breadcrumb(
    tmp_path: Path,
) -> None:
    driver_path = tmp_path / "property_device.py"
    driver_path.write_text(
        """
class PropertyDevice:
    def connect(self):
        return None
    def disconnect(self):
        return None
    @property
    def pressure(self):
        return 1.25
""",
        encoding="utf-8",
    )
    runner = DeviceRunner(
        device_id="dev",
        device_class_path=str(driver_path),
        device_class_name="PropertyDevice",
        device_init_kwargs={},
        registry_endpoint="tcp://127.0.0.1:5555",
        telemetry_calls=[
            TelemetryCall(method="pressure", outputs=[TelemetryOut(signal="pressure")])
        ],
    )
    events: list[str] = []
    device_cls = type(runner._device)
    device_cls.pressure = property(lambda _self: events.append("get") or 1.25)
    runner._begin_operation = lambda name: events.append(  # type: ignore[method-assign]
        f"begin:{name}"
    )
    runner._end_operation = lambda: events.append("end")  # type: ignore[method-assign]
    try:
        got = runner.read_telemetry()
    finally:
        runner.disconnect_ipc()

    assert got["pressure"]["value"] == 1.25
    assert events == ["begin:telemetry:pressure", "get", "end"]


def test_explicit_stop_cancels_pending_heartbeat_restart() -> None:
    from experiment_control._manager.process_supervision import (
        maybe_restart_device_driver,
        stop_driver,
    )

    handle = SimpleNamespace(
        process=None,
        driver_pid=None,
        driver_process_state="FAILED",
        driver_next_restart_t_mono=50.0,
        driver_restart_count=0,
        rpc_endpoint=None,
        pub_endpoint=None,
        spec=SimpleNamespace(
            driver_max_restarts=3,
            device_id="dev",
        ),
    )
    manager = SimpleNamespace(
        _devices={"dev": handle},
        _heartbeat_timeout_s=3.0,
        _last_liveness={},
        _publish_manager_event=mock.Mock(),
        _publish_driver_event=mock.Mock(),
        _close_device_rpc=mock.Mock(),
        start_driver=mock.Mock(),
    )

    stop_driver(manager, "dev")
    assert handle.driver_next_restart_t_mono is None
    assert handle.driver_process_state == "STOPPED"

    maybe_restart_device_driver(manager, "dev", handle, 100.0)
    manager.start_driver.assert_not_called()


def test_cached_capabilities_use_running_since_when_first_heartbeat_missing() -> None:
    from experiment_control._manager.device_routing import _route_command

    cached = {"version": 1, "members": []}
    handle = SimpleNamespace(
        last_hb_recv_mono=None,
        driver_running_since_mono=90.0,
        driver_process_state="RUNNING",
        capabilities=cached,
        rpc_endpoint="tcp://127.0.0.1:12345",
    )
    manager = SimpleNamespace(
        _devices={"dev": handle},
        _federation_hub=SimpleNamespace(is_mirrored_device=lambda _did: False),
        _heartbeat_timeout_s=3.0,
        _normalize_command_source=lambda **kwargs: ("test", "test"),
        _normalize_id=lambda value: None if value is None else str(value),
        _device_heartbeat_age_s=Manager._device_heartbeat_age_s,
        _driver_is_stopped=lambda _item: False,
        _apply_command_interceptors=lambda cmd, **kwargs: (True, cmd, None),
        _build_manager_command_payload=lambda **kwargs: dict(kwargs),
        _publish_manager_event=mock.Mock(),
        _call_device_rpc=mock.Mock(),
    )

    resp = _route_command(
        manager,
        {"device_id": "dev", "action": "capabilities", "params": {}},
    )

    assert resp == {"status": "OK", "result": cached}
    manager._call_device_rpc.assert_not_called()
    manager._publish_manager_event.assert_called_once()


def test_cached_capabilities_still_obey_command_interceptors() -> None:
    from experiment_control._manager.device_routing import _route_command

    interceptor = mock.Mock(return_value=(False, None, "blocked by test"))
    handle = SimpleNamespace(
        last_hb_recv_mono=90.0,
        driver_running_since_mono=80.0,
        driver_process_state="RUNNING",
        capabilities={"version": 1, "members": []},
        rpc_endpoint="tcp://127.0.0.1:12345",
    )
    manager = SimpleNamespace(
        _devices={"dev": handle},
        _federation_hub=SimpleNamespace(is_mirrored_device=lambda _did: False),
        _heartbeat_timeout_s=3.0,
        _normalize_command_source=lambda **kwargs: ("test", "test"),
        _device_heartbeat_age_s=lambda _item, _now: 10.0,
        _driver_is_stopped=lambda _item: False,
        _apply_command_interceptors=interceptor,
        _call_device_rpc=mock.Mock(),
    )

    resp = _route_command(
        manager,
        {"device_id": "dev", "action": "capabilities", "params": {}},
    )

    assert resp["ok"] is False
    interceptor.assert_called_once()
    manager._call_device_rpc.assert_not_called()


def test_failed_driver_with_late_fresh_heartbeat_is_offline() -> None:
    manager = object.__new__(Manager)
    manager._heartbeat_timeout_s = 3.0
    manager._device_rpc_timeout_ms = 1500
    manager._federation_hub = SimpleNamespace(is_mirrored_device=lambda _did: False)
    manager._telemetry_last_bundle_ts = {}
    manager._telemetry_last_recv_mono = {}
    manager._telemetry_latest = {}
    handle = SimpleNamespace(
        last_hb_recv_mono=99.5,
        driver_running_since_mono=None,
        driver_process_state="FAILED",
        last_hb=SimpleNamespace(
            driver_state="CONNECTED",
            device_state="OK",
            device_reachable=True,
            last_error=None,
        ),
        rpc_endpoint="tcp://127.0.0.1:12345",
        pub_endpoint="tcp://127.0.0.1:12346",
        driver_pid=123,
        driver_popen_pid=456,
        driver_heartbeat_pid=123,
        driver_restart_count=0,
        driver_last_exit_code=None,
        driver_last_error="heartbeat timeout",
        connect_check_last=None,
        spec=SimpleNamespace(
            auto_reconnect=SimpleNamespace(
                enabled=False,
                on_telemetry_stale_s=None,
                cooldown_s=1.0,
                max_attempts=None,
                reset_attempts_after_ok_s=1.0,
            )
        ),
        auto_reconnect_attempts=0,
        auto_reconnect_last_attempt_mono=None,
        auto_reconnect_last_attempt_wall=None,
        auto_reconnect_last_success_mono=None,
        auto_reconnect_healthy_since_mono=None,
        auto_reconnect_last_error=None,
        auto_reconnect_suppressed=False,
    )
    manager._devices = {"dev": handle}

    with mock.patch("experiment_control.manager.time.monotonic", return_value=100.0):
        status = manager._device_status_snapshot("dev")

    assert status["driver_process"]["state"] == "FAILED"
    assert status["liveness"] == Liveness.OFFLINE


def test_federated_stale_uses_owner_hard_timeout() -> None:
    from experiment_control.federation.hub import _mirrored_device_liveness

    peer_rt = SimpleNamespace(config=SimpleNamespace(event_stale_s=3.0))
    mirror = SimpleNamespace(
        last_hb_recv_mono=96.0,
        last_hb_payload={"device_reachable": True},
        last_liveness="STALE",
        last_liveness_hard_timeout_s=10.0,
    )

    liveness, age_s = _mirrored_device_liveness(peer_rt, mirror, 100.0)
    assert liveness == "STALE"
    assert age_s == 4.0

    liveness, age_s = _mirrored_device_liveness(peer_rt, mirror, 107.0)
    assert liveness == "OFFLINE"
    assert age_s == 11.0
