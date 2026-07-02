# ruff: noqa: E402

"""Pipelined-lifecycle test: concurrent device.connect ops should run in
parallel across distinct devices via the manager's lifecycle thread
pool, not serialise on the main loop.

Mirrors the shape of tests/test_fastapi_gateway.py::
test_router_rpc_client_pipelines_concurrent_requests but exercises the
manager-side parallelism that PR #37 left out.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager import (
    ConnectCheckSpec,
    DeviceHandle,
    DeviceSpec,
    Manager,
)


class _RunningProcess:
    @staticmethod
    def poll() -> None:
        return None


class _FederationStub:
    @staticmethod
    def forward_device_request(_req: dict[str, object]) -> None:
        return None

    @staticmethod
    def is_mirrored_device(_device_id: str) -> bool:
        return False


def _make_handle(device_id: str) -> DeviceHandle:
    spec = DeviceSpec(
        device_id=device_id,
        device_class_path="dummy.py",
        device_class_name="DummyDriver",
        device_init_kwargs={},
        telemetry_calls=[],
        stream_calls=[],
        run_meta_calls=[],
        connect_check=ConnectCheckSpec(),
    )
    handle = DeviceHandle(spec=spec, process=_RunningProcess())
    handle.rpc_endpoint = f"inproc://test-{device_id}"
    return handle


def _build_minimal_manager(device_ids: list[str], rpc_delay_s: float) -> Manager:
    mgr = object.__new__(Manager)
    mgr._devices = {did: _make_handle(did) for did in device_ids}  # type: ignore[attr-defined]
    mgr._federation_hub = _FederationStub()  # type: ignore[attr-defined]
    mgr._publish_manager_event = mock.Mock()  # type: ignore[attr-defined]

    # Each device RPC sleeps to simulate a real driver round-trip.
    # connect_device does TWO RPCs (connect_device + identity), so the
    # per-device serial cost is 2 * rpc_delay_s.
    def _call_device_rpc(*, device_id, action, params, **_):  # type: ignore[no-untyped-def]
        del device_id, action, params
        time.sleep(rpc_delay_s)
        return {"status": "OK"}

    mgr._call_device_rpc = _call_device_rpc  # type: ignore[attr-defined]

    # Lifecycle parallelism fields normally set in Manager.__init__:
    mgr._main_thread_id = threading.get_ident()  # type: ignore[attr-defined]
    mgr._lifecycle_executor = ThreadPoolExecutor(  # type: ignore[attr-defined]
        max_workers=32, thread_name_prefix="test-lifecycle"
    )
    mgr._lifecycle_device_locks = {}  # type: ignore[attr-defined]
    mgr._lifecycle_reply_queue = queue.Queue()  # type: ignore[attr-defined]
    mgr._lifecycle_event_queue = queue.Queue()  # type: ignore[attr-defined]
    return mgr


class ManagerLifecycleParallelTests(unittest.TestCase):
    def test_concurrent_connect_runs_in_parallel(self) -> None:
        """N concurrent device.connect ops on distinct devices should
        complete in roughly rpc_delay (one driver RPC each, with
        connect_check disabled), not N * rpc_delay (serial baseline).
        """
        N = 10
        DELAY_S = 0.1  # 100 ms per RPC; per-device cost = 100 ms (connect_check disabled)
        device_ids = [f"dev_{i}" for i in range(N)]
        mgr = _build_minimal_manager(device_ids, rpc_delay_s=DELAY_S)
        try:
            t0 = time.monotonic()
            for i, did in enumerate(device_ids):
                req = {
                    "type": "device.connect",
                    "device_id": did,
                    "request_id": f"req-{i}",
                }
                mgr._dispatch_lifecycle_task(  # type: ignore[attr-defined]
                    identity=f"ident-{i}".encode("utf-8"),
                    req=req,
                    rtype="device.connect",
                    device_id=did,
                )
            replies: list[tuple[bytes, dict]] = []
            deadline = time.monotonic() + 5.0
            while len(replies) < N and time.monotonic() < deadline:
                try:
                    identity, resp = mgr._lifecycle_reply_queue.get(timeout=0.05)  # type: ignore[attr-defined]
                except queue.Empty:
                    continue
                replies.append((identity, resp))
            elapsed_s = time.monotonic() - t0
        finally:
            mgr._lifecycle_executor.shutdown(wait=True, cancel_futures=True)  # type: ignore[attr-defined]

        self.assertEqual(len(replies), N, msg=f"only got {len(replies)} replies")
        identities = {ident for ident, _ in replies}
        self.assertEqual(len(identities), N, msg="duplicate identities")
        for _, resp in replies:
            self.assertTrue(isinstance(resp, dict))
            self.assertIn("request_id", resp, msg="request_id not echoed")

        # Serial baseline = N * DELAY_S = 1.0 s.
        # Pipelined = ~DELAY_S (each device runs its RPC on its own
        # worker, all devices run in parallel up to pool size).
        budget_s = 3 * DELAY_S  # generous margin for thread scheduling
        self.assertLess(
            elapsed_s,
            budget_s,
            msg=(
                f"parallel connect for N={N} took {elapsed_s:.3f}s; "
                f"expected < {budget_s:.3f}s (serial baseline "
                f"{N * DELAY_S:.3f}s)"
            ),
        )

    def test_same_device_serialises(self) -> None:
        """Two concurrent connect ops on the SAME device should
        serialise via the per-device Lock — total time ≈ 2 *
        per-device cost, not 1 * per-device cost.
        """
        DELAY_S = 0.1  # per-device cost = 100 ms (one RPC, connect_check off)
        mgr = _build_minimal_manager(["dev_a"], rpc_delay_s=DELAY_S)
        try:
            t0 = time.monotonic()
            for i in range(2):
                req = {
                    "type": "device.connect",
                    "device_id": "dev_a",
                    "request_id": f"req-{i}",
                }
                mgr._dispatch_lifecycle_task(  # type: ignore[attr-defined]
                    identity=f"ident-{i}".encode("utf-8"),
                    req=req,
                    rtype="device.connect",
                    device_id="dev_a",
                )
            replies: list[tuple[bytes, dict]] = []
            deadline = time.monotonic() + 5.0
            while len(replies) < 2 and time.monotonic() < deadline:
                try:
                    identity, resp = mgr._lifecycle_reply_queue.get(timeout=0.05)  # type: ignore[attr-defined]
                except queue.Empty:
                    continue
                replies.append((identity, resp))
            elapsed_s = time.monotonic() - t0
        finally:
            mgr._lifecycle_executor.shutdown(wait=True, cancel_futures=True)  # type: ignore[attr-defined]

        self.assertEqual(len(replies), 2)
        # Two serialised connect ops × 1 RPC each × DELAY_S = 0.2 s
        # minimum. Use a lower bound to confirm serialisation occurred.
        # (If the lock were broken, both would run concurrently and
        # the total would be ~0.1 s.)
        min_expected_s = 1.8 * DELAY_S  # 180 ms; tolerates scheduling jitter
        self.assertGreaterEqual(
            elapsed_s,
            min_expected_s,
            msg=(
                f"same-device ops finished in {elapsed_s:.3f}s; "
                f"expected >= {min_expected_s:.3f}s if per-device Lock "
                f"is serialising correctly"
            ),
        )


class ManagerAutoConnectOffLoopTests(unittest.TestCase):
    """Auto-connect-on-register must run on the lifecycle executor, not block
    the poll loop, so a slow/absent device can't stall the manager at startup."""

    def test_dispatch_auto_connect_does_not_block_and_publishes(self) -> None:
        mgr = _build_minimal_manager(["dev_a"], rpc_delay_s=0.3)
        try:
            t0 = time.monotonic()
            mgr._dispatch_auto_connect("dev_a")  # type: ignore[attr-defined]
            dispatch_elapsed = time.monotonic() - t0
            # Returns immediately — does NOT block the caller on the 0.3s connect.
            self.assertLess(
                dispatch_elapsed,
                0.1,
                msg=f"auto-connect dispatch blocked for {dispatch_elapsed:.3f}s",
            )
            # The connect eventually runs on the worker and publishes an event.
            deadline = time.monotonic() + 5.0
            while (
                mgr._publish_manager_event.call_count == 0  # type: ignore[attr-defined]
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            topics = [
                c.args[0]
                for c in mgr._publish_manager_event.call_args_list  # type: ignore[attr-defined]
                if c.args
            ]
            self.assertIn("manager.connect_device_sent", topics)
        finally:
            mgr._lifecycle_executor.shutdown(wait=True, cancel_futures=True)  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
