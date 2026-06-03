from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .contracts.manager_requests import InternalRpcEnvelope, rpc_error
from .utils.rpc_dispatch import RpcDispatchRegistry
from .utils.zmq_helpers import json_dumps, safe_json_loads

if TYPE_CHECKING:
    import zmq

    from .federation.hub import FederationHub
    from .manager_models import DeviceHandle
    from .manager_protocol import ManagerProtocol

    _MixinBase = ManagerProtocol
else:
    _MixinBase = object

Json = dict[str, Any]

# Lifecycle types that run on the manager's lifecycle thread pool
# (see Manager._dispatch_lifecycle_task). Anything in this set with a
# local, non-federated device_id is handed off to a worker; the worker
# enqueues the reply, the main loop sends it. Different devices run
# concurrently; same-device ops serialise via per-device Lock.
_LIFECYCLE_TYPES = frozenset({
    "device.connect",
    "device.disconnect",
    "device.driver.start",
    "device.driver.stop",
    "device.driver.restart",
    "device.recover",
})


def _parse_internal_payload(payload_bytes: bytes) -> tuple[InternalRpcEnvelope | None, Json | None]:
    try:
        raw = safe_json_loads(payload_bytes)
    except Exception as exc:
        return None, rpc_error(code="invalid_json", message=str(exc))
    envelope = InternalRpcEnvelope.parse(raw)
    if envelope is None:
        return None, rpc_error(code="invalid_request", message="request must be a JSON object")
    return envelope, None


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


class InternalRpcMixin(_MixinBase):
    """Mixin providing internal-RPC dispatch.

    Phase 8.2.6: migrated ``handle_internal_rpc``,
    ``route_internal_request``, and ``ensure_route_registries`` from
    module-level helpers to mixin methods. All three are kept as
    module-level trampolines because ``tests/test_dealer_request_id_correlation``
    imports / monkey-patches them.
    """

    # Owned-state attributes (concrete types declared on Manager).
    _internal_rpc: "zmq.Socket"
    _devices: dict[str, "DeviceHandle"]
    _federation_hub: "FederationHub"
    _internal_action_registry: RpcDispatchRegistry
    _internal_type_registry: RpcDispatchRegistry
    _process_route_registry: RpcDispatchRegistry
    _manager_route_registry: RpcDispatchRegistry

    def _handle_internal_rpc(self) -> None:
        identity, payload_bytes = self._internal_rpc.recv_multipart()
        envelope, parse_error = _parse_internal_payload(payload_bytes)
        if parse_error is not None:
            self._internal_rpc.send_multipart([identity, json_dumps(parse_error)])
            return
        assert envelope is not None
        req = envelope.raw

        # Lifecycle ops: hand off to the worker pool so different
        # devices can run concurrently. Reply is sent later when the
        # worker enqueues it on _lifecycle_reply_queue and the main
        # loop drains. Federated devices stay on the main thread
        # (forwarding uses sockets we haven't audited for thread safety).
        rtype = req.get("type")
        device_id = req.get("device_id")
        if (
            rtype in _LIFECYCLE_TYPES
            and isinstance(device_id, str)
            and device_id in self._devices
            and not self._federation_hub.is_mirrored_device(device_id)
        ):
            self._dispatch_lifecycle_task(identity, req, rtype, device_id)
            return

        try:
            # Call through the module-level ``route_internal_request``
            # so legacy tests that monkeypatch it (notably
            # ``tests.test_dealer_request_id_correlation``) keep working.
            # In production it's a one-line trampoline to
            # ``self._route_internal_request``.
            resp = route_internal_request(self, req)
        except LookupError as exc:
            resp = rpc_error(code="unknown_request_type", message=str(exc))
        except Exception as exc:
            resp = rpc_error(code="route_failed", message=str(exc))
        # Echo the caller's transport-level request_id (when present
        # and the handler didn't already set one) so DEALER clients
        # can correlate this synchronous reply against their outbound
        # payload. The lifecycle worker path at manager._run_lifecycle
        # does the same injection for its asynchronous replies; this
        # covers every other internal RPC route (action / type /
        # process / manager registries, device-routing list/snapshot,
        # command-interceptor register/list, manager.shutdown,
        # manager.identity, etc. — 70+ handlers that otherwise return
        # plain {"ok": ..., "result": ...} dicts).
        rid = req.get("request_id")
        if isinstance(resp, dict) and rid is not None and "request_id" not in resp:
            resp = dict(resp)
            resp["request_id"] = rid
        self._internal_rpc.send_multipart([identity, json_dumps(resp)])

    def _route_internal_request(self, req: Json) -> Json:
        self._ensure_route_registries()
        action_resp = self._dispatch_registry_request(
            self._internal_action_registry,
            route_key=req.get("action"),
            req=req,
        )
        if action_resp is not None:
            return action_resp

        rtype = req.get("type")
        type_resp = self._dispatch_registry_request(
            self._internal_type_registry,
            route_key=rtype,
            req=req,
        )
        if type_resp is not None:
            return type_resp

        device_resp = self._route_device_request(rtype, req)
        if device_resp is not None:
            return device_resp

        process_resp = self._route_process_request(rtype, req)
        if process_resp is not None:
            return process_resp

        manager_resp = self._route_manager_request(rtype, req)
        if manager_resp is not None:
            return manager_resp

        raise LookupError(f"Unknown internal request type {rtype!r}")

    def _ensure_route_registries(self) -> None:
        if not hasattr(self, "_internal_action_registry") or not isinstance(
            self._internal_action_registry, RpcDispatchRegistry
        ):
            self._internal_action_registry = self._build_internal_action_registry()
        if not hasattr(self, "_internal_type_registry") or not isinstance(
            self._internal_type_registry, RpcDispatchRegistry
        ):
            self._internal_type_registry = self._build_internal_type_registry()
        if not hasattr(self, "_process_route_registry") or not isinstance(
            self._process_route_registry, RpcDispatchRegistry
        ):
            self._process_route_registry = self._build_process_route_registry()
        if not hasattr(self, "_manager_route_registry") or not isinstance(
            self._manager_route_registry, RpcDispatchRegistry
        ):
            self._manager_route_registry = self._build_manager_route_registry()


# --- Backward-compat module-level forwarders -----------------------
# ``tests.test_dealer_request_id_correlation`` imports
# ``handle_internal_rpc`` and monkey-patches ``route_internal_request``
# to short-circuit routing while exercising request_id echoing. The
# mixin's ``_handle_internal_rpc`` calls ``route_internal_request``
# (the module-level name, not ``self._route_internal_request``) so
# the patch keeps working. Trampolines forward to the mixin in
# production. (``ensure_route_registries`` had no external callers
# nor test monkey-patches; removed in the pass-5 cleanup.)

def handle_internal_rpc(manager: Any) -> None:
    InternalRpcMixin._handle_internal_rpc(manager)


def route_internal_request(manager: Any, req: Json) -> Json:
    return InternalRpcMixin._route_internal_request(manager, req)
