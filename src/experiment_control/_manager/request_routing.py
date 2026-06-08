from __future__ import annotations

from typing import TYPE_CHECKING

from ..utils.rpc_dispatch import RpcDispatchRegistry

if TYPE_CHECKING:
    from ..manager_protocol import ManagerProtocol

    _MixinBase = ManagerProtocol
else:
    _MixinBase = object


class RequestRoutingMixin(_MixinBase):
    """Mixin providing RpcDispatchRegistry builders.

    Phase 8.2.7: migrated ``build_internal_action_registry``,
    ``build_internal_type_registry``, ``build_process_route_registry``,
    and ``build_manager_route_registry`` from module-level helpers to
    mixin methods. Each method returns a fresh ``RpcDispatchRegistry``
    bound to the calling instance's ``_route_*`` handlers. No external
    callers; no module-level trampolines needed.
    """

    def _build_internal_action_registry(self) -> RpcDispatchRegistry:
        return RpcDispatchRegistry(
            handlers={
                "manager.telemetry.schema.list": self._route_action_telemetry_schema_list,
            },
        )

    def _build_internal_type_registry(self) -> RpcDispatchRegistry:
        return RpcDispatchRegistry(
            handlers={
                "manager.devices.list": self._route_type_list_devices,
                "manager.telemetry.snapshot": self._route_type_telemetry_snapshot,
                "manager.telemetry.get": self._route_type_get_telemetry,
            },
        )

    def _build_process_route_registry(self) -> RpcDispatchRegistry:
        return RpcDispatchRegistry(
            handlers={
                "manager.processes.list": self._route_process_list_status,
                "manager.processes.get": self._route_process_get,
                "manager.processes.start": self._route_process_start,
                "manager.processes.stop": self._route_process_stop,
                "manager.processes.restart": self._route_process_restart,
                "manager.processes.add": self._route_process_add,
                "manager.processes.remove": self._route_process_remove,
                "manager.processes.rpc.advertise": self._route_process_rpc_advertise,
                "manager.processes.rpc": self._route_process_rpc,
                "manager.interceptors.register": self._route_command_interceptor_register,
                "manager.interceptors.unregister": self._route_command_interceptor_unregister,
                "manager.interceptors.list": self._route_command_interceptor_list,
            },
        )

    def _build_manager_route_registry(self) -> RpcDispatchRegistry:
        return RpcDispatchRegistry(
            handlers={
                "manager.control.shutdown": self._route_manager_shutdown,
                "manager.info.identity": self._route_manager_identity,
                "manager.control.cleanup_orphans": self._route_manager_cleanup_orphans,
                "manager.logs.publish": self._route_manager_log_publish,
                "manager.logs.tail": self._route_manager_log_tail,
                "manager.commands.journal.status": self._route_manager_command_journal_status,
                "manager.commands.journal.tail": self._route_manager_command_journal_tail,
                "manager.events.publish": self._route_manager_event_publish,
            },
        )
