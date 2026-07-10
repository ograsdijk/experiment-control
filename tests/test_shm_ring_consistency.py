from __future__ import annotations

import builtins
import uuid
from unittest import TestCase

import numpy as np

from experiment_control.shm import shm_ring
from experiment_control.shm.shm_ring import ShmRingReader, ShmRingWriter


class ShmRingConsistencyTests(TestCase):
    def setUp(self) -> None:
        self._writers: list[ShmRingWriter] = []
        self._readers: list[ShmRingReader] = []

    def tearDown(self) -> None:
        for reader in self._readers:
            reader.close()
        for writer in self._writers:
            writer.close()
            writer.unlink()

    def _ring(
        self,
        *,
        dtype: str | np.dtype[object],
        shape: tuple[int, ...],
        slot_count: int = 1,
        layout_version: int = 1,
    ) -> tuple[ShmRingWriter, ShmRingReader]:
        writer = ShmRingWriter.create(
            f"ec_test_f13_{uuid.uuid4().hex}",
            dtype=dtype,
            shape=shape,
            slot_count=slot_count,
            layout_version=layout_version,
        )
        reader = ShmRingReader.attach(writer.name)
        self._writers.append(writer)
        self._readers.append(reader)
        return writer, reader

    def test_write_round_trips_multidimensional_noncontiguous_array(self) -> None:
        writer, reader = self._ring(dtype="float64", shape=(2, 3))
        source = np.arange(12, dtype=np.float64).reshape(2, 6)[:, ::2]
        self.assertFalse(source.flags.c_contiguous)

        seq = writer.write(source, t0_mono_ns=11, t0_wall_ns=22)

        event = reader.read_event(seq)
        self.assertIsNotNone(event)
        assert event is not None
        actual = np.frombuffer(event["payload"], dtype=np.float64).reshape(2, 3)
        np.testing.assert_array_equal(actual, source)
        self.assertEqual(event["t0_mono_ns"], 11)
        self.assertEqual(event["t0_wall_ns"], 22)

    def test_write_round_trips_scalar_structured_record(self) -> None:
        dtype = np.dtype([("count", "<i4"), ("value", "<f8")])
        writer, reader = self._ring(
            dtype=dtype,
            shape=(),
            layout_version=3,
        )
        source = np.array((7, 2.5), dtype=dtype).reshape(())

        seq = writer.write(source, t0_mono_ns=1, t0_wall_ns=2)

        event = reader.read_event(seq)
        self.assertIsNotNone(event)
        assert event is not None
        actual = np.frombuffer(event["payload"], dtype=dtype).reshape(())
        self.assertEqual(actual["count"].item(), 7)
        self.assertEqual(actual["value"].item(), 2.5)

    def test_read_event_discards_slot_overwritten_during_payload_copy(self) -> None:
        writer, reader = self._ring(dtype="int64", shape=(4,))
        old_seq = writer.write(
            np.full(4, 1, dtype=np.int64),
            t0_mono_ns=1,
            t0_wall_ns=1,
        )
        overwritten = False

        def overwrite_then_copy(value: object) -> bytes:
            nonlocal overwritten
            if not overwritten:
                overwritten = True
                writer.write(
                    np.full(4, 2, dtype=np.int64),
                    t0_mono_ns=2,
                    t0_wall_ns=2,
                )
            return builtins.bytes(value)

        with _PatchedModuleBytes(overwrite_then_copy):
            event = reader.read_event(old_seq)

        self.assertTrue(overwritten)
        self.assertIsNone(event)

    def test_read_events_discards_slot_overwritten_during_payload_copy(self) -> None:
        writer, reader = self._ring(dtype="int64", shape=(4,))
        writer.write(
            np.full(4, 1, dtype=np.int64),
            t0_mono_ns=1,
            t0_wall_ns=1,
        )
        overwritten = False

        def overwrite_then_copy(value: object) -> bytes:
            nonlocal overwritten
            if not overwritten:
                overwritten = True
                writer.write(
                    np.full(4, 2, dtype=np.int64),
                    t0_mono_ns=2,
                    t0_wall_ns=2,
                )
            return builtins.bytes(value)

        with _PatchedModuleBytes(overwrite_then_copy):
            events = reader.read_events(0)

        self.assertTrue(overwritten)
        self.assertEqual(events, [])
        replacement = reader.read_events(0)
        self.assertEqual([event["seq"] for event in replacement], [2])
        actual = np.frombuffer(replacement[0]["payload"], dtype=np.int64)
        np.testing.assert_array_equal(actual, np.full(4, 2, dtype=np.int64))

    def test_create_rejects_object_dtype(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot contain Python objects"):
            ShmRingWriter.create(
                f"ec_test_f13_{uuid.uuid4().hex}",
                dtype="object",
                shape=(1,),
                slot_count=1,
            )


class _PatchedModuleBytes:
    def __init__(self, replacement: object) -> None:
        self._replacement = replacement

    def __enter__(self) -> None:
        setattr(shm_ring, "bytes", self._replacement)

    def __exit__(self, *_exc: object) -> None:
        delattr(shm_ring, "bytes")
