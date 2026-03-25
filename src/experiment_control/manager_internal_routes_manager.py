from __future__ import annotations

from typing import Any

from .manager_route_handlers import (
    route_manager_cleanup_orphans as shared_route_manager_cleanup_orphans,
)
from .manager_route_handlers import (
    route_manager_command_journal_status as shared_route_manager_command_journal_status,
)
from .manager_route_handlers import (
    route_manager_command_journal_tail as shared_route_manager_command_journal_tail,
)
from .manager_route_handlers import (
    route_manager_event_publish as shared_route_manager_event_publish,
)
from .manager_route_handlers import route_manager_identity as shared_route_manager_identity
from .manager_route_handlers import (
    route_manager_log_publish as shared_route_manager_log_publish,
)
from .manager_route_handlers import route_manager_log_tail as shared_route_manager_log_tail
from .manager_route_handlers import route_manager_request as shared_route_manager_request
from .manager_route_handlers import route_manager_shutdown as shared_route_manager_shutdown

Json = dict[str, Any]


def route_manager_request(manager: Any, rtype: Any, req: Json) -> Json | None:
    return shared_route_manager_request(manager, rtype, req)


def route_manager_shutdown(manager: Any, req: Json) -> Json:
    return shared_route_manager_shutdown(manager, req)


def route_manager_identity(manager: Any, req: Json) -> Json:
    return shared_route_manager_identity(manager, req)


def route_manager_cleanup_orphans(manager: Any, req: Json) -> Json:
    return shared_route_manager_cleanup_orphans(manager, req)


def route_manager_log_publish(manager: Any, req: Json) -> Json:
    return shared_route_manager_log_publish(manager, req)


def route_manager_log_tail(manager: Any, req: Json) -> Json:
    return shared_route_manager_log_tail(manager, req)


def route_manager_command_journal_status(manager: Any, req: Json) -> Json:
    return shared_route_manager_command_journal_status(manager, req)


def route_manager_command_journal_tail(manager: Any, req: Json) -> Json:
    return shared_route_manager_command_journal_tail(manager, req)


def route_manager_event_publish(manager: Any, req: Json) -> Json:
    return shared_route_manager_event_publish(manager, req)
