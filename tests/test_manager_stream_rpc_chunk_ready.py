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

from experiment_control._manager.driver_pub import ingest_chunk_ready
from experiment_control._manager.rpc_calls import RpcCallsMixin


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


def _stream_descriptor(seq: int = 1) -> dict[str, Any]:
    return {
        "device_id": "dev-1",
        "stream": "timestamps",
        "shm_name": "ec-test-shm",
        "seq": seq,
        "dtype": "float64",
        "shape": [1],
    }


class ManagerStreamRpcChunkReadyTests(unittest.TestCase):
    def test_stream_rpc_result_publishes_chunk_ready_without_driver_pub(self) -> None:
        mgr = _ManagerStub()
        resp = {"ok": True, "result": _stream_descriptor(seq=7)}

        with patch(
            "experiment_control._manager.rpc_calls._blocking_call_with_pump",
            return_value=resp,
        ):
            returned = mgr._call_device_rpc(
                device_id="dev-1",
                action="stream__timestamps",
                params={},
            )

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

    def test_invalid_stream_rpc_descriptor_fails_clearly(self) -> None:
        mgr = _ManagerStub()
        resp = {
            "ok": True,
            "result": {"device_id": "dev-1", "stream": "timestamps", "seq": 1},
        }

        with patch(
            "experiment_control._manager.rpc_calls._blocking_call_with_pump",
            return_value=resp,
        ):
            with self.assertRaises(ValueError):
                mgr._call_device_rpc(
                    device_id="dev-1",
                    action="stream__timestamps",
                    params={},
                )

        chunk_errors = [
            payload for topic, payload in mgr.events if topic == "manager.chunk_error"
        ]
        self.assertEqual(len(chunk_errors), 1)
        self.assertIn("stream RPC result chunk ingest failed", chunk_errors[0]["error"])
        self.assertNotIn("dev-1", mgr._latest_chunk_desc)
        self.assertFalse(mgr.closed_rpc)

    def test_non_stream_rpc_does_not_publish_chunk_ready(self) -> None:
        mgr = _ManagerStub()
        resp = {"ok": True, "result": _stream_descriptor(seq=3)}

        with patch(
            "experiment_control._manager.rpc_calls._blocking_call_with_pump",
            return_value=resp,
        ):
            mgr._call_device_rpc(
                device_id="dev-1",
                action="read_status",
                params={},
            )

        self.assertFalse(
            [payload for topic, payload in mgr.events if topic == "manager.chunk_ready"]
        )

    def test_duplicate_direct_and_driver_pub_chunk_ready_is_suppressed(self) -> None:
        mgr = _ManagerStub()
        resp = {"ok": True, "result": _stream_descriptor(seq=11)}

        with patch(
            "experiment_control._manager.rpc_calls._blocking_call_with_pump",
            return_value=resp,
        ):
            mgr._call_device_rpc(
                device_id="dev-1",
                action="stream__timestamps",
                params={},
            )
        ingest_chunk_ready(mgr, {"descriptor": _stream_descriptor(seq=11)})

        chunk_ready = [
            payload for topic, payload in mgr.events if topic == "manager.chunk_ready"
        ]
        self.assertEqual(len(chunk_ready), 1)


if __name__ == "__main__":
    unittest.main()
