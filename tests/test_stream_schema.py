# ruff: noqa: E402

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.schemas.stream import stream_calls_from_json, stream_calls_to_json
from experiment_control.types import StreamCall, StreamOut


class StreamSchemaTests(unittest.TestCase):
    def test_stream_calls_parses_output_attrs(self) -> None:
        calls = stream_calls_from_json(
            [
                {
                    "method": "acquire_trace",
                    "outputs": [
                        {
                            "stream": "trace",
                            "dtype": "float64",
                            "shape": [8],
                            "attrs": {"channel_names": ["A"], "axis": "sample"},
                        }
                    ],
                }
            ]
        )
        self.assertEqual(len(calls), 1)
        outputs = calls[0].outputs or []
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].attrs.get("channel_names"), ["A"])
        self.assertEqual(outputs[0].attrs.get("axis"), "sample")

    def test_stream_calls_to_json_includes_attrs(self) -> None:
        calls = [
            StreamCall(
                method="acquire_trace",
                outputs=[
                    StreamOut(
                        stream="trace",
                        dtype="float64",
                        shape=(8,),
                        attrs={"channel_names": ["A"]},
                    )
                ],
            )
        ]
        payload = stream_calls_to_json(calls)
        self.assertEqual(len(payload), 1)
        outputs = payload[0].get("outputs", [])
        self.assertEqual(len(outputs), 1)
        out = outputs[0]
        self.assertEqual(out.get("attrs", {}).get("channel_names"), ["A"])
        self.assertEqual(out.get("stream"), "trace")
        self.assertEqual(out.get("dtype"), "float64")
        self.assertEqual(out.get("shape"), [8])


if __name__ == "__main__":
    unittest.main()
