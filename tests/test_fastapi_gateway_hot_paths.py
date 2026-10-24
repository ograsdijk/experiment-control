# ruff: noqa: E402

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.fastapi.gateway import HubMessage, TelemetryHub, _json_values
from experiment_control.utils.zmq_helpers import json_dumps, json_loads

fastapi_app_module = importlib.import_module("experiment_control.fastapi.app")


class _WsCapture:
    def __init__(self) -> None:
        self.text: list[str] = []
        self.binary: list[bytes] = []

    async def send_text(self, payload: str) -> None:
        self.text.append(payload)

    async def send_bytes(self, payload: bytes) -> None:
        self.binary.append(payload)


def _workspace_state(
    *, binary: bool, trace_max_points: int | None
) -> object:
    return fastapi_app_module._WorkspaceTraceWsState(
        trace_decimator="stride",
        trace_max_points=trace_max_points,
        trace_interval_s=0.0,
        rolling_window=1,
        trace_average_mode="rolling",
        binary_transport=binary,
    )


class JsonHotPathTests(unittest.TestCase):
    def test_json_loads_falls_back_for_non_spec_literals_and_big_ints(self) -> None:
        self.assertEqual(json_loads(b'{"a": [1, 2.5, "x"]}'), {"a": [1, 2.5, "x"]})
        loaded = json_loads(b'{"a": NaN, "b": Infinity}')
        self.assertTrue(np.isnan(loaded["a"]))
        self.assertEqual(loaded["b"], float("inf"))
        self.assertEqual(json_loads(b'{"n": 1180591620717411303424}')["n"], 2**70)

    def test_json_values_encodes_like_tolist(self) -> None:
        for arr in (
            np.asarray([1.5, np.nan, -np.inf, 2.0], dtype=np.float64),
            np.arange(6, dtype=np.int16).reshape(2, 3),
            np.asarray([True, False]),
        ):
            expected = [None if isinstance(x, float) and not np.isfinite(x) else x
                        for x in arr.reshape(-1).tolist()]
            encoded = json.loads(json_dumps({"v": _json_values(arr.reshape(-1))}))
            self.assertEqual(encoded["v"], expected)

    def test_json_dumps_fallback_still_encodes_numpy_values(self) -> None:
        # A >64-bit int makes orjson reject the payload; the stdlib fallback
        # must still encode the ndarray sibling instead of raising.
        payload = {
            "values": np.asarray([1.0, 2.5], dtype=np.float32),
            "context_fields": {"big": 2**70, "n": np.int64(3)},
        }
        decoded = json.loads(json_dumps(payload))
        self.assertEqual(decoded["values"], [1.0, 2.5])
        self.assertEqual(decoded["context_fields"], {"big": 2**70, "n": 3})

    def test_json_values_falls_back_for_non_native_byte_order(self) -> None:
        arr = np.asarray([1.0, np.nan], dtype=">f8")
        values = _json_values(arr)
        self.assertEqual(values, [1.0, None])

    def test_decoded_hub_shares_text_and_message(self) -> None:
        async def run() -> HubMessage:
            hub = TelemetryHub("tcp://127.0.0.1:1", topics=("any",), keep_decoded=True)
            hub._loop = asyncio.get_running_loop()  # noqa: SLF001
            q = hub.subscribe()
            item = HubMessage('{"topic":"any","payload":{"k":1}}', {"topic": "any", "payload": {"k": 1}})
            hub._fanout(item)  # noqa: SLF001
            return await asyncio.wait_for(q.get(), timeout=1.0)

        self.assertIsInstance(asyncio.run(run()), HubMessage)

    def test_workspace_filter_reads_decoded_hub_message(self) -> None:
        msg = {
            "topic": "manager.stream_analysis.output",
            "payload": {"workspace_id": "ws1", "kind": "scalar", "value": 1.0},
        }
        filtered = fastapi_app_module._workspace_filter_stream_message(
            msg=HubMessage(json.dumps(msg), msg),
            workspace="ws1",
            allowed_output_kinds=None,
        )
        self.assertIsNotNone(filtered)
        assert filtered is not None
        self.assertEqual(filtered[0], "manager.stream_analysis.output")
        self.assertIs(filtered[1], msg["payload"])


class WorkspaceTraceDeferralTests(unittest.TestCase):
    def test_finalize_decimates_and_marks(self) -> None:
        state = _workspace_state(binary=False, trace_max_points=10)
        msg = {
            "topic": "manager.stream_analysis.output",
            "payload": {
                "output_id": "o",
                "kind": "trace",
                "_raw_trace": np.arange(100, dtype=np.float64),
                "_mark_decimated": True,
            },
        }
        out = fastapi_app_module._workspace_finalize_trace_msg(state, msg)["payload"]
        self.assertNotIn("_raw_trace", out)
        self.assertNotIn("_mark_decimated", out)
        self.assertTrue(out["decimated"])
        self.assertEqual(out["point_count"], len(out["value"]))
        self.assertLessEqual(out["point_count"], 10)

    def test_finalize_small_trace_is_not_decimated(self) -> None:
        state = _workspace_state(binary=True, trace_max_points=10)
        msg = {"topic": "t", "payload": {"_raw_trace": np.asarray([1.0, 2.0])}}
        out = fastapi_app_module._workspace_finalize_trace_msg(state, msg)["payload"]
        self.assertNotIn("decimated", out)
        self.assertEqual(out["point_count"], 2)
        np.testing.assert_array_equal(out["_binary_values"], [1.0, 2.0])

    def test_throttled_trace_is_only_decimated_when_sent(self) -> None:
        state = _workspace_state(binary=False, trace_max_points=10)
        state.trace_interval_s = 60.0
        ws = _WsCapture()
        calls: list[int] = []
        original = fastapi_app_module._decimate_trace_values

        def counting(values, *, mode, max_points):
            calls.append(1)
            return original(values, mode=mode, max_points=max_points)

        fastapi_app_module._decimate_trace_values = counting
        try:
            async def run() -> None:
                for _ in range(5):
                    payload = {"output_id": "o", "kind": "trace", "value": list(range(100))}
                    await fastapi_app_module._workspace_handle_trace_output_message(
                        ws=ws,
                        state=state,
                        msg={"topic": "manager.stream_analysis.output", "payload": payload},
                        payload=payload,
                        msg_workspace="ws1",
                    )

            asyncio.run(run())
        finally:
            fastapi_app_module._decimate_trace_values = original

        # First trace sends immediately; the rest are superseded pending
        # messages that have not been sent (or decimated) yet.
        self.assertEqual(len(ws.text), 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(state.pending_trace_msgs), 1)


class GZipBinaryExclusionTests(unittest.TestCase):
    def test_query_requests_binary(self) -> None:
        detect = fastapi_app_module._query_requests_binary
        self.assertTrue(detect({"query_string": b"device_id=a&transport=binary"}))
        self.assertTrue(detect({"query_string": b"transport=Binary"}))
        self.assertFalse(detect({"query_string": b"transport=json"}))
        self.assertFalse(detect({"query_string": b""}))

    def test_binary_responses_skip_gzip(self) -> None:
        from starlette.responses import Response

        async def inner(scope, receive, send) -> None:
            # Fresh response per request: GZipResponder edits the raw header
            # list in place, so a shared Response would leak its header.
            response = Response(
                content=b"x" * 4096, media_type="application/octet-stream"
            )
            await response(scope, receive, send)

        middleware = fastapi_app_module._GZipExceptBinaryMiddleware(
            inner, minimum_size=1024
        )

        def content_encoding(query_string: bytes) -> str | None:
            sent: list[dict] = []

            async def receive() -> dict:
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message: dict) -> None:
                sent.append(message)

            scope = {
                "type": "http",
                "method": "GET",
                "path": "/blob",
                "query_string": query_string,
                "headers": [(b"accept-encoding", b"gzip")],
            }
            asyncio.run(middleware(scope, receive, send))
            headers = dict(sent[0]["headers"])
            value = headers.get(b"content-encoding")
            return value.decode() if value is not None else None

        self.assertEqual(content_encoding(b""), "gzip")
        self.assertIsNone(content_encoding(b"transport=binary"))


if __name__ == "__main__":
    unittest.main()
