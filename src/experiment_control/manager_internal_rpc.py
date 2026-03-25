from __future__ import annotations

from typing import Any

from .contracts.manager_requests import InternalRpcEnvelope, rpc_error
from .utils.rpc_dispatch import RpcDispatchRegistry
from .utils.zmq_helpers import json_dumps, safe_json_loads

Json = dict[str, Any]


def _parse_internal_payload(payload_bytes: bytes) -> tuple[InternalRpcEnvelope | None, Json | None]:
    try:
        raw = safe_json_loads(payload_bytes)
    except Exception as exc:
        return None, rpc_error(code="invalid_json", message=str(exc))
    envelope = InternalRpcEnvelope.parse(raw)
    if envelope is None:
        return None, rpc_error(code="invalid_request", message="request must be a JSON object")
    return envelope, None


def handle_internal_rpc(manager: Any) -> None:
    identity, payload_bytes = manager._internal_rpc.recv_multipart()
    envelope, parse_error = _parse_internal_payload(payload_bytes)
    if parse_error is not None:
        manager._internal_rpc.send_multipart([identity, json_dumps(parse_error)])
        return
    assert envelope is not None
    try:
        resp = route_internal_request(manager, envelope.raw)
    except LookupError as exc:
        resp = rpc_error(code="unknown_request_type", message=str(exc))
    except Exception as exc:
        resp = rpc_error(code="route_failed", message=str(exc))
    manager._internal_rpc.send_multipart([identity, json_dumps(resp)])


def route_internal_request(manager: Any, req: Json) -> Json:
    manager._ensure_route_registries()
    action_resp = manager._dispatch_registry_request(
        manager._internal_action_registry,
        route_key=req.get("action"),
        req=req,
    )
    if action_resp is not None:
        return action_resp

    rtype = req.get("type")
    type_resp = manager._dispatch_registry_request(
        manager._internal_type_registry,
        route_key=rtype,
        req=req,
    )
    if type_resp is not None:
        return type_resp

    device_resp = manager._route_device_request(rtype, req)
    if device_resp is not None:
        return device_resp

    process_resp = manager._route_process_request(rtype, req)
    if process_resp is not None:
        return process_resp

    manager_resp = manager._route_manager_request(rtype, req)
    if manager_resp is not None:
        return manager_resp

    raise LookupError(f"Unknown internal request type {rtype!r}")


def dispatch_registry_request(
    registry: RpcDispatchRegistry,
    *,
    route_key: Any,
    req: Json,
) -> Json | None:
    key_text = str(route_key or "").strip()
    if not key_text:
        return None
    lookup_req: Json = req
    if req.get("type") != key_text:
        lookup_req = dict(req)
        lookup_req["type"] = key_text
    return registry.dispatch(lookup_req)


def ensure_route_registries(manager: Any) -> None:
    if not isinstance(getattr(manager, "_internal_action_registry", None), RpcDispatchRegistry):
        manager._internal_action_registry = manager._build_internal_action_registry()
    if not isinstance(getattr(manager, "_internal_type_registry", None), RpcDispatchRegistry):
        manager._internal_type_registry = manager._build_internal_type_registry()
    if not isinstance(getattr(manager, "_process_route_registry", None), RpcDispatchRegistry):
        manager._process_route_registry = manager._build_process_route_registry()
    if not isinstance(getattr(manager, "_manager_route_registry", None), RpcDispatchRegistry):
        manager._manager_route_registry = manager._build_manager_route_registry()
