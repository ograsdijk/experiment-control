from __future__ import annotations

from typing import Any

from .manager_device_routing import route_device_request as shared_route_device_request

Json = dict[str, Any]


def route_action_telemetry_schema_list(manager: Any, req: Json) -> Json:
    del req
    return {"ok": True, "result": manager._telemetry_schema_list()}


def route_type_list_devices(manager: Any, req: Json) -> Json:
    del req
    return {"ok": True, "devices": manager._list_devices_snapshot()}


def route_type_telemetry_snapshot(manager: Any, req: Json) -> Json:
    del req
    return {"ok": True, "result": manager._telemetry_snapshot()}


def route_type_get_telemetry(manager: Any, req: Json) -> Json:
    device_id = str(req["device_id"])
    return {
        "ok": True,
        "telemetry": manager._get_device_telemetry_snapshot(device_id),
    }


def route_device_request(manager: Any, rtype: Any, req: Json) -> Json | None:
    return shared_route_device_request(manager, rtype, req)
