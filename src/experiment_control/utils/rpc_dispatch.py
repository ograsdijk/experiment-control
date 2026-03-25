from __future__ import annotations

from typing import Any, Callable

Json = dict[str, Any]
RpcHandler = Callable[[Json], Json]


def normalize_rpc_action(raw: Any) -> str:
    return str(raw or "").strip()


class RpcDispatchRegistry:
    """Small action->handler registry with optional action aliases."""

    def __init__(
        self,
        *,
        handlers: dict[str, RpcHandler],
        aliases: dict[str, str] | None = None,
    ) -> None:
        self._handlers: dict[str, RpcHandler] = {}
        for action, handler in handlers.items():
            action_key = normalize_rpc_action(action)
            if not action_key:
                continue
            self._handlers[action_key] = handler

        self._aliases: dict[str, str] = {}
        for alias, target in (aliases or {}).items():
            alias_key = normalize_rpc_action(alias)
            target_key = normalize_rpc_action(target)
            if not alias_key or not target_key:
                continue
            if target_key not in self._handlers:
                continue
            self._aliases[alias_key] = target_key

    def canonical_action(self, raw_action: Any) -> str:
        action = normalize_rpc_action(raw_action)
        if not action:
            return ""
        return self._aliases.get(action, action)

    def handler_for(self, raw_action: Any) -> RpcHandler | None:
        action = self.canonical_action(raw_action)
        if not action:
            return None
        return self._handlers.get(action)

    def dispatch(self, req: Json) -> Json | None:
        handler = self.handler_for(req.get("type"))
        if handler is None:
            return None
        return handler(req)

