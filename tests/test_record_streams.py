from __future__ import annotations

import uuid

import numpy as np
import pytest

from experiment_control.driver import DeviceRunner
from experiment_control._driver.stream_wrappers import build_stream_wrapper
from experiment_control.contracts.messages import (
    ChunkReadyMessage,
    parse_chunk_sequence_range,
)
from experiment_control.shm.shm_ring import ShmRingReader, ShmRingWriter
from experiment_control.types import (
    StreamCall,
    StreamField,
    StreamMeta,
    StreamOut,
    StreamResult,
)


@pytest.mark.parametrize(
    "payload",
    [
        {"seq": 3, "first_seq": 1, "batch_count": 2},
        {"seq": 2, "first_seq": 3, "batch_count": 0},
        {"seq": 3, "first_seq": 1},
        {"seq": 3, "batch_count": 3},
        {"seq": "bad"},
        {"seq": 3.5},
        {"seq": True},
    ],
)
def test_chunk_sequence_range_rejects_inconsistent_descriptors(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        parse_chunk_sequence_range(payload)


def test_chunk_sequence_range_accepts_legacy_single_frame_and_valid_batch() -> None:
    legacy = parse_chunk_sequence_range({"seq": 7})
    assert legacy is not None
    assert (legacy.first_seq, legacy.final_seq, legacy.batch_count) == (7, 7, 1)
    batch = parse_chunk_sequence_range(
        {"seq": 9, "first_seq": 7, "batch_count": 3},
        max_batch_count=3,
    )
    assert batch is not None
    assert (batch.first_seq, batch.final_seq, batch.batch_count) == (7, 9, 3)
    with pytest.raises(ValueError):
        parse_chunk_sequence_range(
            {"seq": 9, "first_seq": 7, "batch_count": 3},
            max_batch_count=2,
        )


def test_chunk_ready_message_rejects_invalid_sequence_range() -> None:
    assert (
        ChunkReadyMessage.parse(
            {
                "device_id": "dev",
                "stream": "trace",
                "shm_name": "ring",
                "seq": 5,
                "first_seq": 1,
                "batch_count": 2,
            }
        )
        is None
    )


def test_shm_ring_round_trips_batched_frame_metadata() -> None:
    metadata_dtype = np.dtype([("record", "u8"), ("timestamp", "f8")])
    name = f"ec_test_metadata_{uuid.uuid4().hex}"
    writer = ShmRingWriter.create(
        name=name,
        dtype="int16",
        shape=(2,),
        slot_count=4,
        layout_version=4,
        metadata_dtype=metadata_dtype,
    )
    try:
        frames = np.arange(6, dtype=np.int16).reshape(3, 2)
        metadata = np.empty(3, dtype=metadata_dtype)
        metadata["record"] = [10, 11, 12]
        metadata["timestamp"] = [1.0, 2.0, 3.0]
        first, last = writer.write_batch(
            frames,
            t0_mono_ns=[1, 2, 3],
            t0_wall_ns=[4, 5, 6],
            metadata=metadata,
        )
        assert (first, last) == (1, 3)
        reader = ShmRingReader.attach(name)
        try:
            events = reader.read_events(0)
            assert [event["seq"] for event in events] == [1, 2, 3]
            decoded = np.asarray(
                [
                    np.frombuffer(event["metadata"], dtype=metadata_dtype, count=1)[0]
                    for event in events
                ],
                dtype=metadata_dtype,
            )
            np.testing.assert_array_equal(decoded, metadata)
        finally:
            reader.close()
    finally:
        writer.close()
        writer.unlink()


def test_stream_wrapper_publishes_metadata_batch_once() -> None:
    class Device:
        @staticmethod
        def acquire(n_batch: int = 1) -> StreamResult:
            return StreamResult(
                data=np.arange(n_batch * 2, dtype=np.int16).reshape(n_batch, 2),
                meta={
                    "record": np.arange(n_batch, dtype=np.uint64),
                    "timestamp": np.arange(n_batch, dtype=np.float64),
                },
            )

    published: list[tuple[np.ndarray, np.ndarray]] = []

    class Runner:
        _device = Device()

        @staticmethod
        def publish_stream_batch(
            stream: str,
            arr: np.ndarray,
            *,
            metadata: np.ndarray | None = None,
        ) -> dict[str, object]:
            assert stream == "trace"
            assert metadata is not None
            published.append((arr.copy(), metadata.copy()))
            return {"stream": stream, "first_seq": 1, "seq": len(arr)}

    call = StreamCall(
        method="acquire",
        outputs=[StreamOut(stream="trace", dtype="int16", shape=(2,))],
        meta=[
            StreamMeta(name="record", dtype="uint64"),
            StreamMeta(name="timestamp", dtype="float64"),
        ],
    )
    result = build_stream_wrapper(runner=Runner(), stream_call=call)(n_batch=3)
    assert len(result) == 1
    assert len(published) == 1
    assert published[0][0].shape == (3, 2)
    np.testing.assert_array_equal(published[0][1]["record"], [0, 1, 2])


def test_device_runner_batch_emits_one_sequence_range_descriptor() -> None:
    metadata_dtype = np.dtype([("record", "uint64")])
    runner = object.__new__(DeviceRunner)
    runner._stream_outputs = {  # type: ignore[attr-defined]
        "trace": StreamOut(stream="trace", dtype="int16", shape=(2,), ring_slots=4)
    }
    runner._stream_last_published_seq = {}  # type: ignore[attr-defined]
    runner._stream_shm_names = {"trace": "test-ring"}  # type: ignore[attr-defined]
    runner._stream_context = {  # type: ignore[attr-defined]
        "trace": {"context_id": 9, "context_fields": {"scan": 3.0}}
    }
    runner.device_id = "dev1"  # type: ignore[attr-defined]

    class Writer:
        layout = type("Layout", (), {"layout_version": 4})()

        @staticmethod
        def write_batch(*args, **kwargs):  # type: ignore[no-untyped-def]
            return 21, 23

    runner._stream_writers = {"trace": Writer()}  # type: ignore[attr-defined]
    runner._ensure_stream_publishers = lambda: None  # type: ignore[attr-defined]
    messages: list[list[bytes]] = []
    runner.pub = type(  # type: ignore[attr-defined]
        "Pub", (), {"send_multipart": lambda self, frames: messages.append(frames)}
    )()
    metadata = np.empty(3, dtype=metadata_dtype)
    metadata["record"] = [1, 2, 3]
    descriptor = DeviceRunner.publish_stream_batch(
        runner,  # type: ignore[arg-type]
        "trace",
        np.ones((3, 2), dtype=np.int16),
        metadata=metadata,
    )
    assert descriptor["first_seq"] == 21
    assert descriptor["seq"] == 23
    assert descriptor["batch_count"] == 3
    assert descriptor["context_id"] == 9
    assert len(messages) == 1


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
