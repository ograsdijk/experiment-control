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
    _ReplyItem,
    _device_rpc_exception_error,
)


class _CaptureWorker:
    def __init__(self) -> None:
        self.tasks = []

    def submit(self, task: object) -> bool:
        self.tasks.append(task)
        return True


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
                lambda identity, resp, *, request_id=None: responses.append((identity, resp))
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

    def test_local_command_task_carries_source_fields(self) -> None:
        router = DeviceRouter(
            external_rpc_bind="tcp://127.0.0.1:*",
            process_id="device_router",
        )
        try:
            worker = _CaptureWorker()
            responses = []
            router._ensure_device_worker = lambda device_id: worker  # type: ignore[method-assign]
            router._send_external_response = (  # type: ignore[method-assign]
                lambda identity, resp, *, request_id=None: responses.append((identity, resp))
            )
            router._device_endpoints["dummy"] = "tcp://127.0.0.1:7001"
            router._device_states["dummy"] = "STOPPING"

            router._dispatch_device_command(
                b"client-1",
                {
                    "type": "command",
                    "device_id": "dummy",
                    "action": "set_frequency_hz",
                    "params": {"value": 10.0},
                    "request_id": "req-123",
                    "source_kind": "webui",
                    "source_id": "fastapi",
                },
            )

            self.assertEqual(responses, [])
            self.assertEqual(len(worker.tasks), 1)
            task = worker.tasks[0]
            self.assertEqual(task.request_id, "req-123")
            self.assertEqual(task.source_kind, "webui")
            self.assertEqual(task.source_id, "fastapi")
            self.assertEqual(task.device_state, "STOPPING")
        finally:
            router.close()

    def test_device_rpc_exception_timeout_maps_to_structured_error(self) -> None:
        err = _device_rpc_exception_error(
            zmq.Again(),
            timeout_ms=1500,
            device_id="trace1",
            action="capabilities",
        )
        self.assertEqual(err.get("code"), "device_rpc_timeout")
        self.assertEqual(err.get("device_id"), "trace1")
        self.assertEqual(err.get("action"), "capabilities")
        self.assertEqual(err.get("timeout_ms"), 1500)
        self.assertTrue(bool(err.get("transient")))
        self.assertTrue(bool(err.get("retryable")))

    def test_process_rpc_task_carries_action_and_source_fields(self) -> None:
        router = DeviceRouter(
            external_rpc_bind="tcp://127.0.0.1:*",
            process_id="device_router",
        )
        try:
            worker = _CaptureWorker()
            responses = []
            router._ensure_process_worker = lambda process_id: worker  # type: ignore[method-assign]
            router._send_external_response = (  # type: ignore[method-assign]
                lambda identity, resp, *, request_id=None: responses.append((identity, resp))
            )
            router._process_endpoints["sequencer"] = "tcp://127.0.0.1:9901"

            router._dispatch_process_rpc(
                b"client-1",
                {
                    "type": "manager.processes.rpc",
                    "process_id": "sequencer",
                    "request_id": "req-outer",
                    "source_kind": "webui",
                    "source_id": "fastapi",
                    "request": {
                        "type": "sequencer.start",
                        "params": {"sequence_id": "main"},
                        "request_id": "req-inner",
                    },
                },
            )

            self.assertEqual(responses, [])
            self.assertEqual(len(worker.tasks), 1)
            task = worker.tasks[0]
            self.assertEqual(task.process_id, "sequencer")
            self.assertEqual(task.action, "sequencer.start")
            self.assertEqual(task.params, {"sequence_id": "main"})
            self.assertEqual(task.request_id, "req-outer")
            self.assertEqual(task.source_kind, "webui")
            self.assertEqual(task.source_id, "fastapi")
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
                lambda identity, resp, *, request_id=None: responses.append((identity, resp))
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
            queue_max=8,
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

    def test_queue_full_returns_router_busy_for_device_command(self) -> None:
        router = DeviceRouter(
            external_rpc_bind="tcp://127.0.0.1:*",
            process_id="device_router",
            inflight_max=32,
        )
        try:
            class _RejectWorker:
                @staticmethod
                def submit(task: object) -> bool:
                    del task
                    return False

                @staticmethod
                def queue_depth() -> int:
                    return 32

                @staticmethod
                def queue_max() -> int:
                    return 32

                @staticmethod
                def is_alive() -> bool:
                    return True

            responses = []
            router._ensure_device_worker = lambda _device_id: _RejectWorker()  # type: ignore[method-assign]
            router._send_external_response = (  # type: ignore[method-assign]
                lambda identity, resp, *, request_id=None: responses.append((identity, resp))
            )
            router._device_endpoints["dummy"] = "tcp://127.0.0.1:7001"

            router._dispatch_device_command(
                b"client-1",
                {
                    "type": "command",
                    "device_id": "dummy",
                    "action": "get",
                    "params": {},
                },
            )

            self.assertEqual(len(responses), 1)
            resp = responses[0][1]
            self.assertFalse(bool(resp.get("ok")))
            self.assertEqual(resp.get("error", {}).get("code"), "router_busy")
            self.assertEqual(router._inflight_count, 0)  # noqa: SLF001
        finally:
            router.close()

    def test_drain_replies_releases_inflight(self) -> None:
        router = DeviceRouter(
            external_rpc_bind="tcp://127.0.0.1:*",
            process_id="device_router",
        )
        try:
            sent = []

            class _SocketStub:
                @staticmethod
                def send_multipart(parts):
                    sent.append(parts)

            router._external_rpc = _SocketStub()  # type: ignore[attr-defined]
            router._inflight_count = 1  # noqa: SLF001
            router._reply_queue.put_nowait(  # noqa: SLF001
                _ReplyItem(
                    identity=b"client-1",
                    response={"ok": True},
                    inflight_reserved=True,
                )
            )
            router._drain_replies()  # noqa: SLF001
            self.assertEqual(len(sent), 1)
            self.assertEqual(router._inflight_count, 0)  # noqa: SLF001
        finally:
            router.close()


if __name__ == "__main__":
    unittest.main()

