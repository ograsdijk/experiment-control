"""F3 regression: audit events are published off the command critical path.

The device-router workers emit a ``manager.command`` audit event after each
device/process call. That publish used to be a blocking DEALER round-trip to
the Manager, so a stalled Manager loop delayed every command reply (and the
worker's next dequeue) by up to the RPC timeout. The publish now runs on a
dedicated background thread (``_AsyncEventPublisher``) behind a bounded,
non-blocking queue. These tests pin that behaviour:

1. ``publish()`` returns immediately even while the wrapped client's
   ``publish_event`` is blocked (a stalled Manager).
2. Events are eventually delivered, in order, once the client unblocks.
3. Overflow drops the newest events and counts them, never blocking.
4. ``close()`` drains queued events and closes the client.
"""

# ruff: noqa: E402

import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.device_router import _AsyncEventPublisher


class _FakeManagerClient:
    """Records published events; can be made to block like a stalled Manager."""

    def __init__(self, gate: threading.Event | None = None) -> None:
        self._gate = gate
        self.events: list[tuple[str, dict]] = []
        self.closed = False

    def publish_event(self, *, topic, payload, include_process_id, include_ts):
        if self._gate is not None:
            # Emulate a blocked Manager RPC until the test releases the gate.
            self._gate.wait(timeout=5.0)
        self.events.append((topic, payload))

    def close(self) -> None:
        self.closed = True


class _RaisingManagerClient:
    """publish_event always raises, like an unreachable Manager that errors."""

    def __init__(self) -> None:
        self.closed = False

    def publish_event(self, **_kwargs) -> None:
        raise RuntimeError("manager unreachable")

    def close(self) -> None:
        self.closed = True


def _drain_publisher(pub, deadline_s: float = 2.0) -> None:
    """Close the publisher and wait for its background thread to finish."""
    pub.close(timeout_s=deadline_s)


class AsyncEventPublisherTests(unittest.TestCase):
    def test_publish_does_not_block_when_manager_stalled(self) -> None:
        gate = threading.Event()  # never set -> client.publish_event blocks
        client = _FakeManagerClient(gate=gate)
        pub = _AsyncEventPublisher(
            client_factory=lambda: client, name="audit-test", maxsize=64
        )
        try:
            # First event is picked up by the worker thread and blocks inside
            # publish_event; the rest queue up. None of these calls may block.
            start = time.monotonic()
            for i in range(10):
                pub.publish("manager.command", {"i": i})
            elapsed = time.monotonic() - start
            self.assertLess(
                elapsed,
                0.5,
                "publish() blocked on a stalled Manager (F3 regression)",
            )
            # Nothing delivered while the client is gated.
            self.assertEqual(client.events, [])
        finally:
            gate.set()
            pub.close()

    def test_events_delivered_in_order_after_unblock(self) -> None:
        gate = threading.Event()
        client = _FakeManagerClient(gate=gate)
        pub = _AsyncEventPublisher(
            client_factory=lambda: client, name="audit-test", maxsize=64
        )
        for i in range(5):
            pub.publish("manager.command", {"i": i})
        gate.set()
        pub.close()  # drains queue, then closes client
        self.assertEqual([p["i"] for _t, p in client.events], [0, 1, 2, 3, 4])
        self.assertTrue(client.closed)

    def test_overflow_drops_are_counted_and_never_block(self) -> None:
        gate = threading.Event()  # keep the drain thread blocked so the queue fills
        client = _FakeManagerClient(gate=gate)
        pub = _AsyncEventPublisher(
            client_factory=lambda: client, name="audit-test", maxsize=4
        )
        try:
            # One event is in-flight (blocked in publish_event); the queue holds
            # up to maxsize more. Everything beyond that must be dropped, counted,
            # and must not block.
            start = time.monotonic()
            for i in range(100):
                pub.publish("manager.command", {"i": i})
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, 0.5)
            self.assertGreater(pub.dropped, 0)
        finally:
            gate.set()
            pub.close()

    def test_close_is_bounded_when_manager_unreachable(self) -> None:
        gate = threading.Event()  # never set: client stays blocked
        client = _FakeManagerClient(gate=gate)
        pub = _AsyncEventPublisher(
            client_factory=lambda: client, name="audit-test", maxsize=64
        )
        pub.publish("manager.command", {"i": 0})
        start = time.monotonic()
        pub.close(timeout_s=0.5)
        elapsed = time.monotonic() - start
        # close() must not hang on an unreachable Manager (daemon thread is
        # abandoned at process exit).
        self.assertLess(elapsed, 2.0)
        gate.set()

    def test_client_build_failure_is_counted_not_silent(self) -> None:
        # A factory that always raises must NOT permanently and silently
        # disable audit: every discarded event is counted and client_error
        # is surfaced (retried after a cooldown, not disabled forever).
        def _boom():
            raise RuntimeError("cannot build client")

        pub = _AsyncEventPublisher(
            client_factory=_boom, name="audit-test", maxsize=64
        )
        for i in range(5):
            pub.publish("manager.command", {"i": i})
        _drain_publisher(pub)
        self.assertTrue(pub.client_error)
        self.assertEqual(pub.dropped, 5)  # all counted, none silently lost

    def test_publish_error_is_counted(self) -> None:
        client = _RaisingManagerClient()
        pub = _AsyncEventPublisher(
            client_factory=lambda: client, name="audit-test", maxsize=64
        )
        for i in range(3):
            pub.publish("manager.command", {"i": i})
        _drain_publisher(pub)
        self.assertEqual(pub.publish_errors, 3)
        self.assertTrue(client.closed)

    def test_publish_stamps_capture_ts_when_absent(self) -> None:
        client = _FakeManagerClient()
        pub = _AsyncEventPublisher(
            client_factory=lambda: client, name="audit-test", maxsize=64
        )
        pub.publish("manager.command_interceptor.error", {"error": "x"})
        _drain_publisher(pub)
        self.assertEqual(len(client.events), 1)
        _topic, payload = client.events[0]
        self.assertIn("ts", payload)
        self.assertIn("t_wall", payload["ts"])
        self.assertIn("t_mono", payload["ts"])

    def test_publish_preserves_caller_supplied_ts(self) -> None:
        client = _FakeManagerClient()
        pub = _AsyncEventPublisher(
            client_factory=lambda: client, name="audit-test", maxsize=64
        )
        ts = {"t_wall": 123.0, "t_mono": 45.0}
        pub.publish("manager.command", {"i": 0, "ts": ts})
        _drain_publisher(pub)
        _topic, payload = client.events[0]
        self.assertEqual(payload["ts"], ts)


if __name__ == "__main__":
    unittest.main()
