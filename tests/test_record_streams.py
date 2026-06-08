from __future__ import annotations

import uuid

import numpy as np
import pytest

from experiment_control.driver import DeviceRunner
from experiment_control._driver.stream_wrappers import build_stream_wrapper
from experiment_control.shm.shm_ring import ShmRingReader, ShmRingWriter
from experiment_control.types import StreamCall, StreamField, StreamOut


def test_shm_ring_round_trips_structured_record_dtype() -> None:
    dtype = np.dtype([("sample_seq", "u8"), ("frequency_hz", "f8")])
    name = f"ec_test_records_{uuid.uuid4().hex}"
    writer = ShmRingWriter.create(
        name=name,
        dtype=dtype,
        shape=(),
        slot_count=4,
        layout_version=3,
    )
    try:
        arr = np.asarray((7, 456.0), dtype=dtype).reshape(())
        seq = writer.write(arr, t0_mono_ns=11, t0_wall_ns=22)
        reader = ShmRingReader.attach(name)
        try:
            event = reader.read_event(seq)
            assert event is not None
            out = np.frombuffer(event["payload"], dtype=reader.layout.dtype).reshape(())
            assert reader.layout.dtype == dtype
            assert tuple(reader.layout.shape) == ()
            assert int(out["sample_seq"]) == 7
            assert float(out["frequency_hz"]) == 456.0
        finally:
            reader.close()
    finally:
        writer.close()
        writer.unlink()


def test_frame_stream_wrapper_rejects_mismatched_dtype() -> None:
    class Device:
        @staticmethod
        def acquire_trace() -> np.ndarray:
            return np.ones((2,), dtype=np.float64)

    class Runner:
        _device = Device()

        @staticmethod
        def publish_stream(stream: str, arr: np.ndarray) -> dict[str, object]:
            del stream, arr
            raise AssertionError("mismatched frame stream should not publish")

    call = StreamCall(
        method="acquire_trace",
        outputs=[StreamOut(stream="trace", dtype="int16", shape=(2,))],
    )
    wrapper = build_stream_wrapper(runner=Runner(), stream_call=call)

    with pytest.raises(ValueError, match="dtype mismatch"):
        wrapper()


def test_publish_stream_rejects_mismatched_frame_dtype_before_writer_setup() -> None:
    runner = object.__new__(DeviceRunner)
    runner._stream_outputs = {  # type: ignore[attr-defined]
        "trace": StreamOut(stream="trace", dtype="int16", shape=(2,))
    }

    with pytest.raises(ValueError, match="dtype mismatch"):
        DeviceRunner.publish_stream(
            runner,  # type: ignore[arg-type]
            "trace",
            np.ones((2,), dtype=np.float64),
        )


def test_record_stream_wrapper_splits_hf_style_structured_batches() -> None:
    dtype = np.dtype(
        [
            ("sample_seq", "u8"),
            ("t_mono_s", "f8"),
            ("channel", "i4"),
            ("dwell_id", "i8"),
            ("frequency_hz", "f8"),
            ("wavelength_nm", "f8"),
            ("status_code", "i4"),
        ]
    )

    class Device:
        @staticmethod
        def acquire_frequency_records(max_records: int | None = None) -> np.ndarray:
            del max_records
            records = np.empty(2, dtype=dtype)
            records[0] = (1, 10.0, 1, 4, 101.0, 500.0, 0)
            records[1] = (2, 11.0, 2, 5, 202.0, 501.0, 0)
            return records

    published: list[np.ndarray] = []

    class Runner:
        _device = Device()

        @staticmethod
        def publish_stream(stream: str, arr: np.ndarray) -> dict[str, object]:
            assert stream == "frequency_records"
            published.append(np.asarray(arr).copy())
            return {"stream": stream}

    fields = tuple(
        StreamField(name=name, dtype=str(dtype.fields[name][0]))
        for name in dtype.names or ()
    )
    call = StreamCall(
        method="acquire_frequency_records",
        kwargs={"max_records": 512},
        outputs=[
            StreamOut(
                stream="frequency_records",
                kind="records",
                fields=fields,
                ring_slots=4096,
            )
        ],
    )
    wrapper = build_stream_wrapper(runner=Runner(), stream_call=call)

    result = wrapper()

    assert len(result) == 2
    assert [item.shape for item in published] == [(), ()]
    assert all(item.dtype == dtype for item in published)
    assert [int(item["channel"]) for item in published] == [1, 2]
    assert [float(item["frequency_hz"]) for item in published] == [101.0, 202.0]
