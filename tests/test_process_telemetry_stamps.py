# ruff: noqa: E402
"""Process telemetry carries a producer seq (ManagedProcessBase) and the
manager's receive clock (``ts.t_wall_recv``), like device telemetry, so the
HDF writer persists real values instead of -1 / NaN."""

import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import zmq

from experiment_control._manager.pubsub import publish_manager_event
from experiment_control._manager.route_handlers import route_manager_event_publish
from experiment_control.manager_client import ManagerClient
from experiment_control.processes.process_base import ManagedProcessBase


class _RecordingManager:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def advertise_process_telemetry_schema(self, **_kw: Any) -> None:
        return None

    def publish_event(self, *, topic: str, payload: dict[str, Any]) -> None:
        self.events.append({"topic": topic, "payload": payload})


class _FailingManager(_RecordingManager):
    def publish_event(self, *, topic: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("manager unreachable")


class ProcessBaseTelemetrySeqTests(unittest.TestCase):
    def _make(self, manager: Any) -> ManagedProcessBase:
        proc = ManagedProcessBase(process_id="p1", heartbeat_endpoint=None)
        proc._manager = manager  # noqa: SLF001
        return proc

    def test_seq_increments_per_bundle(self) -> None:
        mgr = _RecordingManager()
        proc = self._make(mgr)
        self.assertTrue(proc.publish_telemetry({"x": 1.0}))
        self.assertTrue(proc.publish_telemetry({"x": 2.0}))
        self.assertEqual([e["payload"]["seq"] for e in mgr.events], [1, 2])

    def test_failed_publish_still_consumes_seq(self) -> None:
        # A gap in the recorded seq must mark the dropped bundle.
        proc = self._make(_FailingManager())
        self.assertFalse(proc.publish_telemetry({"x": 1.0}))
        mgr = _RecordingManager()
        proc._manager = mgr  # noqa: SLF001
        self.assertTrue(proc.publish_telemetry({"x": 2.0}))
        self.assertEqual(mgr.events[0]["payload"]["seq"], 2)

    def test_stub_without_init_still_publishes_seq(self) -> None:
        proc = ManagedProcessBase.__new__(ManagedProcessBase)
        proc._manager = _RecordingManager()  # noqa: SLF001
        proc._process_id = "p1"  # noqa: SLF001
        proc._process_telemetry_schema_advertised = True  # noqa: SLF001
        self.assertTrue(proc.publish_telemetry({"x": 1.0}))
        self.assertEqual(proc._manager.events[0]["payload"]["seq"], 1)  # noqa: SLF001

    def test_concurrent_publishes_get_unique_seqs(self) -> None:
        mgr = _RecordingManager()
        proc = self._make(mgr)
        n_threads, per_thread = 8, 200
        barrier = threading.Barrier(n_threads)

        def _worker() -> None:
            barrier.wait()
            for _ in range(per_thread):
                proc.publish_telemetry({"x": 1.0})

        threads = [threading.Thread(target=_worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        seqs = sorted(e["payload"]["seq"] for e in mgr.events)
        self.assertEqual(seqs, list(range(1, n_threads * per_thread + 1)))

    def test_seq_survives_real_manager_client_publish_event(self) -> None:
        # Guard against ManagerClient.publish_event ever rebuilding the
        # payload and dropping seq: only its socket-level call() is faked.
        client = ManagerClient(
            ctx=zmq.Context.instance(),
            manager_rpc="tcp://127.0.0.1:65581",
            manager_pub="tcp://127.0.0.1:65582",
            rpc_timeout_ms=100,
            process_id="p1",
            subscribe_telemetry=False,
        )
        sent: list[dict[str, Any]] = []
        try:
            with (
                patch.object(client, "call", side_effect=lambda req, **_k: sent.append(req)),
                patch.object(client, "advertise_process_telemetry_schema"),
            ):
                proc = self._make(client)
                self.assertTrue(proc.publish_telemetry({"x": 1.0}))
        finally:
            client.close()
        req = sent[-1]
        self.assertEqual(req["type"], "manager.events.publish")
        self.assertEqual(req["topic"], "manager.process_telemetry_update")
        self.assertEqual(req["payload"]["seq"], 1)
        self.assertEqual(req["payload"]["process_id"], "p1")
        self.assertIn("t_wall", req["payload"]["ts"])


def _main_thread_manager() -> tuple[SimpleNamespace, list[tuple[str, dict[str, Any]]]]:
    """Stub that runs PubSubMixin._publish_manager_event's main-thread path
    for real and captures what reaches subscribers via an event hook."""
    published: list[tuple[str, dict[str, Any]]] = []
    mgr = SimpleNamespace(
        _external_pub=SimpleNamespace(send_multipart=lambda _frames: None),
        _event_hooks=[lambda t, p: published.append((t, p))],
        _append_command_journal_entry=lambda _p: None,
        _maybe_publish_log_event=lambda _t, _p: None,
        _maybe_emit_manager_log_sink=lambda _t, _p: None,
        _normalize_topic=lambda t: t.strip(),
        _emit_log_from_payload=lambda *_a, **_k: None,
    )
    mgr._publish_manager_event = lambda t, p: publish_manager_event(mgr, t, p)
    return mgr, published


class ManagerProcessTelemetryRecvStampTests(unittest.TestCase):
    def test_process_events_publish_gets_recv_clock(self) -> None:
        mgr, published = _main_thread_manager()
        before = time.time()
        resp = route_manager_event_publish(
            mgr,
            {
                "type": "manager.events.publish",
                "topic": "manager.process_telemetry_update",
                "payload": {
                    "process_id": "p1",
                    "seq": 4,
                    "signals": {},
                    "ts": {"t_wall": 10.0, "t_mono": 2.0},
                },
            },
        )
        after = time.time()
        self.assertTrue(resp["ok"])
        topic, payload = published[0]
        self.assertEqual(topic, "manager.process_telemetry_update")
        ts = payload["ts"]
        self.assertGreaterEqual(ts["t_wall_recv"], before)
        self.assertLessEqual(ts["t_wall_recv"], after)
        self.assertIn("t_mono_recv", ts)
        self.assertEqual(ts["t_wall"], 10.0)
        self.assertEqual(payload["seq"], 4)

    def test_relayed_bundle_recv_clock_is_overwritten(self) -> None:
        # A federated bundle arrives carrying the owner manager's stamp; the
        # persisted t_wall_recv must be THIS host's.
        mgr, published = _main_thread_manager()
        before = time.time()
        mgr._publish_manager_event(
            "manager.process_telemetry_update",
            {
                "process_id": "spb",
                "is_remote": True,
                "signals": {},
                "ts": {"t_wall": 50.0, "t_mono": 1.0, "t_wall_recv": 51.0},
            },
        )
        ts = published[0][1]["ts"]
        self.assertGreaterEqual(ts["t_wall_recv"], before)
        self.assertEqual(ts["t_wall"], 50.0)

    def test_missing_ts_still_stamped(self) -> None:
        mgr, published = _main_thread_manager()
        mgr._publish_manager_event(
            "manager.process_telemetry_update", {"process_id": "p1", "signals": {}}
        )
        self.assertIn("t_wall_recv", published[0][1]["ts"])

    def test_caller_payload_not_mutated(self) -> None:
        mgr, _published = _main_thread_manager()
        ts = {"t_wall": 1.0, "t_mono": 1.0}
        payload = {"process_id": "p1", "signals": {}, "ts": ts}
        mgr._publish_manager_event("manager.process_telemetry_update", payload)
        self.assertNotIn("t_wall_recv", ts)
        self.assertIs(payload["ts"], ts)

    def test_other_topics_untouched(self) -> None:
        mgr, published = _main_thread_manager()
        mgr._publish_manager_event(
            "manager.some_event", {"ts": {"t_wall": 1.0, "t_mono": 1.0}}
        )
        self.assertNotIn("t_wall_recv", published[0][1]["ts"])

    def test_stamped_before_worker_thread_queueing(self) -> None:
        import queue

        mgr = SimpleNamespace(
            _main_thread_id=threading.get_ident() + 999,  # act as a worker
            _lifecycle_event_queue=queue.Queue(maxsize=4),
            _lifecycle_event_dropped=0,
            _lifecycle_event_dropped_lock=threading.Lock(),
        )
        publish_manager_event(
            mgr, "manager.process_telemetry_update", {"process_id": "p1", "signals": {}}
        )
        _topic, payload = mgr._lifecycle_event_queue.get_nowait()
        self.assertIn("t_wall_recv", payload["ts"])


if __name__ == "__main__":
    unittest.main()
