# ruff: noqa: E402
"""Tests for the round-trip clock-skew probe (cli/clock_skew_probe.py).

The subtle part is the NTP formula and its sign convention:
``skew = T_peer - (T1 + RTT/2)`` with ``+`` meaning the peer is ahead. These
tests pin that against a fake peer whose clock is offset by a known amount, plus
the unreachable-peer reporting.
"""

from __future__ import annotations

import sys
import threading
import time
import uuid
from pathlib import Path

import unittest

import zmq

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.cli.clock_skew_probe import (
    _measure_peer,
    _parse_peer_arg,
)
from experiment_control.utils.zmq_helpers import json_dumps, json_loads


class _FakePeerManager:
    """In-process ROUTER answering ``manager.info.ping`` with a clock offset.

    The reply's ``t_wall`` is this host's ``time.time()`` plus ``offset_s`` at
    reply time, simulating a peer whose wall clock leads/lags by ``offset_s``.
    Echoes request_id so ManagerClient correlation succeeds.
    """

    def __init__(
        self,
        ctx: zmq.Context,
        *,
        offset_s: float = 0.0,
        t_wall_override: object = None,
    ) -> None:
        self._ctx = ctx
        self._offset_s = offset_s
        # When set, reply with this exact t_wall value (e.g. a bool / non-finite)
        # instead of a real clock, to exercise the probe's validation.
        self._t_wall_override = t_wall_override
        self._sock = ctx.socket(zmq.ROUTER)
        self.endpoint = f"inproc://fake-peer-{uuid.uuid4().hex}"
        self._sock.bind(self.endpoint)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._thread.join(timeout=2.0)
        finally:
            try:
                self._sock.close(0)
            except Exception:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._sock.poll(50, zmq.POLLIN):
                continue
            try:
                identity, payload_raw = self._sock.recv_multipart(zmq.NOBLOCK)
            except zmq.Again:
                continue
            payload = json_loads(payload_raw)
            request_id = (
                payload.get("request_id") if isinstance(payload, dict) else None
            )
            t_wall = (
                self._t_wall_override
                if self._t_wall_override is not None
                else time.time() + self._offset_s
            )
            reply = {
                "request_id": request_id,
                "ok": True,
                "result": {"t_wall": t_wall, "instance_id": "fake-peer"},
            }
            try:
                self._sock.send_multipart([identity, json_dumps(reply)])
            except Exception:
                pass


class ClockSkewProbeTests(unittest.TestCase):
    def test_skew_estimate_matches_injected_offset(self) -> None:
        ctx = zmq.Context()
        server = _FakePeerManager(ctx, offset_s=2.0)
        try:
            summary = _measure_peer(
                ctx,
                "peerA",
                server.endpoint,
                samples=5,
                rpc_timeout_ms=1000,
                interval_s=0.0,
            )
        finally:
            server.stop()
            ctx.term()

        self.assertTrue(summary["reachable"])
        self.assertEqual(summary["samples_ok"], 5)
        self.assertEqual(summary["samples_failed"], 0)
        # Peer is 2 s ahead -> positive skew ~= 2.0 s. The residual is the
        # offset between reply-instant and the (T1 + RTT/2) midpoint, tiny for
        # inproc; allow generous slack for OS timer granularity.
        self.assertAlmostEqual(summary["skew_s"], 2.0, delta=0.1)
        self.assertGreaterEqual(summary["one_way_s"], 0.0)
        self.assertGreaterEqual(summary["rtt_s"], 0.0)

    def test_negative_offset_reports_peer_behind(self) -> None:
        ctx = zmq.Context()
        server = _FakePeerManager(ctx, offset_s=-1.5)
        try:
            summary = _measure_peer(
                ctx,
                "peerB",
                server.endpoint,
                samples=3,
                rpc_timeout_ms=1000,
                interval_s=0.0,
            )
        finally:
            server.stop()
            ctx.term()

        self.assertTrue(summary["reachable"])
        self.assertAlmostEqual(summary["skew_s"], -1.5, delta=0.1)

    def test_unreachable_peer_is_reported(self) -> None:
        ctx = zmq.Context()
        try:
            summary = _measure_peer(
                ctx,
                "dead",
                "tcp://127.0.0.1:59998",
                samples=1,
                rpc_timeout_ms=200,
                interval_s=0.0,
            )
        finally:
            ctx.term()
        self.assertFalse(summary["reachable"])
        self.assertEqual(summary["samples_ok"], 0)
        self.assertEqual(summary["samples_failed"], 1)
        self.assertNotIn("skew_s", summary)

    def test_malformed_endpoint_reported_not_raised(self) -> None:
        # A bad endpoint makes ManagerClient's zmq connect() raise in __init__;
        # the probe must report that peer unreachable, not abort the run.
        ctx = zmq.Context()
        try:
            summary = _measure_peer(
                ctx,
                "bad",
                "bogus://nope",
                samples=2,
                rpc_timeout_ms=200,
                interval_s=0.0,
            )
        finally:
            ctx.term()
        self.assertFalse(summary["reachable"])
        self.assertEqual(summary["samples_ok"], 0)
        self.assertIn("error", summary)
        self.assertNotIn("skew_s", summary)

    def test_non_numeric_t_wall_rejected(self) -> None:
        # bool is an int subclass; it must not be accepted as a timestamp.
        ctx = zmq.Context()
        server = _FakePeerManager(ctx, t_wall_override=True)
        try:
            summary = _measure_peer(
                ctx,
                "boolpeer",
                server.endpoint,
                samples=3,
                rpc_timeout_ms=1000,
                interval_s=0.0,
            )
        finally:
            server.stop()
            ctx.term()
        self.assertFalse(summary["reachable"])
        self.assertEqual(summary["samples_ok"], 0)
        self.assertEqual(summary["samples_failed"], 3)

    def test_parse_peer_arg(self) -> None:
        self.assertEqual(
            _parse_peer_arg("spb=tcp://10.0.0.5:6000"),
            ("spb", "tcp://10.0.0.5:6000"),
        )
        # Bare endpoint -> name defaults to the endpoint.
        self.assertEqual(
            _parse_peer_arg("tcp://10.0.0.5:6000"),
            ("tcp://10.0.0.5:6000", "tcp://10.0.0.5:6000"),
        )


if __name__ == "__main__":
    unittest.main()
