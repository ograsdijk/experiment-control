# ruff: noqa: E402

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager import CommandInterceptorRoute
from experiment_control._manager.route_handlers import (
    command_interceptor_routes_snapshot,
    register_command_interceptor_routes,
    route_command_interceptor_unregister,
    unregister_command_interceptor_routes,
)
from experiment_control.processes.device_router import DeviceRouter
from experiment_control.processes.process_base import ManagedProcessBase


def _make_manager() -> SimpleNamespace:
    manager = SimpleNamespace(
        _command_interceptor_routes=[],
        _command_interceptor_order=0,
        _command_interceptor_cache={},
        _command_interceptor_cache_max=2048,
        _events=[],
        _processes={"interlock": object(), "guard": object()},
    )
    manager._publish_manager_event = lambda topic, payload: manager._events.append(
        (topic, payload)
    )
    manager._command_interceptor_routes_snapshot = (
        lambda: command_interceptor_routes_snapshot(manager)
    )
    manager._register_command_interceptor_routes = (
        lambda process_id, routes_raw, *, replace: register_command_interceptor_routes(
            manager,
            process_id,
            routes_raw,
            replace=replace,
            route_cls=CommandInterceptorRoute,
        )
    )
    manager._unregister_command_interceptor_routes = (
        lambda process_id: unregister_command_interceptor_routes(manager, process_id)
    )
    return manager


class ManagerInterceptorUnregisterTests(unittest.TestCase):
    def test_unregister_removes_only_matching_process_routes(self) -> None:
        manager = _make_manager()
        manager._register_command_interceptor_routes(
            "interlock",
            [{"device_id": "dev1", "action": "set"}],
            replace=True,
        )
        manager._register_command_interceptor_routes(
            "guard",
            [{"device_id": "dev2", "action": "set"}],
            replace=True,
        )

        resp = route_command_interceptor_unregister(
            manager,
            {"type": "manager.interceptors.unregister", "process_id": "interlock"},
        )

        self.assertTrue(resp["ok"])
        self.assertTrue(resp["result"]["removed"])
        self.assertEqual(
            manager._command_interceptor_routes_snapshot(),
            [{"process_id": "guard", "device_id": "dev2", "action": "set", "order": 2}],
        )
        self.assertEqual(manager._events[-1][0], "manager.command_interceptor.routes_unregistered")

    def test_unregister_unknown_process_is_ok_without_removing_routes(self) -> None:
        manager = _make_manager()
        manager._register_command_interceptor_routes(
            "guard",
            [{"device_id": "dev2", "action": "set"}],
            replace=True,
        )

        resp = route_command_interceptor_unregister(
            manager,
            {"type": "manager.interceptors.unregister", "process_id": "missing"},
        )

        self.assertTrue(resp["ok"])
        self.assertFalse(resp["result"]["removed"])
        self.assertEqual(len(manager._command_interceptor_routes_snapshot()), 1)

    def test_device_router_unregister_removes_only_matching_process_routes(self) -> None:
        router = DeviceRouter.__new__(DeviceRouter)
        router._routes = [
            CommandInterceptorRoute("interlock", "dev1", "set", 1),
            CommandInterceptorRoute("guard", "dev2", "set", 2),
        ]
        router._route_lock = threading.Lock()
        router._route_cache = {("dev1", "set"): [router._routes[0]]}
        router._events = []
        router._publish_manager_event = lambda topic, payload: router._events.append(
            (topic, payload)
        )

        resp = router._handle_command_interceptor(
            {"type": "manager.interceptors.unregister", "process_id": "interlock"}
        )

        self.assertTrue(resp["ok"])
        self.assertTrue(resp["result"]["removed"])
        self.assertEqual([route.process_id for route in router._routes], ["guard"])
        self.assertEqual(router._route_cache, {})
        self.assertEqual(router._events[-1][0], "manager.command_interceptor.routes_unregistered")

    def test_process_base_unregister_helper_calls_explicit_rpc(self) -> None:
        calls = []

        class _Client:
            def call(self, payload):
                calls.append(payload)
                return {"ok": True, "result": {"removed": True}}

        proc = ManagedProcessBase(
            process_id="interlock",
            heartbeat_endpoint=None,
            heartbeat_period_s=1.0,
        )
        proc._manager = _Client()

        proc._unregister_command_interceptor_routes()

        self.assertEqual(
            calls,
            [{"type": "manager.interceptors.unregister", "process_id": "interlock"}],
        )


if __name__ == "__main__":
    unittest.main()
