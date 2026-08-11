from __future__ import annotations

import struct
import time
import json
from dataclasses import dataclass
from math import prod
from multiprocessing import shared_memory
from typing import Any, cast

import numpy as np

MAGIC = b"CNTXSHM1"
HEADER_SIZE = 64
SLOT_ENTRY_SIZE = 64
# Slot fields: seq_begin, t0_mono_ns, t0_wall_ns, shot(reserved), flags(reserved), r0, r1, r2
SLOT_STRUCT = struct.Struct("<QQQQQQQQ")
SEQ_END_OFFSET = 32
HEADER_STRUCT_V1 = struct.Struct("<8sIIIIII")
HEADER_STRUCT_V2 = struct.Struct("<8sIIIIIIQQQQ")
HEADER_LAST_SEQ_OFFSET = 32
HEADER_LAST_SLOT_OFFSET = 40
STRUCTURED_DTYPE_PREFIX = "json:"


def dtype_nbytes(dtype_str: str) -> int:
    return int(np.dtype(dtype_str).itemsize)


def prod_shape(shape: tuple[int, ...]) -> int:
    return int(prod(shape))


def _dtype_to_header_text(dtype: np.dtype[Any]) -> str:
    if dtype.fields is None:
        return str(dtype)
    return STRUCTURED_DTYPE_PREFIX + json.dumps(
        {
            "kind": "structured_dtype",
            "descr": dtype.descr,
        },
        separators=(",", ":"),
    )


def _dtype_from_header_text(text: str) -> np.dtype[Any]:
    if text.startswith(STRUCTURED_DTYPE_PREFIX):
        raw = json.loads(text[len(STRUCTURED_DTYPE_PREFIX) :])
        if not isinstance(raw, dict) or raw.get("kind") != "structured_dtype":
            raise ValueError("Invalid structured dtype header")
        descr = raw.get("descr")
        if not isinstance(descr, list):
            raise ValueError("Invalid structured dtype descriptor")
        return np.dtype([tuple(item) for item in descr])
    return np.dtype(text)


def now_mono_ns() -> int:
    return int(time.monotonic_ns())


def now_wall_ns() -> int:
    return int(time.time_ns())


@dataclass
class ShmLayout:
    dtype: np.dtype[Any]
    shape: tuple[int, ...]
    slot_count: int
    payload_nbytes: int
    layout_version: int
    dtype_str_len: int
    shape_len: int
    slot_table_offset: int
    payload_offset: int
    metadata_dtype: np.dtype[Any] | None = None
    metadata_nbytes: int = 0
    metadata_dtype_str_len: int = 0
    metadata_offset: int = 0


class ShmRingWriter:
    def __init__(self, shm: shared_memory.SharedMemory, *, layout: ShmLayout) -> None:
        self._shm = shm
        self._layout = layout
        self._buf = cast(memoryview, shm.buf)
        self._next_seq = 0
        self._next_slot = 0

    @property
    def name(self) -> str:
        return self._shm.name

    @property
    def layout(self) -> ShmLayout:
        return self._layout

    @classmethod
    def create(
        cls,
        name: str,
        *,
        dtype: str | np.dtype[Any],
        shape: tuple[int, ...],
        slot_count: int,
        layout_version: int = 1,
        metadata_dtype: np.dtype[Any] | None = None,
    ) -> "ShmRingWriter":
        dtype_obj = np.dtype(dtype)
        if dtype_obj.hasobject:
            raise ValueError("SHM ring dtype cannot contain Python objects")
        payload_nbytes = int(dtype_obj.itemsize * prod(shape))
        dtype_bytes = _dtype_to_header_text(dtype_obj).encode("utf-8")
        shape_len = len(shape)
        dtype_str_len = len(dtype_bytes)
        metadata_dtype_obj = None if metadata_dtype is None else np.dtype(metadata_dtype)
        if metadata_dtype_obj is not None:
            if metadata_dtype_obj.hasobject or metadata_dtype_obj.fields is None:
                raise ValueError("SHM ring metadata dtype must be a structured fixed-size dtype")
            if layout_version < 4:
                raise ValueError("SHM ring metadata requires layout_version >= 4")
            metadata_dtype_bytes = _dtype_to_header_text(metadata_dtype_obj).encode("utf-8")
            metadata_nbytes = int(metadata_dtype_obj.itemsize)
        else:
            metadata_dtype_bytes = b""
            metadata_nbytes = 0
        metadata_dtype_str_len = len(metadata_dtype_bytes)

        slot_table_offset = (
            HEADER_SIZE + dtype_str_len + shape_len * 4 + metadata_dtype_str_len
        )
        payload_offset = slot_table_offset + slot_count * SLOT_ENTRY_SIZE
        metadata_offset = payload_offset + slot_count * payload_nbytes
        total_bytes = metadata_offset + slot_count * metadata_nbytes

        try:
            shm = shared_memory.SharedMemory(name=name, create=True, size=total_bytes)
        except FileExistsError:
            # A previous run died without unlinking; reclaim the name.
            # Catch (don't `finally: pass`) so a flaky `close()` doesn't
            # block the subsequent `unlink()`, and a flaky `unlink()`
            # doesn't block the retry. The retry below will surface a
            # second FileExistsError if reclaim genuinely failed.
            stale = shared_memory.SharedMemory(name=name, create=False)
            try:
                stale.close()
            except Exception:
                pass
            try:
                stale.unlink()
            except Exception:
                pass
            shm = shared_memory.SharedMemory(name=name, create=True, size=total_bytes)

        buf = cast(memoryview, shm.buf)
        buf[:total_bytes] = b"\x00" * total_bytes
        if layout_version == 1:
            HEADER_STRUCT_V1.pack_into(
                buf,
                0,
                MAGIC,
                int(layout_version),
                int(slot_count),
                int(payload_nbytes),
                int(dtype_str_len),
                int(shape_len),
                0,
            )
        else:
            HEADER_STRUCT_V2.pack_into(
                buf,
                0,
                MAGIC,
                int(layout_version),
                int(slot_count),
                int(payload_nbytes),
                int(dtype_str_len),
                int(shape_len),
                0,
                0,
                0,
                int(metadata_dtype_str_len),
                int(metadata_nbytes),
            )
        buf[HEADER_SIZE : HEADER_SIZE + dtype_str_len] = dtype_bytes
        shape_offset = HEADER_SIZE + dtype_str_len
        for i, dim in enumerate(shape):
            struct.pack_into("<i", buf, shape_offset + i * 4, int(dim))
        metadata_dtype_offset = shape_offset + shape_len * 4
        if metadata_dtype_bytes:
            buf[
                metadata_dtype_offset : metadata_dtype_offset + metadata_dtype_str_len
            ] = metadata_dtype_bytes

        layout = ShmLayout(
            dtype=dtype_obj,
            shape=tuple(int(x) for x in shape),
            slot_count=int(slot_count),
            payload_nbytes=int(payload_nbytes),
            layout_version=int(layout_version),
            dtype_str_len=int(dtype_str_len),
            shape_len=int(shape_len),
            slot_table_offset=int(slot_table_offset),
            payload_offset=int(payload_offset),
            metadata_dtype=metadata_dtype_obj,
            metadata_nbytes=metadata_nbytes,
            metadata_dtype_str_len=metadata_dtype_str_len,
            metadata_offset=int(metadata_offset),
        )
        return cls(shm, layout=layout)

    def write(
        self,
        arr: np.ndarray,
        *,
        t0_mono_ns: int,
        t0_wall_ns: int,
        metadata: np.ndarray | np.void | None = None,
    ) -> int:
        if arr.dtype != self._layout.dtype:
            raise ValueError(
                f"dtype mismatch: got {arr.dtype}, expected {self._layout.dtype}"
            )
        if tuple(arr.shape) != self._layout.shape:
            raise ValueError(
                f"shape mismatch: got {arr.shape}, expected {self._layout.shape}"
            )
        metadata_dtype = self._layout.metadata_dtype
        if metadata_dtype is None:
            if metadata is not None:
                raise ValueError("metadata provided for a ring without a metadata schema")
        else:
            if metadata is None:
                raise ValueError("metadata required by the SHM ring schema")
            metadata_arr = np.asarray(metadata)
            if metadata_arr.dtype != metadata_dtype or metadata_arr.shape != ():
                raise ValueError(
                    "metadata mismatch: "
                    f"got dtype={metadata_arr.dtype}, shape={metadata_arr.shape}; "
                    f"expected dtype={metadata_dtype}, shape=()"
                )

        slot = self._next_slot
        seq = self._next_seq + 1

        slot_offset = self._layout.slot_table_offset + slot * SLOT_ENTRY_SIZE
        # Invalidate slot before writing payload to avoid torn reads.
        struct.pack_into("<Q", self._buf, slot_offset, 0)
        struct.pack_into("<Q", self._buf, slot_offset + SEQ_END_OFFSET, 0)

        payload_start = self._layout.payload_offset + slot * self._layout.payload_nbytes
        payload_view = np.ndarray(
            shape=self._layout.shape,
            dtype=self._layout.dtype,
            buffer=self._buf,
            offset=payload_start,
        )
        np.copyto(payload_view, arr, casting="no")
        if metadata_dtype is not None:
            metadata_start = (
                self._layout.metadata_offset + slot * self._layout.metadata_nbytes
            )
            metadata_view = np.ndarray(
                shape=(),
                dtype=metadata_dtype,
                buffer=self._buf,
                offset=metadata_start,
            )
            np.copyto(metadata_view, metadata_arr, casting="no")

        SLOT_STRUCT.pack_into(
            self._buf,
            slot_offset,
            int(seq),
            int(t0_mono_ns),
            int(t0_wall_ns),
            0,
            0,
            0,
            0,
            0,
        )
        struct.pack_into("<Q", self._buf, slot_offset + SEQ_END_OFFSET, int(seq))

        if self._layout.layout_version >= 2:
            struct.pack_into("<Q", self._buf, HEADER_LAST_SEQ_OFFSET, int(seq))
            struct.pack_into("<Q", self._buf, HEADER_LAST_SLOT_OFFSET, int(slot))

        self._next_seq = int(seq)
        self._next_slot = (slot + 1) % self._layout.slot_count
        return seq

    def write_batch(
        self,
        arr: np.ndarray,
        *,
        t0_mono_ns: list[int] | np.ndarray,
        t0_wall_ns: list[int] | np.ndarray,
        metadata: np.ndarray | None = None,
    ) -> tuple[int, int]:
        """Write multiple logical frames and return their inclusive seq range."""
        batch = np.asarray(arr)
        if batch.ndim < 1:
            raise ValueError("batched stream payload must have a leading batch axis")
        expected_shape = (batch.shape[0],) + self._layout.shape
        if tuple(batch.shape) != tuple(expected_shape):
            raise ValueError(
                f"batched shape mismatch: got {batch.shape}, expected (n, {self._layout.shape})"
            )
        if batch.dtype != self._layout.dtype:
            raise ValueError(
                f"dtype mismatch: got {batch.dtype}, expected {self._layout.dtype}"
            )
        count = int(batch.shape[0])
        if count < 1 or count > self._layout.slot_count:
            raise ValueError("batch size must be between 1 and the ring slot count")
        mono = np.asarray(t0_mono_ns, dtype=np.uint64)
        wall = np.asarray(t0_wall_ns, dtype=np.uint64)
        if mono.shape != (count,) or wall.shape != (count,):
            raise ValueError("batch timestamps must contain one value per frame")
        if self._layout.metadata_dtype is None:
            if metadata is not None:
                raise ValueError("metadata provided for a ring without a metadata schema")
        else:
            if metadata is None:
                raise ValueError("metadata required by the SHM ring schema")
            if metadata.dtype != self._layout.metadata_dtype or metadata.shape != (count,):
                raise ValueError("batch metadata must match the ring schema and batch size")

        first_seq = 0
        last_seq = 0
        for idx in range(count):
            last_seq = self.write(
                np.asarray(batch[idx]),
                t0_mono_ns=int(mono[idx]),
                t0_wall_ns=int(wall[idx]),
                metadata=None if metadata is None else metadata[idx],
            )
            if idx == 0:
                first_seq = last_seq
        return first_seq, last_seq

    def close(self) -> None:
        self._shm.close()

    def unlink(self) -> None:
        self._shm.unlink()


class ShmRingReader:
    def __init__(self, shm: shared_memory.SharedMemory, *, layout: ShmLayout) -> None:
        self._shm = shm
        self._layout = layout
        self._buf = cast(memoryview, shm.buf)

    @property
    def name(self) -> str:
        return self._shm.name

    @property
    def layout(self) -> ShmLayout:
        return self._layout

    @classmethod
    def attach(cls, name: str) -> "ShmRingReader":
        shm = shared_memory.SharedMemory(name=name, create=False)
        buf = cast(memoryview, shm.buf)

        magic, layout_version, slot_count, payload_nbytes, dtype_len, shape_len, _ = (
            HEADER_STRUCT_V1.unpack_from(buf, 0)
        )
        if magic != MAGIC:
            raise ValueError(f"Invalid shm ring magic for {name!r}")

        dtype_start = HEADER_SIZE
        dtype_end = HEADER_SIZE + int(dtype_len)
        dtype_str = bytes(buf[dtype_start:dtype_end]).decode("utf-8")
        shape_offset = dtype_end
        shape = tuple(
            int(struct.unpack_from("<i", buf, shape_offset + i * 4)[0])
            for i in range(int(shape_len))
        )

        metadata_dtype: np.dtype[Any] | None = None
        metadata_dtype_len = 0
        metadata_nbytes = 0
        if int(layout_version) >= 4:
            header_v2 = HEADER_STRUCT_V2.unpack_from(buf, 0)
            metadata_dtype_len = int(header_v2[-2])
            metadata_nbytes = int(header_v2[-1])
            metadata_dtype_offset = shape_offset + int(shape_len) * 4
            metadata_dtype_end = metadata_dtype_offset + metadata_dtype_len
            metadata_dtype_text = bytes(
                buf[metadata_dtype_offset:metadata_dtype_end]
            ).decode("utf-8")
            metadata_dtype = _dtype_from_header_text(metadata_dtype_text)
            if metadata_dtype.itemsize != metadata_nbytes:
                raise ValueError(f"Invalid SHM metadata layout for {name!r}")

        slot_table_offset = (
            HEADER_SIZE
            + int(dtype_len)
            + int(shape_len) * 4
            + metadata_dtype_len
        )
        payload_offset = slot_table_offset + int(slot_count) * SLOT_ENTRY_SIZE
        metadata_offset = payload_offset + int(slot_count) * int(payload_nbytes)

        layout = ShmLayout(
            dtype=_dtype_from_header_text(dtype_str),
            shape=shape,
            slot_count=int(slot_count),
            payload_nbytes=int(payload_nbytes),
            layout_version=int(layout_version),
            dtype_str_len=int(dtype_len),
            shape_len=int(shape_len),
            slot_table_offset=int(slot_table_offset),
            payload_offset=int(payload_offset),
            metadata_dtype=metadata_dtype,
            metadata_nbytes=metadata_nbytes,
            metadata_dtype_str_len=metadata_dtype_len,
            metadata_offset=int(metadata_offset),
        )
        return cls(shm, layout=layout)

    def _read_last_hint(self) -> tuple[int | None, int | None]:
        if self._layout.layout_version < 2:
            return None, None
        last_seq = struct.unpack_from("<Q", self._buf, HEADER_LAST_SEQ_OFFSET)[0]
        last_slot = struct.unpack_from("<Q", self._buf, HEADER_LAST_SLOT_OFFSET)[0]
        return int(last_seq), int(last_slot)

    def _read_stable_slot(
        self,
        slot: int,
        *,
        expected_seq: int,
    ) -> dict[str, Any] | None:
        slot_offset = self._layout.slot_table_offset + slot * SLOT_ENTRY_SIZE
        seq_begin = struct.unpack_from("<Q", self._buf, slot_offset)[0]
        seq_end = struct.unpack_from(
            "<Q", self._buf, slot_offset + SEQ_END_OFFSET
        )[0]
        if seq_begin == 0 or seq_begin != seq_end or seq_end != expected_seq:
            return None

        t0_mono_ns, t0_wall_ns, _shot = struct.unpack_from(
            "<QQQ", self._buf, slot_offset + 8
        )
        payload_start = self._layout.payload_offset + slot * self._layout.payload_nbytes
        payload_end = payload_start + self._layout.payload_nbytes
        payload = bytes(self._buf[payload_start:payload_end])
        metadata_payload: bytes | None = None
        if self._layout.metadata_dtype is not None:
            metadata_start = (
                self._layout.metadata_offset + slot * self._layout.metadata_nbytes
            )
            metadata_end = metadata_start + self._layout.metadata_nbytes
            metadata_payload = bytes(self._buf[metadata_start:metadata_end])

        seq_begin_after = struct.unpack_from("<Q", self._buf, slot_offset)[0]
        seq_end_after = struct.unpack_from(
            "<Q", self._buf, slot_offset + SEQ_END_OFFSET
        )[0]
        if (
            seq_begin_after != expected_seq
            or seq_end_after != expected_seq
            or seq_begin_after != seq_end_after
        ):
            return None
        event = {
            "seq": int(expected_seq),
            "t0_mono_ns": int(t0_mono_ns),
            "t0_wall_ns": int(t0_wall_ns),
            "payload": payload,
        }
        if metadata_payload is not None:
            event["metadata"] = metadata_payload
        return event

    def read_event(self, seq_target: int) -> dict[str, Any] | None:
        def try_slot(slot: int) -> dict[str, Any] | None:
            return self._read_stable_slot(slot, expected_seq=seq_target)

        last_seq_hint, last_slot_hint = self._read_last_hint()
        if last_slot_hint is not None and 0 <= last_slot_hint < self._layout.slot_count:
            hit = try_slot(int(last_slot_hint))
            if hit is not None:
                return hit

            window = 32
            for delta in range(1, window + 1):
                hit = try_slot((int(last_slot_hint) + delta) % self._layout.slot_count)
                if hit is not None:
                    return hit
                hit = try_slot((int(last_slot_hint) - delta) % self._layout.slot_count)
                if hit is not None:
                    return hit

        for slot in range(self._layout.slot_count):
            hit = try_slot(slot)
            if hit is not None:
                return hit

        return None

    def read_events(self, last_seen_seq: int) -> list[dict[str, Any]]:
        entries: list[tuple[int, int]] = []
        slot_indices: list[int]
        last_seq_hint, last_slot_hint = self._read_last_hint()
        if last_slot_hint is not None and 0 <= last_slot_hint < self._layout.slot_count:
            slot_indices = [
                (last_slot_hint + i) % self._layout.slot_count
                for i in range(self._layout.slot_count)
            ]
        else:
            slot_indices = list(range(self._layout.slot_count))

        for slot in slot_indices:
            slot_offset = self._layout.slot_table_offset + slot * SLOT_ENTRY_SIZE
            seq_begin = struct.unpack_from("<Q", self._buf, slot_offset)[0]
            seq_end = struct.unpack_from("<Q", self._buf, slot_offset + SEQ_END_OFFSET)[
                0
            ]
            if seq_begin == 0 or seq_begin != seq_end or seq_end <= last_seen_seq:
                continue
            entries.append((int(seq_end), slot))

        entries.sort(key=lambda item: item[0])
        out: list[dict[str, Any]] = []
        for seq, slot in entries:
            event = self._read_stable_slot(slot, expected_seq=seq)
            if event is not None:
                out.append(event)
        return out

    def close(self) -> None:
        self._shm.close()
