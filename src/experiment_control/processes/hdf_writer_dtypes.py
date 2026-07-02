from __future__ import annotations

import importlib
from typing import Any

import numpy as np

# h5py ships without type stubs (no py.typed marker); a plain `import h5py`
# triggers mypy's `import-untyped` error. Loading via importlib keeps mypy
# clean here without needing a per-line `# type: ignore`. The sibling
# `hdf_writer.py` accepts the baseline error via a direct import.
h5py = importlib.import_module("h5py")

DTYPE_MAP: dict[str, np.dtype[Any]] = {
    "float64": np.dtype("float64"),
    "float32": np.dtype("float32"),
    "int64": np.dtype("int64"),
    "int32": np.dtype("int32"),
    "uint64": np.dtype("uint64"),
    "uint32": np.dtype("uint32"),
    "bool": np.dtype("bool"),
}
DEFAULT_NUMERIC_COMPRESSION = "lzf"
DEFAULT_NUMERIC_SHUFFLE = True
DEFAULT_TELEMETRY_CHUNK_ROWS = 64

# Fixed byte length for a bare ``str`` telemetry dtype (per-signal override via
# ``str:N``). Fixed-length strings keep the compound dataset filterable, so the
# numeric shuffle+compression still apply to string-bearing device compounds
# (unlike variable-length strings, which HDF5 rejects the shuffle filter on).
DEFAULT_STR_LEN = 32


def str_length_for(dtype_str: str) -> int | None:
    """Return the fixed byte length for a string telemetry dtype, else None.

    Accepts ``"str"`` (-> :data:`DEFAULT_STR_LEN`) and ``"str:N"`` (-> N).
    Returns None for any non-string dtype so callers fall through to
    ``DTYPE_MAP``. UTF-8 values longer than N bytes are truncated at write time.
    """
    s = str(dtype_str).strip()
    if s == "str":
        return DEFAULT_STR_LEN
    if s.startswith("str:"):
        try:
            n = int(s[4:])
        except ValueError:
            raise ValueError(
                f"Invalid string dtype {dtype_str!r} (expected 'str' or 'str:N')"
            ) from None
        if n <= 0:
            raise ValueError(f"String dtype length must be positive: {dtype_str!r}")
        return n
    return None


def _event_dtype() -> np.dtype[Any]:
    str_dt = h5py.string_dtype("utf-8")
    return np.dtype(
        [
            ("t_wall", np.float64),
            ("t_mono", np.float64),
            ("kind", str_dt),
            ("severity", str_dt),
            ("device_id", str_dt),
            ("action", str_dt),
            ("params_json", str_dt),
            ("ok", np.bool_),
            ("error", str_dt),
            ("result_json", str_dt),
            ("topic", str_dt),
            ("message", str_dt),
            ("payload_json", str_dt),
        ]
    )


def _context_table_dtype() -> np.dtype[Any]:
    str_dt = h5py.string_dtype("utf-8")
    return np.dtype(
        [
            ("context_id", np.int64),
            ("ts_wall_ns", np.int64),
            ("ts_mono_ns", np.int64),
            ("fields_json", str_dt),
        ]
    )


def _sequencer_event_dtype() -> np.dtype[Any]:
    str_dt = h5py.string_dtype("utf-8")
    return np.dtype(
        [
            ("t_wall", np.float64),
            ("t_mono", np.float64),
            ("process_id", str_dt),
            ("event", str_dt),
            ("source", str_dt),
            ("ok", np.bool_),
            ("message", str_dt),
            ("payload_json", str_dt),
            ("yaml_snapshot_id", np.int64),
        ]
    )


def _sequencer_yaml_dtype() -> np.dtype[Any]:
    str_dt = h5py.string_dtype("utf-8")
    return np.dtype(
        [
            ("snapshot_id", np.int64),
            ("t_wall", np.float64),
            ("t_mono", np.float64),
            ("process_id", str_dt),
            ("source", str_dt),
            ("text", str_dt),
        ]
    )


def _measurement_note_dtype() -> np.dtype[Any]:
    str_dt = h5py.string_dtype("utf-8")
    return np.dtype(
        [
            ("t_wall", np.float64),
            ("t_mono", np.float64),
            ("author", str_dt),
            ("kind", str_dt),
            ("message", str_dt),
            ("payload_json", str_dt),
        ]
    )
