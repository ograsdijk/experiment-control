from __future__ import annotations

from typing import Any

from .utils.rpc_dispatch import RpcDispatchRegistry


def build_internal_action_registry(manager: Any) -> RpcDispatchRegistry:
    return RpcDispatchRegistry(
        handlers={
            "manager.telemetry.schema.list": manager._route_action_telemetry_schema_list,
        },
    )


def build_internal_type_registry(manager: Any) -> RpcDispatchRegistry:
    return RpcDispatchRegistry(
        handlers={
            "manager.devices.list": manager._route_type_list_devices,
            "manager.telemetry.snapshot": manager._route_type_telemetry_snapshot,
            "manager.telemetry.get": manager._route_type_get_telemetry,
        },
    )


def build_process_route_registry(manager: Any) -> RpcDispatchRegistry:
    return RpcDispatchRegistry(
        handlers={
            "manager.processes.list": manager._route_process_list_status,
            "manager.processes.get": manager._route_process_get,
            "manager.processes.start": manager._route_process_start,
            "manager.processes.stop": manager._route_process_stop,
            "manager.processes.restart": manager._route_process_restart,
            "manager.processes.add": manager._route_process_add,
            "manager.processes.remove": manager._route_process_remove,
            "manager.processes.rpc.advertise": manager._route_process_rpc_advertise,
            "manager.processes.rpc": manager._route_process_rpc,
            "manager.interceptors.register": manager._route_command_interceptor_register,
            "manager.interceptors.list": manager._route_command_interceptor_list,
        },
    )


def build_manager_route_registry(manager: Any) -> RpcDispatchRegistry:
    return RpcDispatchRegistry(
        handlers={
            "manager.control.shutdown": manager._route_manager_shutdown,
            "manager.info.identity": manager._route_manager_identity,
            "manager.control.cleanup_orphans": manager._route_manager_cleanup_orphans,
            "manager.logs.publish": manager._route_manager_log_publish,
            "manager.logs.tail": manager._route_manager_log_tail,
            "manager.commands.journal.status": manager._route_manager_command_journal_status,
            "manager.commands.journal.tail": manager._route_manager_command_journal_tail,
            "manager.events.publish": manager._route_manager_event_publish,
        },
    )
