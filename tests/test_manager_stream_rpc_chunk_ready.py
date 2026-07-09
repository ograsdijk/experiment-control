# ruff: noqa: E402

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import numpy as np

from experiment_control._driver.stream_wrappers import build_stream_wrapper
from experiment_control._manager.driver_pub import ingest_chunk_ready
from experiment_control._manager.rpc_calls import RpcCallsMixin
from experiment_control.types import StreamCall, StreamOut


class _FakeSocket:
    def setsockopt(self, _option: Any, _value: Any) -> None:
        return None


class _ManagerStub(RpcCallsMixin):
    def __init__(self) -> None:
        self._device_rpc_timeout_ms = 1000
        self._rpc_seq = 0
        self._devices = {
            "dev-1": SimpleNamespace(
                rpc_endpoint="inproc://dev-1",
                rpc_sock=None,
                rpc_lock=threading.RLock(),
                rpc_fail_count=0,
                rpc_last_fail_t_mono=None,
            )
        }
        self._latest_chunk_desc: dict[str, dict[str, dict[str, Any]]] = {}
        self._chunk_device_order: dict[str, None] = {}
        self._chunk_cache_max_devices = 16
        self._chunk_cache_max_streams_per_device = 16
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.closed_rpc = False
        self._manager_chunk_ready_ingest_error_total = 0

    def _ensure_device_req_socket(self, _handle: Any) -> _FakeSocket:
        return _FakeSocket()

    def _pump_manager_subscriptions(self) -> None:
        return None

    def _close_device_rpc(self, _handle: Any) -> None:
        self.closed_rpc = True

    def _ingest_chunk_ready(self, msg: dict[str, Any]) -> None:
        ingest_chunk_ready(self, msg)

    def _publish_manager_event(self, topic: str, payload: dict[str, Any]) -> None:
        self.events.append((topic, payload))

    @staticmethod
    def _normalize_id(raw: Any) -> str | None:
        if raw is None:
            return None
        text = str(raw).strip()
        return text or None

    @staticmethod
    def _normalize_command_source(
        *,
        source_kind: Any,
        source_id: Any,
        caller_process_id: Any,
    ) -> tuple[str, str | None]:
        del caller_process_id
        return str(source_kind or "manager"), None if source_id is None else str(source_id)

    @staticmethod
    def _safe_json(value: Any, *, max_len: int = 4000) -> str:
        del max_len
        return str(value)


def _stream_descriptor(seq: int = 1, *, stream: str = "timestamps") -> dict[str, Any]:
    return {
        "device_id": "dev-1",
        "stream": stream,
        "shm_name": "ec-test-shm",
        "seq": seq,
        "dtype": "float64",
        "shape": [1],
    }


def _stream_result(*seqs: int) -> list[dict[str, Any]]:
    """The real device-side stream wrapper shape: a *list* of descriptors,
    one per published shot (see ``_driver.stream_wrappers``)."""
    return [_stream_descriptor(seq) for seq in (seqs or (1,))]


class _StubRunner:
    """Minimal ``_StreamPublisher`` for driving the real stream wrapper so
    tests exercise the genuine (list) result shape rather than a fixture."""

    def __init__(self) -> None:
        self._device = SimpleNamespace(
            read_timestamps=lambda: np.array([1.0], dtype=np.float64)
        )
        self.published: list[dict[str, Any]] = []

    def publish_stream(self, stream: str, arr: np.ndarray) -> dict[str, Any]:
        del arr
        seq = len(self.published) + 1
        desc = {
            "device_id": "dev-1",
            "stream": stream,
            "stream_kind": "frame",
            "shm_name": "ec-test-shm",
            "layout_version": 1,
            "seq": seq,
            "t0_mono_ns": 0,
            "t0_wall_ns": 0,
        }
        self.published.append(desc)
        return desc


class ManagerStreamRpcChunkReadyTests(unittest.TestCase):
    def _call(self, mgr: _ManagerStub, resp: dict[str, Any], *, action: str):
        with patch(
            "experiment_control._manager.rpc_calls._blocking_call_with_pump",
            return_value=resp,
        ):
            return mgr._call_device_rpc(device_id="dev-1", action=action, params={})

    def test_stream_rpc_result_list_publishes_chunk_ready(self) -> None:
        # The device-side wrapper returns a LIST of descriptors, not a bare
        # dict. This is the shape that failed before the fix.
        mgr = _ManagerStub()
        resp = {"ok": True, "result": _stream_result(7)}

        returned = self._call(mgr, resp, action="stream__timestamps")

        self.assertIs(returned, resp)
        chunk_ready = [
            payload for topic, payload in mgr.events if topic == "manager.chunk_ready"
        ]
        self.assertEqual(len(chunk_ready), 1)
        self.assertEqual(chunk_ready[0]["device_id"], "dev-1")
        self.assertEqual(chunk_ready[0]["stream"], "timestamps")
        self.assertEqual(chunk_ready[0]["seq"], 7)
        self.assertEqual(
            mgr._latest_chunk_desc["dev-1"]["timestamps"]["shm_name"],
            "ec-test-shm",
        )

    def test_stream_rpc_result_from_real_wrapper_shape(self) -> None:
        # Regression guard: build the result via the ACTUAL stream wrapper
        # so the manager-side ingest can never drift from the shape the
        # driver really produces (a list, per shot).
        runner = _StubRunner()
        wrapper = build_stream_wrapper(
            runner=runner,
            stream_call=StreamCall(
                method="read_timestamps",
                outputs=[StreamOut(stream="timestamps", dtype="float64", shape=(1,))],
            ),
        )
        result = wrapper()
        self.assertIsInstance(result, list)  # documents/pins the contract

        mgr = _ManagerStub()
        resp = {"ok": True, "result": result}
        returned = self._call(mgr, resp, action="stream__timestamps")

        self.assertIs(returned, resp)
        chunk_ready = [
            payload for topic, payload in mgr.events if topic == "manager.chunk_ready"
        ]
        self.assertEqual(len(chunk_ready), 1)
        self.assertEqual(chunk_ready[0]["stream"], "timestamps")

    def test_batched_stream_result_publishes_each_shot(self) -> None:
        mgr = _ManagerStub()
        resp = {"ok": True, "result": _stream_result(4, 5, 6)}

        self._call(mgr, resp, action="stream__timestamps")

        chunk_ready = [
            payload for topic, payload in mgr.events if topic == "manager.chunk_ready"
        ]
        self.assertEqual([p["seq"] for p in chunk_ready], [4, 5, 6])

    def test_multi_output_result_publishes_each_stream(self) -> None:
        # Multi-output wrapper shape: list of {stream_name: descriptor}.
        mgr = _ManagerStub()
        resp = {
            "ok": True,
            "result": [
                {
                    "a": _stream_descriptor(1, stream="a"),
                    "b": _stream_descriptor(1, stream="b"),
                }
            ],
        }

        self._call(mgr, resp, action="stream__pair")

        published = {
            payload["stream"]
            for topic, payload in mgr.events
            if topic == "manager.chunk_ready"
        }
        self.assertEqual(published, {"a", "b"})

    def test_malformed_stream_result_is_non_fatal(self) -> None:
        # A descriptor missing shm_name is unusable, but the device call
        # already succeeded and published over PUB — so we must NOT raise
        # or fail the RPC; just record a chunk_error and return the reply.
        mgr = _ManagerStub()
        resp = {
            "ok": True,
            "result": [{"device_id": "dev-1", "stream": "timestamps", "seq": 1}],
        }

        returned = self._call(mgr, resp, action="stream__timestamps")

        self.assertIs(returned, resp)
        chunk_errors = [
            payload for topic, payload in mgr.events if topic == "manager.chunk_error"
        ]
        self.assertEqual(len(chunk_errors), 1)
        self.assertNotIn("dev-1", mgr._latest_chunk_desc)
        self.assertFalse(mgr.closed_rpc)
        self.assertEqual(mgr._manager_chunk_ready_ingest_error_total, 1)

    def test_non_stream_rpc_does_not_publish_chunk_ready(self) -> None:
        mgr = _ManagerStub()
        resp = {"ok": True, "result": _stream_result(3)}

        self._call(mgr, resp, action="read_status")

        self.assertFalse(
            [payload for topic, payload in mgr.events if topic == "manager.chunk_ready"]
        )

    def test_duplicate_direct_and_driver_pub_chunk_ready_is_suppressed(self) -> None:
        mgr = _ManagerStub()
        resp = {"ok": True, "result": _stream_result(11)}

        self._call(mgr, resp, action="stream__timestamps")
        ingest_chunk_ready(mgr, {"descriptor": _stream_descriptor(seq=11)})

        chunk_ready = [
            payload for topic, payload in mgr.events if topic == "manager.chunk_ready"
        ]
        self.assertEqual(len(chunk_ready), 1)


if __name__ == "__main__":
    unittest.main()
