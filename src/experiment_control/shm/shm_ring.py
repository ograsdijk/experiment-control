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
    ) -> "ShmRingWriter":
        dtype_obj = np.dtype(dtype)
        payload_nbytes = int(dtype_obj.itemsize * prod(shape))
        dtype_bytes = _dtype_to_header_text(dtype_obj).encode("utf-8")
        shape_len = len(shape)
        dtype_str_len = len(dtype_bytes)

        slot_table_offset = HEADER_SIZE + dtype_str_len + shape_len * 4
        payload_offset = slot_table_offset + slot_count * SLOT_ENTRY_SIZE
        total_bytes = payload_offset + slot_count * payload_nbytes

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
                0,
                0,
            )
        buf[HEADER_SIZE : HEADER_SIZE + dtype_str_len] = dtype_bytes
        shape_offset = HEADER_SIZE + dtype_str_len
        for i, dim in enumerate(shape):
            struct.pack_into("<i", buf, shape_offset + i * 4, int(dim))

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
        )
        return cls(shm, layout=layout)

    def write(
        self,
        arr: np.ndarray,
        *,
        t0_mono_ns: int,
        t0_wall_ns: int,
    ) -> int:
        if arr.dtype != self._layout.dtype:
            raise ValueError(
                f"dtype mismatch: got {arr.dtype}, expected {self._layout.dtype}"
            )
        if tuple(arr.shape) != self._layout.shape:
            raise ValueError(
                f"shape mismatch: got {arr.shape}, expected {self._layout.shape}"
            )

        slot = self._next_slot
        seq = self._next_seq + 1

        slot_offset = self._layout.slot_table_offset + slot * SLOT_ENTRY_SIZE
        # Invalidate slot before writing payload to avoid torn reads.
        struct.pack_into("<Q", self._buf, slot_offset, 0)
        struct.pack_into("<Q", self._buf, slot_offset + SEQ_END_OFFSET, 0)

        payload_start = self._layout.payload_offset + slot * self._layout.payload_nbytes
        payload_end = payload_start + self._layout.payload_nbytes
        self._buf[payload_start:payload_end] = arr.tobytes(order="C")

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

        slot_table_offset = HEADER_SIZE + int(dtype_len) + int(shape_len) * 4
        payload_offset = slot_table_offset + int(slot_count) * SLOT_ENTRY_SIZE

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
        )
        return cls(shm, layout=layout)

    def _read_last_hint(self) -> tuple[int | None, int | None]:
        if self._layout.layout_version < 2:
            return None, None
        last_seq = struct.unpack_from("<Q", self._buf, HEADER_LAST_SEQ_OFFSET)[0]
        last_slot = struct.unpack_from("<Q", self._buf, HEADER_LAST_SLOT_OFFSET)[0]
        return int(last_seq), int(last_slot)

    def read_event(self, seq_target: int) -> dict[str, Any] | None:
        def try_slot(slot: int) -> dict[str, Any] | None:
            slot_offset = self._layout.slot_table_offset + slot * SLOT_ENTRY_SIZE
            seq_begin = struct.unpack_from("<Q", self._buf, slot_offset)[0]
            if seq_begin == 0:
                return None
            seq_end = struct.unpack_from("<Q", self._buf, slot_offset + SEQ_END_OFFSET)[
                0
            ]
            if seq_begin != seq_end or seq_end != seq_target:
                return None
            t0_mono_ns, t0_wall_ns, _shot = struct.unpack_from(
                "<QQQ", self._buf, slot_offset + 8
            )
            payload_start = (
                self._layout.payload_offset + slot * self._layout.payload_nbytes
            )
            payload_end = payload_start + self._layout.payload_nbytes
            payload = bytes(self._buf[payload_start:payload_end])
            return {
                "seq": int(seq_end),
                "t0_mono_ns": int(t0_mono_ns),
                "t0_wall_ns": int(t0_wall_ns),
                "payload": payload,
            }

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
            slot_offset = self._layout.slot_table_offset + slot * SLOT_ENTRY_SIZE
            t0_mono_ns, t0_wall_ns, shot = struct.unpack_from(
                "<QQQ", self._buf, slot_offset + 8
            )
            payload_start = (
                self._layout.payload_offset + slot * self._layout.payload_nbytes
            )
            payload_end = payload_start + self._layout.payload_nbytes
            payload = bytes(self._buf[payload_start:payload_end])
            out.append(
                {
                    "seq": int(seq),
                    "t0_mono_ns": int(t0_mono_ns),
                    "t0_wall_ns": int(t0_wall_ns),
                    "payload": payload,
                }
            )
        return out

    def close(self) -> None:
        self._shm.close()
