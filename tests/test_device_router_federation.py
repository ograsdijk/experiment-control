# ruff: noqa: E402

import queue
import sys
import unittest
from pathlib import Path

import zmq

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.device_router import (
    DeviceRouter,
    MirroredRoute,
    _MirroredDeviceWorker,
    _MirroredTask,
)


class _CaptureWorker:
    def __init__(self) -> None:
        self.tasks = []

    def submit(self, task: object) -> None:
        self.tasks.append(task)


class _CaptureManager:
    def __init__(self) -> None:
        self.calls = []

    def call(self, payload: dict, *, timeout_ms: int | None = None) -> dict:
        self.calls.append((payload, timeout_ms))
        return {"ok": True}

    def close(self) -> None:
        return None


class DeviceRouterFederationTests(unittest.TestCase):
    def test_mirrored_command_uses_dedicated_worker(self) -> None:
        router = DeviceRouter(
            external_rpc_bind="tcp://127.0.0.1:*",
            federation_mirrors=[
                {
                    "local_id": "lab2.psu",
                    "peer_id": "lab2",
                    "remote_device_id": "psu",
                    "peer_router_rpc": "tcp://10.0.0.22:6000",
                }
            ],
            process_id="device_router",
        )
        try:
            worker = _CaptureWorker()
            responses = []
            router._ensure_mirrored_worker = lambda device_id: worker  # type: ignore[method-assign]
            router._send_external_response = (  # type: ignore[method-assign]
                lambda identity, resp: responses.append((identity, resp))
            )

            router._dispatch_device_command(
                b"client-1",
                {
                    "type": "command",
                    "device_id": "lab2.psu",
                    "action": "get",
                    "params": {},
                },
            )

            self.assertEqual(responses, [])
            self.assertEqual(len(worker.tasks), 1)
            task = worker.tasks[0]
            self.assertEqual(task.route.local_id, "lab2.psu")
            self.assertEqual(task.route.remote_device_id, "psu")
        finally:
            router.close()

    def test_reexport_of_mirrored_device_is_blocked(self) -> None:
        router = DeviceRouter(
            external_rpc_bind="tcp://127.0.0.1:*",
            federation_mirrors=[
                {
                    "local_id": "lab2.psu",
                    "peer_id": "lab2",
                    "remote_device_id": "psu",
                    "peer_router_rpc": "tcp://10.0.0.22:6000",
                }
            ],
            process_id="device_router",
        )
        try:
            worker = _CaptureWorker()
            responses = []
            router._ensure_mirrored_worker = lambda device_id: worker  # type: ignore[method-assign]
            router._send_external_response = (  # type: ignore[method-assign]
                lambda identity, resp: responses.append((identity, resp))
            )

            router._dispatch_device_command(
                b"client-1",
                {
                    "type": "command",
                    "device_id": "lab2.psu",
                    "action": "get",
                    "params": {},
                    "federation": {"hop_count": 1, "origin_instance_id": "lab1"},
                },
            )

            self.assertEqual(len(worker.tasks), 0)
            self.assertEqual(len(responses), 1)
            self.assertFalse(responses[0][1]["ok"])
            self.assertEqual(
                responses[0][1]["error"]["code"], "federation_reexport_blocked"
            )
        finally:
            router.close()

    def test_mirrored_capabilities_result_updates_local_manager_cache(self) -> None:
        route = MirroredRoute(
            local_id="lab2.psu",
            peer_id="lab2",
            remote_device_id="psu",
            peer_router_rpc="tcp://10.0.0.22:6000",
            rpc_timeout_ms=1500,
            allow_device_actions=("*",),
            deny_device_actions=(),
            allow_lifecycle_ops=False,
            allow_admin_ops=False,
            origin_instance_id="lab1",
        )
        worker = _MirroredDeviceWorker(
            route=route,
            ctx=zmq.Context.instance(),
            reply_queue=queue.Queue(),
            manager_rpc="tcp://127.0.0.1:6002",
            manager_pub="tcp://127.0.0.1:6001",
            manager_timeout_ms=250,
        )
        task = _MirroredTask(
            identity=b"client-1",
            request={
                "type": "command",
                "device_id": "lab2.psu",
                "action": "capabilities",
                "params": {},
            },
            route=route,
        )
        manager = _CaptureManager()
        worker._manager = manager

        worker._maybe_cache_capabilities(
            task,
            {"ok": True, "result": {"version": 1, "members": [{"name": "get"}]}},
        )

        self.assertEqual(len(manager.calls), 1)
        payload, timeout_ms = manager.calls[0]
        self.assertEqual(payload["type"], "federation.capabilities.update")
        self.assertEqual(payload["device_id"], "lab2.psu")
        self.assertEqual(payload["capabilities"]["version"], 1)
        self.assertEqual(timeout_ms, 250)


if __name__ == "__main__":
    unittest.main()
