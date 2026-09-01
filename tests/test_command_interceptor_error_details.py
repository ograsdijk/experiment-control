from types import SimpleNamespace

from experiment_control._manager.device_routing import _route_command


def test_command_interceptor_rejection_preserves_structured_error_details() -> None:
    interceptor_error = {
        "kind": "command_interceptor",
        "code": "INTERCEPTOR_REJECTED",
        "message": "frequency outside calibrated range",
        "process_id": "laser_lock_freq_nltl_power",
        "device_id": "synthhd",
        "action": "set_frequency",
        "details": {
            "code": "FREQ_OUT_OF_RANGE",
            "message": "outside calibration",
            "details": {"max_hz": 2.0},
        },
    }
    manager = SimpleNamespace(
        _devices={"synthhd": SimpleNamespace(rpc_endpoint="tcp://driver")},
        _normalize_command_source=lambda **_kwargs: ("operator", "test"),
        _driver_is_stopped=lambda _handle: False,
        _apply_command_interceptors=lambda _cmd, **_kwargs: (
            False,
            None,
            interceptor_error,
        ),
    )

    response = _route_command(
        manager,
        {
            "device_id": "synthhd",
            "action": "set_frequency",
            "params": {"channel": 0, "freq_hz": 1.0},
        },
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "command_interceptor_rejected"
    assert response["error"]["message"] == interceptor_error["message"]
    assert response["error"]["details"] == interceptor_error
