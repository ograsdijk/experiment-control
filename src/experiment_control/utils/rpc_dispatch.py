from __future__ import annotations

from typing import Any, Callable, Mapping

Json = dict[str, Any]
RpcHandler = Callable[[Json], Json]


def normalize_rpc_action(raw: Any) -> str:
    return str(raw or "").strip()


class RpcDispatchRegistry:
    """Small action->handler registry with optional action aliases."""

    def __init__(
        self,
        *,
        handlers: Mapping[str, RpcHandler],
        aliases: Mapping[str, str] | None = None,
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

    def canonicalize_request(self, req: Json) -> Json:
        """Return req (or a shallow copy with req['type'] rewritten) so the
        action is in canonical form. Used by callers that want canonicalisation
        without the registry's built-in dispatch."""
        canonical = self.canonical_action(req.get("type"))
        if canonical:
            req_type = str(req.get("type", "")).strip()
            if canonical != req_type:
                req = dict(req)
                req["type"] = canonical
        return req

    def dispatch_with_canonical(self, req: Json) -> Json | None:
        """Canonicalize req['type'] via alias map then dispatch.

        If the action is aliased, the request is shallow-copied so the
        original dict the caller passed in is not mutated. Returns None if
        no handler matches (the caller decides what unknown_request response
        to return).
        """
        canonical_req = self.canonicalize_request(req)
        # `canonical_req["type"]` is already canonical, so look the
        # handler up directly instead of routing back through
        # `dispatch` → `handler_for` → `canonical_action` (no-op).
        handler = self._handlers.get(normalize_rpc_action(canonical_req.get("type")))
        if handler is None:
            return None
        return handler(canonical_req)

