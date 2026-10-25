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


class SharedTraceFrameTests(unittest.TestCase):
    def setUp(self) -> None:
        fastapi_app_module._TRACE_FRAME_CACHE.clear()

    @staticmethod
    def _trace_ready_msg(seq: int, *, t0: int = 1000) -> dict:
        return {
            "topic": "manager.stream_analysis.output",
            "payload": {
                "workspace_id": "ws1",
                "output_id": "o",
                "kind": "trace",
                "seq": seq,
                "t0_mono_ns": t0,
                "_raw_trace": np.sin(np.linspace(0.0, 20.0, 400)),
            },
        }

    def _send(self, state: object, msg: dict) -> _WsCapture:
        ws = _WsCapture()
        asyncio.run(fastapi_app_module._workspace_send_trace_payload(ws, state, msg))
        return ws

    def _count_decimations(self) -> tuple[list[int], object]:
        calls: list[int] = []
        original = fastapi_app_module._decimate_trace_values

        def counting(values, *, mode, max_points):
            calls.append(1)
            return original(values, mode=mode, max_points=max_points)

        fastapi_app_module._decimate_trace_values = counting
        self.addCleanup(setattr, fastapi_app_module, "_decimate_trace_values", original)
        return calls, original

    def test_identical_workspace_frames_are_encoded_once_for_all_sockets(self) -> None:
        calls, _ = self._count_decimations()
        a = _workspace_state(binary=True, trace_max_points=64)
        b = _workspace_state(binary=True, trace_max_points=64)
        # Each socket builds its own payload dict from shared memory.
        ws_a = self._send(a, self._trace_ready_msg(7))
        ws_b = self._send(b, self._trace_ready_msg(7))
        self.assertEqual(len(calls), 1)
        self.assertEqual(ws_a.text, ws_b.text)
        self.assertEqual(ws_a.binary, ws_b.binary)

    def test_restarted_producer_with_reused_seq_is_not_served_from_cache(self) -> None:
        calls, _ = self._count_decimations()
        state = _workspace_state(binary=True, trace_max_points=64)
        self._send(state, self._trace_ready_msg(7, t0=1000))
        self._send(state, self._trace_ready_msg(7, t0=2000))
        self.assertEqual(len(calls), 2)

    def test_rolling_average_sockets_never_share(self) -> None:
        state = _workspace_state(binary=True, trace_max_points=64)
        state.rolling_window = 4
        key = fastapi_app_module._workspace_trace_cache_key(state, self._trace_ready_msg(7))
        self.assertIsNone(key)

    def test_different_parameters_get_separate_frames(self) -> None:
        calls, _ = self._count_decimations()
        self._send(_workspace_state(binary=True, trace_max_points=64), self._trace_ready_msg(7))
        self._send(_workspace_state(binary=True, trace_max_points=128), self._trace_ready_msg(7))
        self.assertEqual(len(calls), 2)

    def test_raw_key_names_the_source_frame_and_socket_parameters(self) -> None:
        key = fastapi_app_module._raw_trace_cache_key
        params = dict(
            channel_index=0,
            trace_decimator="minmax",
            trace_max_points=1024,
            binary_transport=True,
            compact_dtype=True,
        )
        source = np.zeros(4)
        payload = {"device_id": "d", "stream": "s", "seq": 5, "t0_mono_ns": 100, "_source_values": source}
        same_frame = dict(payload, _source_values=np.ones(4))
        self.assertEqual(key(payload, **params), key(same_frame, **params))
        # The key keeps no reference to the payload's arrays.
        self.assertFalse(any(part is source for part in key(payload, **params)))
        # A restarted device reuses seq 5 with a new timestamp.
        self.assertNotEqual(key(payload, **params), key(dict(payload, t0_mono_ns=999), **params))
        self.assertNotEqual(key(payload, **params), key(payload, **dict(params, channel_index=1)))
        # Without a seq or timestamps a frame cannot be shared.
        self.assertIsNone(key({"device_id": "d", "stream": "s", "seq": 5}, **params))
        self.assertIsNone(key({"device_id": "d", "stream": "s", "t0_mono_ns": 1}, **params))

    def test_cache_evicts_least_recently_used(self) -> None:
        cache = fastapi_app_module.SharedTraceFrameCache(maxsize=2)
        frames = [fastapi_app_module.EncodedTraceFrame(str(i), None) for i in range(3)]
        cache.put("a", frames[0])
        cache.put("b", frames[1])
        cache.get("a")
        cache.put("c", frames[2])
        self.assertIsNone(cache.get("b"))
        self.assertIs(cache.get("a"), frames[0])


class CompactTraceDtypeTests(unittest.TestCase):
    def test_float32_only_when_rounding_is_far_below_the_span(self) -> None:
        sufficient = fastapi_app_module._float32_is_sufficient
        self.assertTrue(sufficient(np.sin(np.linspace(0.0, 10.0, 1000))))
        self.assertTrue(sufficient(np.zeros(8)))
        self.assertTrue(sufficient(np.full(8, 2.0)))
        # A trace riding on a large offset would lose visible detail.
        self.assertFalse(sufficient(1e9 + np.sin(np.linspace(0.0, 10.0, 1000))))
        self.assertFalse(sufficient(np.full(8, 0.1)))
        # Outside float32's normal range: overflow to inf, or subnormal.
        self.assertFalse(sufficient(np.array([0.0, 1e39])))
        self.assertFalse(sufficient(np.array([-1e39, 1e39])))
        self.assertFalse(sufficient(np.array([0.0, 1e-40, 2e-40])))

    def test_binary_frame_uses_float32_only_when_allowed(self) -> None:
        values = np.sin(np.linspace(0.0, 10.0, 256))
        msg = {"topic": "t", "payload": {"seq": 1, "_binary_values": values}}
        compact = fastapi_app_module._encode_binary_trace_frame(msg, allow_float32=True)
        meta = json.loads(compact.text)["payload"]
        self.assertEqual(meta["dtype"], "float32")
        self.assertEqual(meta["byte_length"], 256 * 4)
        decoded = np.frombuffer(compact.body, dtype="<f4")
        np.testing.assert_allclose(decoded, values, atol=1e-6)

        full = fastapi_app_module._encode_binary_trace_frame(msg)
        self.assertEqual(json.loads(full.text)["payload"]["dtype"], "float64")
        np.testing.assert_array_equal(np.frombuffer(full.body, dtype="<f8"), values)

    def test_offset_trace_stays_float64_even_when_allowed(self) -> None:
        values = 1e9 + np.arange(16, dtype=np.float64)
        msg = {"topic": "t", "payload": {"seq": 1, "_binary_values": values}}
        frame = fastapi_app_module._encode_binary_trace_frame(msg, allow_float32=True)
        self.assertEqual(json.loads(frame.text)["payload"]["dtype"], "float64")

    def test_dtype_query_parsing(self) -> None:
        self.assertTrue(fastapi_app_module._parse_compact_dtype("auto"))
        self.assertTrue(fastapi_app_module._parse_compact_dtype(" AUTO "))
        self.assertFalse(fastapi_app_module._parse_compact_dtype(None))
        self.assertFalse(fastapi_app_module._parse_compact_dtype("float64"))


class TelemetryDeviceFilterTests(unittest.TestCase):
    @staticmethod
    def _item(device_id: str) -> HubMessage:
        msg = {"topic": "manager.telemetry", "payload": {"device_id": device_id, "signals": {}}}
        return HubMessage(json_dumps(msg).decode("utf-8"), msg)

    def test_unfiltered_socket_gets_every_shared_text(self) -> None:
        item = self._item("a")
        self.assertIs(fastapi_app_module._telemetry_ws_text(item, None), item.text)

    def test_filtered_socket_skips_other_devices(self) -> None:
        only_a = {"a"}
        self.assertEqual(
            fastapi_app_module._telemetry_ws_text(self._item("a"), only_a),
            self._item("a").text,
        )
        self.assertIsNone(fastapi_app_module._telemetry_ws_text(self._item("b"), only_a))

    def test_plain_string_items_pass_through(self) -> None:
        self.assertEqual(fastapi_app_module._telemetry_ws_text("{}", {"a"}), "{}")


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
