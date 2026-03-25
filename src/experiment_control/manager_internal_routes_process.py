from __future__ import annotations

from typing import Any, Callable

from .manager_route_handlers import (
    publish_process_command_response as shared_publish_process_command_response,
)
from .manager_route_handlers import (
    route_command_interceptor_list as shared_route_command_interceptor_list,
)
from .manager_route_handlers import (
    route_command_interceptor_register as shared_route_command_interceptor_register,
)
from .manager_route_handlers import route_process_add as shared_route_process_add
from .manager_route_handlers import (
    route_process_control as shared_route_process_control,
)
from .manager_route_handlers import route_process_get as shared_route_process_get
from .manager_route_handlers import (
    route_process_list_status as shared_route_process_list_status,
)
from .manager_route_handlers import route_process_remove as shared_route_process_remove
from .manager_route_handlers import (
    route_process_request as shared_route_process_request,
)
from .manager_route_handlers import route_process_rpc as shared_route_process_rpc
from .manager_route_handlers import (
    route_process_rpc_advertise as shared_route_process_rpc_advertise,
)

Json = dict[str, Any]


def publish_process_command_response(
    manager: Any,
    *,
    process_id: str,
    action: str,
    params: Json,
    response: Json,
    request_id: Any,
    caller_process_id: Any,
    source_kind: str,
    source_id: str,
) -> Json:
    return shared_publish_process_command_response(
        manager,
        process_id=process_id,
        action=action,
        params=params,
        response=response,
        request_id=request_id,
        caller_process_id=caller_process_id,
        source_kind=source_kind,
        source_id=source_id,
    )


def route_process_request(manager: Any, rtype: Any, req: Json) -> Json | None:
    return shared_route_process_request(manager, rtype, req)


def route_process_list_status(manager: Any, req: Json) -> Json:
    return shared_route_process_list_status(manager, req)


def route_process_get(manager: Any, req: Json) -> Json:
    return shared_route_process_get(manager, req)


def route_process_control(
    manager: Any,
    req: Json,
    *,
    action: str,
    runner: Callable[[str], None],
) -> Json:
    return shared_route_process_control(manager, req, action=action, runner=runner)


def route_process_add(manager: Any, req: Json) -> Json:
    return shared_route_process_add(manager, req)


def route_process_remove(manager: Any, req: Json) -> Json:
    return shared_route_process_remove(manager, req)


def route_process_rpc_advertise(manager: Any, req: Json) -> Json:
    return shared_route_process_rpc_advertise(manager, req)


def route_process_rpc(
    manager: Any,
    req: Json,
    *,
    running_states: set[Any],
    starting_state: Any,
) -> Json:
    return shared_route_process_rpc(
        manager,
        req,
        running_states=running_states,
        starting_state=starting_state,
    )


def route_command_interceptor_register(manager: Any, req: Json) -> Json:
    return shared_route_command_interceptor_register(manager, req)


def route_command_interceptor_list(manager: Any, req: Json) -> Json:
    return shared_route_command_interceptor_list(manager, req)
