from __future__ import annotations

import time
from types import SimpleNamespace
from unittest import mock

from experiment_control._manager.device_routing import _route_command


class _FederationStub:
    @staticmethod
    def is_mirrored_device(device_id: str) -> bool:
        del device_id
        return False


def test_stale_driver_capabilities_are_served_from_manager_cache() -> None:
    cached = {
        "version": 1,
        "members": [{"name": "read_pressure", "kind": "method"}],
    }
    handle = SimpleNamespace(
        last_hb_recv_mono=time.monotonic() - 4.0,
        driver_process_state="RUNNING",
        capabilities=cached,
        rpc_endpoint="tcp://127.0.0.1:12345",
    )
    manager = SimpleNamespace(
        _devices={"dev": handle},
        _federation_hub=_FederationStub(),
        _heartbeat_timeout_s=3.0,
        _normalize_command_source=lambda **kwargs: ("test", "test"),
        _normalize_id=lambda value: None if value is None else str(value),
        _device_heartbeat_age_s=lambda item, now: now - item.last_hb_recv_mono,
        _driver_is_stopped=lambda item: False,
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
    assert resp["result"] is not cached
    manager._call_device_rpc.assert_not_called()
    manager._publish_manager_event.assert_called_once()
    assert manager._publish_manager_event.call_args.args[0] == "manager.command"
