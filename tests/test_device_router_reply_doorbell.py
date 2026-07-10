"""F2 regression: worker replies wake the router poll loop immediately.

The router main loop is ``poll_and_drain(poller, 50, ...)`` then
``_drain_replies()``. Worker threads deposit completed replies on a plain
``queue.Queue`` that the ZMQ poller cannot see, so a lone reply used to sit in
the queue until the 50 ms poll expired before being sent — a ~50 ms floor on
every command in the sequential (single-client) case.

The fix adds an inproc PUSH->PULL doorbell: after a worker enqueues a reply it
PUSHes an empty frame; the router registers the PULL end in its poller and
drains it, so the loop wakes the instant a reply is ready. These tests pin the
mechanism:

1. Enqueuing a reply makes the doorbell PULL readable (the poller wakes).
2. The doorbell is a pure signal — the reply itself still rides ``_reply_queue``.
3. Many rings coalesce: one drain clears the PULL, so the loop never spins.
4. With no doorbell endpoint the reply still enqueues (graceful fallback).
"""

# ruff: noqa: E402

import queue
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import zmq

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes import device_router as device_router_module
from experiment_control.processes.device_router import (
    DeviceRouter,
    _BaseWorker,
    _DeviceTask,
    _DeviceWorker,
    _ReplyItem,
)
from experiment_control.utils.zmq_helpers import json_dumps, safe_json_loads


def _make_worker(ctx, endpoint):
    return _BaseWorker(
        name="doorbell-test",
        ctx=ctx,
        reply_queue=queue.Queue(),
        queue_max=64,
        doorbell_endpoint=endpoint,
    )


class ReplyDoorbellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = zmq.Context()
        self.endpoint = "inproc://doorbell-test"
        self.pull = self.ctx.socket(zmq.PULL)
        self.pull.setsockopt(zmq.LINGER, 0)
        self.pull.bind(self.endpoint)

    def tearDown(self) -> None:
        self.pull.close(0)
        self.ctx.term()

    def _enqueue(self, worker) -> None:
        worker._enqueue_reply(
            identity=b"client",
            response={"ok": True},
            inflight_reserved=False,
            request_id="r1",
        )

    def test_enqueue_reply_wakes_poller_promptly(self) -> None:
        worker = _make_worker(self.ctx, self.endpoint)
        try:
            poller = zmq.Poller()
            poller.register(self.pull, zmq.POLLIN)
            # Before any reply the poller must time out (nothing to wake it).
            self.assertEqual(dict(poller.poll(50)), {})
            self._enqueue(worker)
            # After a reply the doorbell frame makes the PULL readable well
            # inside the old 50 ms poll floor — poll returns essentially at
            # once rather than waiting out its 1 s timeout.
            start = time.monotonic()
            events = dict(poller.poll(1000))
            elapsed = time.monotonic() - start
            self.assertEqual(events.get(self.pull), zmq.POLLIN)
            self.assertLess(elapsed, 0.5, "doorbell did not wake the poll loop (F2)")
        finally:
            worker._close_doorbell()

    def test_reply_rides_the_queue_not_the_doorbell(self) -> None:
        worker = _make_worker(self.ctx, self.endpoint)
        try:
            self._enqueue(worker)
            # The doorbell frame is empty: it is only a signal.
            frame = self.pull.recv(zmq.NOBLOCK)
            self.assertEqual(frame, b"")
            # The actual reply payload is on the reply queue.
            item = worker._reply_queue.get_nowait()
            self.assertIsInstance(item, _ReplyItem)
            self.assertEqual(item.response, {"ok": True})
        finally:
            worker._close_doorbell()

    def test_many_rings_coalesce_and_drain_fully(self) -> None:
        worker = _make_worker(self.ctx, self.endpoint)
        try:
            for _ in range(25):
                self._enqueue(worker)
            # Drain like the router does; it must reach Again (no perpetual
            # readiness => the poll loop cannot busy-spin).
            drained = 0
            while True:
                try:
                    self.pull.recv(zmq.NOBLOCK)
                    drained += 1
                except zmq.Again:
                    break
            self.assertGreater(drained, 0)
            poller = zmq.Poller()
            poller.register(self.pull, zmq.POLLIN)
            self.assertEqual(dict(poller.poll(0)), {})
        finally:
            worker._close_doorbell()

    def test_no_doorbell_endpoint_still_enqueues(self) -> None:
        worker = _make_worker(self.ctx, None)
        # Must not raise and must not build a socket.
        self._enqueue(worker)
        self.assertIsNone(worker._doorbell)
        item = worker._reply_queue.get_nowait()
        self.assertIsInstance(item, _ReplyItem)

    def test_router_poll_loop_wakes_and_drains_worker_doorbell(self) -> None:
        external_endpoint = "inproc://router-doorbell-wiring-test"
        router = DeviceRouter(
            external_rpc_bind=external_endpoint,
            manager_rpc="inproc://unused-manager-rpc",
            manager_pub="inproc://unused-manager-pub",
            process_id=None,
            heartbeat_endpoint=None,
            ctx=self.ctx,
        )
        router_errors: list[BaseException] = []
        doorbell_drained = threading.Event()
        original_drain_doorbell = router._drain_doorbell

        def tracked_drain_doorbell() -> None:
            doorbell_drained.set()
            original_drain_doorbell()

        router._drain_doorbell = tracked_drain_doorbell  # type: ignore[method-assign]

        def delayed_worker_loop(worker: _DeviceWorker) -> None:
            task = worker._queue.get(timeout=1.0)
            if not isinstance(task, _DeviceTask):
                raise AssertionError(f"expected _DeviceTask, got {type(task)!r}")
            # Give the router time to drain an empty reply queue and re-enter
            # its poll. The reply must then wake that poll through the actual
            # worker -> PUSH -> PULL -> handler wiring.
            time.sleep(0.02)
            worker._enqueue_reply(
                identity=task.identity,
                response={"ok": True, "result": "doorbell"},
                inflight_reserved=task.inflight_reserved,
                request_id=task.request_id,
            )

        real_poll_and_drain = device_router_module.poll_and_drain

        def long_idle_poll(poller, _timeout_ms, *, handlers=None):  # type: ignore[no-untyped-def]
            return real_poll_and_drain(poller, 500, handlers=handlers)

        def run_router() -> None:
            try:
                router.run()
            except BaseException as exc:  # pragma: no cover - asserted below
                router_errors.append(exc)

        runner = threading.Thread(target=run_router, daemon=True)
        client = self.ctx.socket(zmq.DEALER)
        client.setsockopt(zmq.LINGER, 0)
        try:
            with (
                mock.patch.object(
                    device_router_module, "poll_and_drain", side_effect=long_idle_poll
                ),
                mock.patch.object(_DeviceWorker, "_run_loop", delayed_worker_loop),
            ):
                runner.start()
                deadline = time.monotonic() + 2.0
                while router._reply_doorbell is None and time.monotonic() < deadline:
                    if not runner.is_alive():
                        break
                    time.sleep(0.005)
                self.assertIsNotNone(router._reply_doorbell, router_errors)

                client.connect(external_endpoint)
                request = {
                    "type": "command",
                    "device_id": "dev-1",
                    "action": "status",
                    "params": {},
                    "request_id": "router-doorbell-r1",
                }
                start = time.monotonic()
                client.send(json_dumps(request))
                self.assertTrue(
                    client.poll(300, zmq.POLLIN),
                    "router reply waited for the stretched 500 ms idle poll",
                )
                response = safe_json_loads(client.recv())
                elapsed = time.monotonic() - start

                self.assertIsInstance(response, dict)
                self.assertEqual(response.get("result"), "doorbell")
                self.assertEqual(response.get("request_id"), "router-doorbell-r1")
                self.assertLess(elapsed, 0.3)
                self.assertTrue(
                    doorbell_drained.wait(timeout=0.1),
                    "router did not dispatch its doorbell drain handler",
                )
        finally:
            client.close(0)
            router._stop_evt.set()
            runner.join(timeout=2.0)
            self.assertFalse(runner.is_alive(), "router thread did not stop")
            self.assertEqual(router_errors, [])
            for worker in router._device_workers.values():
                worker.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
