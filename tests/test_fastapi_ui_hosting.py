# ruff: noqa: E402

from __future__ import annotations

import asyncio
import importlib
import json
import sys

import numpy as np
import unittest
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from _temp_utils import repo_temp_dir

fastapi_app_module = importlib.import_module("experiment_control.fastapi.app")


def _request(accept_encoding: str = ""):
    return SimpleNamespace(headers={"accept-encoding": accept_encoding})


class _WsCapture:
    def __init__(self) -> None:
        self.text: list[str] = []
        self.binary: list[bytes] = []

    async def send_text(self, payload: str) -> None:
        self.text.append(payload)

    async def send_bytes(self, payload: bytes) -> None:
        self.binary.append(payload)


class FastApiUiHostingTests(unittest.TestCase):
    def test_index_response_is_no_cache(self) -> None:
        with repo_temp_dir("ui-hosting") as base:
            index = base / "index.html"
            index.write_text("<html></html>", encoding="utf-8")

            response = fastapi_app_module._ui_file_response(index)

        self.assertEqual(response.headers.get("cache-control"), "no-cache")

    def test_assets_response_is_immutable(self) -> None:
        with repo_temp_dir("ui-hosting") as base:
            assets = base / "assets"
            assets.mkdir()
            asset = assets / "index-abc123.js"
            asset.write_text("console.log(1)", encoding="utf-8")

            response = fastapi_app_module._ui_asset_response(
                base,
                "assets/index-abc123.js",
                request=_request(),
            )

        self.assertEqual(
            response.headers.get("cache-control"),
            "public, max-age=31536000, immutable",
        )

    def test_spa_fallback_response_is_no_cache(self) -> None:
        with repo_temp_dir("ui-hosting") as base:
            index = base / "index.html"
            index.write_text("<html></html>", encoding="utf-8")

            response = fastapi_app_module._ui_asset_response(
                base,
                "missing/path",
                request=_request(),
            )

        self.assertEqual(response.headers.get("cache-control"), "no-cache")

    def test_precompressed_asset_response_prefers_brotli(self) -> None:
        with repo_temp_dir("ui-hosting") as base:
            assets = base / "assets"
            assets.mkdir()
            asset = assets / "index-abc123.js"
            asset.write_text("console.log(1)", encoding="utf-8")
            asset.with_name(f"{asset.name}.gz").write_bytes(b"gzip-bytes")
            asset.with_name(f"{asset.name}.br").write_bytes(b"brotli-bytes")

            response = fastapi_app_module._ui_asset_response(
                base,
                "assets/index-abc123.js",
                request=_request("gzip, br"),
            )

        self.assertEqual(response.headers.get("content-encoding"), "br")
        self.assertEqual(response.headers.get("vary"), "Accept-Encoding")
        self.assertEqual(response.media_type, "text/javascript")

    def test_trace_frame_array_builder_avoids_values_list(self) -> None:
        source = np.arange(10, dtype=np.float64)
        built = fastapi_app_module._build_trace_frame_array(
            {
                "device_id": "dev",
                "stream": "trace",
                "seq": 1,
                "shape": [10],
                "values": source,
            },
            channel_index=0,
            trace_decimator="stride",
            trace_max_points=5,
        )

        self.assertIsNotNone(built)
        payload, trace = built
        self.assertNotIn("values", payload)
        self.assertEqual(payload["shape"], [5])
        self.assertEqual(payload["point_count"], 5)
        self.assertTrue(payload["decimated"])
        self.assertIsInstance(trace, np.ndarray)
        self.assertLessEqual(trace.size, 5)

    def test_binary_trace_frame_sends_metadata_then_bytes(self) -> None:
        ws = _WsCapture()
        msg = {
            "topic": "manager.stream_frame",
            "payload": {
                "device_id": "dev",
                "stream": "trace",
                "seq": 1,
                "values": [1.0, 2.0, 3.0],
            },
        }

        asyncio.run(fastapi_app_module._ws_send_binary_trace_frame(ws, msg))

        self.assertEqual(len(ws.text), 1)
        self.assertEqual(len(ws.binary), 1)
        meta = json.loads(ws.text[0])
        payload = meta["payload"]
        self.assertEqual(payload["encoding"], "binary-frame")
        self.assertEqual(payload["dtype"], "float64")
        self.assertEqual(payload["byte_length"], 24)
        self.assertNotIn("values", payload)
        self.assertEqual(len(ws.binary[0]), 24)

    def test_binary_http_response_packs_json_metadata_and_bytes(self) -> None:
        response = fastapi_app_module._binary_http_response(
            {"ok": True, "result": {"payload": {"byte_length": 16}}},
            np.asarray([1.0, 2.0], dtype=np.float64),
        )
        body = response.body
        meta_len = int.from_bytes(body[:8], byteorder="little", signed=False)
        meta = json.loads(body[8 : 8 + meta_len])
        data = body[8 + meta_len :]

        self.assertEqual(response.media_type, "application/vnd.experiment-control.binary+json")
        self.assertEqual(meta["ok"], True)
        self.assertEqual(len(data), 16)

    def test_binary_trace_frame_supports_stream_analysis_value_field(self) -> None:
        ws = _WsCapture()
        msg = {
            "topic": "manager.stream_analysis.output",
            "payload": {
                "workspace_id": "ws",
                "output_id": "trace",
                "kind": "trace",
                "seq": 1,
                "value": [1.0, 2.0],
            },
        }

        asyncio.run(
            fastapi_app_module._ws_send_binary_trace_frame(
                ws,
                msg,
                value_field="value",
            )
        )

        payload = json.loads(ws.text[0])["payload"]
        self.assertEqual(payload["encoding"], "binary-frame")
        self.assertNotIn("value", payload)
        self.assertEqual(payload["byte_length"], 16)
        self.assertEqual(len(ws.binary[0]), 16)

    def test_json_sender_strips_private_binary_values(self) -> None:
        ws = _WsCapture()
        msg = {
            "topic": "manager.stream_frame",
            "payload": {
                "device_id": "dev",
                "stream": "trace",
                "values": [1.0],
                "_binary_values": np.asarray([1.0], dtype=np.float64),
            },
        }

        asyncio.run(fastapi_app_module._ws_send_json(ws, msg))

        payload = json.loads(ws.text[0])["payload"]
        self.assertNotIn("_binary_values", payload)
        self.assertEqual(payload["values"], [1.0])


if __name__ == "__main__":
    unittest.main()
