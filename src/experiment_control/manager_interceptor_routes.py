from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

Json = dict[str, Any]
RouteKey = tuple[str, str]


@dataclass
class InterceptorRouteState:
    routes: list[Any] = field(default_factory=list)
    chain_cache: OrderedDict[RouteKey, list[Any]] = field(default_factory=OrderedDict)
    next_order: int = 0
    generation: int = 0
    max_cache: int = 2048

    def snapshot(self) -> list[Json]:
        return [
            {
                "process_id": route.process_id,
                "device_id": route.device_id,
                "action": route.action,
                "order": route.order,
            }
            for route in sorted(self.routes, key=lambda route: route.order)
        ]

    def invalidate(self) -> None:
        self.generation += 1
        self.chain_cache.clear()

    def drop_process(self, process_id: str) -> bool:
        before = len(self.routes)
        self.routes = [route for route in self.routes if route.process_id != process_id]
        changed = len(self.routes) != before
        if changed:
            self.invalidate()
        return changed

    def register(
        self,
        *,
        process_id: str,
        routes_raw: Any,
        replace: bool,
        route_cls: Any,
    ) -> list[Json]:
        if not isinstance(routes_raw, list):
            raise TypeError("routes must be a list")
        if replace:
            self.drop_process(process_id)

        seen: set[tuple[str, str, str]] = set()
        added: list[Json] = []
        for route in routes_raw:
            if not isinstance(route, dict):
                raise TypeError("route must be an object")
            device_id = str(route.get("device_id", "")).strip()
            action = str(route.get("action", "")).strip()
            if not device_id or not action:
                raise ValueError("route.device_id and route.action are required")
            key = (process_id, device_id, action)
            if key in seen:
                continue
            seen.add(key)
            self.next_order += 1
            entry = route_cls(
                process_id=process_id,
                device_id=device_id,
                action=action,
                order=self.next_order,
            )
            self.routes.append(entry)
            added.append(
                {
                    "process_id": process_id,
                    "device_id": device_id,
                    "action": action,
                    "order": entry.order,
                }
            )
        self.invalidate()
        return added

    def chain(
        self,
        *,
        device_id: str,
        action: str,
        match_route: Any,
    ) -> list[Any]:
        key = (device_id, action)
        cached = self.chain_cache.get(key)
        if cached is not None:
            # Touch as LRU.
            self.chain_cache.pop(key, None)
            self.chain_cache[key] = cached
            return list(cached)

        matches = [
            route
            for route in self.routes
            if match_route(route, device_id, action)
        ]
        matches.sort(key=lambda route: route.order)
        ordered: list[Any] = []
        seen: set[str] = set()
        for route in matches:
            if route.process_id in seen:
                continue
            seen.add(route.process_id)
            ordered.append(route)

        self.chain_cache[key] = list(ordered)
        max_items = max(32, int(self.max_cache))
        while len(self.chain_cache) > max_items:
            oldest = next(iter(self.chain_cache))
            self.chain_cache.pop(oldest, None)
        return ordered


def _legacy_state(manager: Any) -> InterceptorRouteState:
    routes = list(getattr(manager, "_command_interceptor_routes", []))
    next_order = int(getattr(manager, "_command_interceptor_order", 0))
    max_cache = int(getattr(manager, "_command_interceptor_cache_max", 2048))
    chain_cache = OrderedDict()
    for key, value in dict(getattr(manager, "_command_interceptor_cache", {})).items():
        if isinstance(key, tuple) and len(key) == 2:
            chain_cache[(str(key[0]), str(key[1]))] = list(value)
    return InterceptorRouteState(
        routes=routes,
        chain_cache=chain_cache,
        next_order=next_order,
        max_cache=max_cache,
    )


def ensure_interceptor_route_state(manager: Any) -> InterceptorRouteState:
    state = getattr(manager, "_interceptor_route_state", None)
    if isinstance(state, InterceptorRouteState):
        return state
    state = _legacy_state(manager)
    manager._interceptor_route_state = state
    return state


def sync_legacy_fields(manager: Any, state: InterceptorRouteState) -> None:
    # Keep backward compatibility with existing code paths that still read legacy fields.
    manager._command_interceptor_routes = list(state.routes)
    manager._command_interceptor_order = int(state.next_order)
    manager._command_interceptor_cache = dict(state.chain_cache)


def snapshot(manager: Any) -> list[Json]:
    state = ensure_interceptor_route_state(manager)
    sync_legacy_fields(manager, state)
    return state.snapshot()


def invalidate(manager: Any) -> None:
    state = ensure_interceptor_route_state(manager)
    state.invalidate()
    sync_legacy_fields(manager, state)


def drop_process(manager: Any, process_id: str) -> bool:
    state = ensure_interceptor_route_state(manager)
    changed = state.drop_process(process_id)
    sync_legacy_fields(manager, state)
    return changed


def register(
    manager: Any,
    *,
    process_id: str,
    routes_raw: Any,
    replace: bool,
    route_cls: Any,
) -> list[Json]:
    state = ensure_interceptor_route_state(manager)
    state.max_cache = int(getattr(manager, "_command_interceptor_cache_max", state.max_cache))
    added = state.register(
        process_id=process_id,
        routes_raw=routes_raw,
        replace=replace,
        route_cls=route_cls,
    )
    sync_legacy_fields(manager, state)
    return added


def chain(
    manager: Any,
    *,
    device_id: str,
    action: str,
    match_route: Any,
) -> list[Any]:
    state = ensure_interceptor_route_state(manager)
    state.max_cache = int(getattr(manager, "_command_interceptor_cache_max", state.max_cache))
    ordered = state.chain(
        device_id=device_id,
        action=action,
        match_route=match_route,
    )
    sync_legacy_fields(manager, state)
    return ordered
