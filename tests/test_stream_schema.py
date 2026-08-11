# ruff: noqa: E402

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.schemas.stream import (
    stream_calls_from_json,
    stream_calls_to_json,
)
from experiment_control.types import StreamCall, StreamField, StreamMeta, StreamOut


class StreamSchemaTests(unittest.TestCase):
    def test_stream_meta_rejects_hdf_incompatible_names_and_dtypes(self) -> None:
        for name in ("", "has space", "path/name", "1starts_with_digit", "naive-name"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                StreamMeta(name=name, dtype="float64")
        for dtype in ("str", "U8", "datetime64[ns]", "timedelta64[ns]", "V8"):
            with self.subTest(dtype=dtype), self.assertRaises(ValueError):
                StreamMeta(name="valid_name", dtype=dtype)

    def test_stream_meta_accepts_supported_fixed_size_scalars(self) -> None:
        for dtype in ("bool", "uint64", "int32", "float64", "complex128", "S16"):
            with self.subTest(dtype=dtype):
                self.assertEqual(
                    StreamMeta(name="valid_name", dtype=dtype).name, "valid_name"
                )

    def test_stream_metadata_round_trip(self) -> None:
        raw = [
            {
                "method": "acquire",
                "meta": [
                    {
                        "name": "hardware_frame_id",
                        "dtype": "uint64",
                        "units": None,
                        "description": "Device frame counter",
                    }
                ],
                "outputs": [{"stream": "trace", "dtype": "int16", "shape": [2]}],
            }
        ]
        calls = stream_calls_from_json(raw)
        self.assertEqual(calls[0].meta[0].name, "hardware_frame_id")
        self.assertEqual(calls[0].meta[0].dtype, "uint64")
        encoded = stream_calls_to_json(calls)
        self.assertEqual(encoded[0]["meta"][0]["name"], "hardware_frame_id")

    def test_stream_metadata_rejects_reserved_name(self) -> None:
        with self.assertRaisesRegex(TypeError, "reserved"):
            stream_calls_from_json(
                [
                    {
                        "method": "acquire",
                        "meta": [{"name": "seq", "dtype": "uint64"}],
                        "outputs": [
                            {"stream": "trace", "dtype": "int16", "shape": [2]}
                        ],
                    }
                ]
            )

    def test_stream_calls_parses_output_attrs(self) -> None:
        calls = stream_calls_from_json(
            [
                {
                    "method": "acquire_trace",
                    "period_s": 1.5,
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
        self.assertEqual(calls[0].period_s, 1.5)
        self.assertEqual(outputs[0].attrs.get("channel_names"), ["A"])
        self.assertEqual(outputs[0].attrs.get("axis"), "sample")

    def test_stream_calls_period_is_optional(self) -> None:
        calls = stream_calls_from_json(
            [
                {
                    "method": "acquire_trace",
                    "outputs": [
                        {
                            "stream": "trace",
                            "dtype": "float64",
                            "shape": [8],
                        }
                    ],
                }
            ]
        )
        self.assertIsNone(calls[0].period_s)

    def test_stream_calls_rejects_nonpositive_period(self) -> None:
        with self.assertRaises(TypeError):
            stream_calls_from_json(
                [
                    {
                        "method": "acquire_trace",
                        "period_s": 0,
                        "outputs": [
                            {
                                "stream": "trace",
                                "dtype": "float64",
                                "shape": [8],
                            }
                        ],
                    }
                ]
            )

    def test_stream_calls_to_json_includes_attrs(self) -> None:
        calls = [
            StreamCall(
                method="acquire_trace",
                period_s=2.0,
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
        self.assertEqual(payload[0].get("period_s"), 2.0)
        outputs = payload[0].get("outputs", [])
        self.assertEqual(len(outputs), 1)
        out = outputs[0]
        self.assertEqual(out.get("attrs", {}).get("channel_names"), ["A"])
        self.assertEqual(out.get("stream"), "trace")
        self.assertEqual(out.get("dtype"), "float64")
        self.assertEqual(out.get("shape"), [8])

    def test_stream_calls_parses_record_output_fields(self) -> None:
        calls = stream_calls_from_json(
            [
                {
                    "method": "acquire_records",
                    "outputs": [
                        {
                            "stream": "frequency_records",
                            "kind": "records",
                            "ring_slots": 64,
                            "fields": [
                                {"name": "sample_seq", "dtype": "uint64"},
                                {
                                    "name": "frequency_hz",
                                    "dtype": "float64",
                                    "units": "Hz",
                                },
                            ],
                        }
                    ],
                }
            ]
        )
        outputs = calls[0].outputs or []
        self.assertEqual(outputs[0].kind, "records")
        self.assertEqual(outputs[0].shape, ())
        self.assertEqual(outputs[0].numpy_dtype().names, ("sample_seq", "frequency_hz"))
        self.assertFalse(outputs[0].numpy_dtype().isalignedstruct)

    def test_stream_calls_to_json_writes_record_fields(self) -> None:
        calls = [
            StreamCall(
                method="acquire_records",
                outputs=[
                    StreamOut(
                        stream="frequency_records",
                        kind="records",
                        fields=(
                            StreamField(name="sample_seq", dtype="uint64"),
                            StreamField(
                                name="frequency_hz", dtype="float64", units="Hz"
                            ),
                        ),
                    )
                ],
            )
        ]
        payload = stream_calls_to_json(calls)
        output = payload[0]["outputs"][0]
        self.assertEqual(output["kind"], "records")
        self.assertEqual(
            [field["name"] for field in output["fields"]],
            ["sample_seq", "frequency_hz"],
        )
        self.assertNotIn("shape", output)


if __name__ == "__main__":
    unittest.main()
