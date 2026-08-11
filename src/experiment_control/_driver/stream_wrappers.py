from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

from ..types import StreamCall, StreamMeta, StreamOut, StreamResult


class _StreamPublisher(Protocol):
    _device: Any

    def publish_stream(
        self,
        stream: str,
        arr: np.ndarray,
        *,
        metadata: np.ndarray | np.void | None = None,
    ) -> dict[str, Any]: ...

    def publish_stream_batch(
        self,
        stream: str,
        arr: np.ndarray,
        *,
        metadata: np.ndarray | None = None,
    ) -> dict[str, Any]: ...


def _ensure_shot_shape(arr: np.ndarray, out: StreamOut) -> np.ndarray:
    expected_dtype = out.numpy_dtype()
    if arr.dtype != expected_dtype:
        raise ValueError(
            f"Stream {out.stream!r} dtype mismatch: got {arr.dtype}, expected {expected_dtype}"
        )
    if tuple(arr.shape) != tuple(out.shape):
        raise ValueError(
            f"Stream {out.stream!r} shot shape mismatch: got {arr.shape}, expected {out.shape}"
        )
    if not arr.flags["C_CONTIGUOUS"]:
        return np.ascontiguousarray(arr)
    return arr


def _as_single_shot(value: Any, out: StreamOut) -> list[np.ndarray]:
    arr = np.asarray(value)
    return [_ensure_shot_shape(arr, out)]


def _as_record_shots(value: Any, out: StreamOut) -> list[np.ndarray]:
    dtype = out.numpy_dtype()
    arr = np.asarray(value, dtype=dtype)
    if arr.shape == ():
        return [np.asarray(arr, dtype=dtype).reshape(())]
    if arr.ndim != 1:
        raise ValueError(
            f"Record stream {out.stream!r} expected scalar record or 1D record batch, got {arr.shape}"
        )
    return [np.asarray(arr[idx], dtype=dtype).reshape(()) for idx in range(arr.shape[0])]


def _as_batch_shots(value: np.ndarray, out: StreamOut, *, n_batch: int) -> list[np.ndarray]:
    if not (
        value.ndim >= 1
        and value.shape[0] == n_batch
        and tuple(value.shape[1:]) == tuple(out.shape)
    ):
        raise ValueError(
            f"Stream {out.stream!r} batched shape mismatch: got {value.shape}, expected ({n_batch}, {out.shape})"
        )
    out_list: list[np.ndarray] = []
    for idx in range(n_batch):
        out_list.append(_ensure_shot_shape(np.asarray(value[idx]), out))
    return out_list


def _as_sequence_shots(
    value: list[Any] | tuple[Any, ...],
    out: StreamOut,
    *,
    expected_len: int,
) -> list[np.ndarray]:
    if len(value) != expected_len:
        raise ValueError(
            f"Stream {out.stream!r} list length {len(value)} != {expected_len}"
        )
    out_list: list[np.ndarray] = []
    for item in value:
        out_list.append(_ensure_shot_shape(np.asarray(item), out))
    return out_list


def _as_shot_list(
    value: Any,
    out: StreamOut,
    *,
    n_batch: int,
    allow_batch: bool,
) -> list[np.ndarray]:
    if out.kind == "records":
        return _as_record_shots(value, out)
    if isinstance(value, np.ndarray):
        if tuple(value.shape) == tuple(out.shape):
            return [_ensure_shot_shape(value, out)]
        if allow_batch:
            return _as_batch_shots(value, out, n_batch=n_batch)
        raise ValueError(
            f"Stream {out.stream!r} shot shape mismatch: got {value.shape}, expected {out.shape}"
        )
    if isinstance(value, (list, tuple)):
        expected_len = n_batch if allow_batch else 1
        return _as_sequence_shots(value, out, expected_len=expected_len)
    if n_batch == 1:
        return _as_single_shot(value, out)
    raise TypeError(
        f"Stream {out.stream!r} expected ndarray or list/tuple for n_batch={n_batch}"
    )


def _resolve_stream_callable(runner: _StreamPublisher, stream_call: StreamCall) -> Callable[..., Any]:
    func = getattr(runner._device, stream_call.method, None)
    if func is None or not callable(func):
        raise NotImplementedError(f"Stream method {stream_call.method!r} not found")
    return func


def _unwrap_stream_result(ret: Any, stream_call: StreamCall) -> tuple[Any, dict[str, Any]]:
    declared = list(stream_call.meta or [])
    if not declared:
        if isinstance(ret, StreamResult) and ret.meta:
            raise ValueError("StreamResult metadata was returned but none is declared")
        return (ret.data if isinstance(ret, StreamResult) else ret), {}
    if not isinstance(ret, StreamResult):
        raise TypeError("Stream call with declared metadata must return StreamResult")
    expected = {item.name for item in declared}
    actual = set(ret.meta)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"Stream metadata keys mismatch: missing={missing}, extra={extra}")
    return ret.data, dict(ret.meta)


def _metadata_array(
    declared: list[StreamMeta],
    values: dict[str, Any],
    *,
    count: int,
) -> np.ndarray | None:
    if not declared:
        return None
    dtype = np.dtype([(item.name, np.dtype(item.dtype)) for item in declared])
    out = np.empty(count, dtype=dtype)
    for item in declared:
        value = np.asarray(values[item.name])
        if value.shape == () and count == 1:
            value = value.reshape(1)
        if value.shape != (count,):
            raise ValueError(
                f"Stream metadata {item.name!r} shape mismatch: "
                f"got {value.shape}, expected ({count},)"
            )
        expected_dtype = np.dtype(item.dtype)
        if value.dtype != expected_dtype:
            raise ValueError(
                f"Stream metadata {item.name!r} dtype mismatch: "
                f"got {value.dtype}, expected {expected_dtype}"
            )
        out[item.name] = value
    return out


def _invoke_stream_callable(
    *,
    func: Callable[..., Any],
    stream_call: StreamCall,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[Any, int, bool]:
    call_kwargs = dict(stream_call.kwargs or {})
    call_kwargs.update(kwargs)
    n_batch_provided = "n_batch" in call_kwargs
    n_batch = int(call_kwargs.pop("n_batch", 1))
    if n_batch < 1:
        raise ValueError("n_batch must be >= 1")
    if not n_batch_provided:
        return func(*args, **call_kwargs), n_batch, False
    try:
        return func(*args, n_batch=n_batch, **call_kwargs), n_batch, True
    except TypeError as e:
        if "n_batch" in str(e) or "unexpected keyword" in str(e):
            raise TypeError(
                f"Stream method {stream_call.method!r} does not support n_batch"
            ) from e
        raise


def _publish_single_output(
    *,
    runner: _StreamPublisher,
    output: StreamOut,
    ret: Any,
    n_batch: int,
    n_batch_provided: bool,
    metadata: dict[str, Any],
    meta_schema: list[StreamMeta],
) -> list[dict[str, Any]]:
    shots = _as_shot_list(
        ret,
        output,
        n_batch=n_batch,
        allow_batch=n_batch_provided,
    )
    meta_arr = _metadata_array(meta_schema, metadata, count=len(shots))
    if n_batch_provided:
        batch = np.stack(shots, axis=0)
        return [runner.publish_stream_batch(output.stream, batch, metadata=meta_arr)]
    if meta_arr is None:
        return [runner.publish_stream(output.stream, shot) for shot in shots]
    return [
        runner.publish_stream(output.stream, shot, metadata=meta_arr[idx])
        for idx, shot in enumerate(shots)
    ]


def _collect_multi_output_shots(
    *,
    outputs: list[StreamOut],
    ret: Any,
    n_batch: int,
    n_batch_provided: bool,
) -> dict[str, list[np.ndarray]]:
    if any(out.kind == "records" for out in outputs):
        raise ValueError(
            "Record streams currently require a single output per stream call"
        )
    if not isinstance(ret, dict):
        raise TypeError(
            "Stream call with multiple outputs must return dict[str, ndarray|list]"
        )
    shot_lists: dict[str, list[np.ndarray]] = {}
    for out in outputs:
        if out.stream not in ret:
            raise KeyError(f"Missing stream output {out.stream!r} in return dict")
        shot_lists[out.stream] = _as_shot_list(
            ret[out.stream],
            out,
            n_batch=n_batch,
            allow_batch=n_batch_provided,
        )
    return shot_lists


def _publish_multi_output(
    *,
    runner: _StreamPublisher,
    outputs: list[StreamOut],
    shot_lists: dict[str, list[np.ndarray]],
    n_batch: int,
    n_batch_provided: bool,
    metadata: dict[str, Any],
    meta_schema: list[StreamMeta],
) -> list[dict[str, Any]]:
    meta_arr = _metadata_array(meta_schema, metadata, count=n_batch)
    if n_batch_provided:
        batch_descs: dict[str, Any] = {}
        for out in outputs:
            batch_descs[out.stream] = runner.publish_stream_batch(
                out.stream,
                np.stack(shot_lists[out.stream], axis=0),
                metadata=meta_arr,
            )
        return [batch_descs]
    results: list[dict[str, Any]] = []
    for i in range(n_batch):
        descs: dict[str, Any] = {}
        for out in outputs:
            if meta_arr is None:
                descs[out.stream] = runner.publish_stream(
                    out.stream, shot_lists[out.stream][i]
                )
            else:
                descs[out.stream] = runner.publish_stream(
                    out.stream, shot_lists[out.stream][i], metadata=meta_arr[i]
                )
        results.append(descs)
    return results


def build_stream_wrapper(
    *,
    runner: _StreamPublisher,
    stream_call: StreamCall,
) -> Callable[..., Any]:
    outputs = list(stream_call.outputs or [])

    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        effective_kwargs = dict(stream_call.kwargs or {})
        effective_kwargs.update(kwargs)
        if "n_batch" in effective_kwargs:
            requested_batch = int(effective_kwargs["n_batch"])
            max_batch = min(out.ring_slots for out in outputs)
            if requested_batch > max_batch:
                raise ValueError(
                    f"n_batch={requested_batch} exceeds stream ring capacity {max_batch}"
                )
        func = _resolve_stream_callable(runner, stream_call)
        ret, n_batch, n_batch_provided = _invoke_stream_callable(
            func=func,
            stream_call=stream_call,
            args=args,
            kwargs=kwargs,
        )
        ret, metadata = _unwrap_stream_result(ret, stream_call)
        meta_schema = list(stream_call.meta or [])
        if len(outputs) == 1:
            return _publish_single_output(
                runner=runner,
                output=outputs[0],
                ret=ret,
                n_batch=n_batch,
                n_batch_provided=n_batch_provided,
                metadata=metadata,
                meta_schema=meta_schema,
            )
        shot_lists = _collect_multi_output_shots(
            outputs=outputs,
            ret=ret,
            n_batch=n_batch,
            n_batch_provided=n_batch_provided,
        )
        return _publish_multi_output(
            runner=runner,
            outputs=outputs,
            shot_lists=shot_lists,
            n_batch=n_batch,
            n_batch_provided=n_batch_provided,
            metadata=metadata,
            meta_schema=meta_schema,
        )

    return _wrapper
